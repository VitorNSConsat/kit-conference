"""Observações adicionadas a um pacote de sobressalentes: quem, quando, a que se referem."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import uuid

import pytest

os.environ["DB_PATH"] = ":memory:"
os.environ.setdefault("SECRET_KEY", "chave-de-teste-nao-usar-em-producao")

from database import init_db, db
import app.sobressalentes as sob


@pytest.fixture
def pacote():
    init_db()
    s = uuid.uuid4().hex[:6]
    with db() as conn:
        conn.execute("INSERT INTO users (username, nome, password_hash, admin, ativo) VALUES (?, 'Ana Obs', 'x', 1, 1)", (f"o-{s}",))
        uid = conn.execute("SELECT id FROM users WHERE username = ?", (f"o-{s}",)).fetchone()[0]
        conn.execute("INSERT INTO item_tipo (nome, criado_em) VALUES (?, '2026-01-01')", (f"Fusivel {s}",))
        tid = conn.execute("SELECT id FROM item_tipo WHERE nome = ?", (f"Fusivel {s}",)).fetchone()[0]
        conn.execute("INSERT INTO estoque (item_tipo_id, codigo_barra, quantidade_atual, quantidade_minima, criado_em) VALUES (?, ?, 10, 1, '2026-01-01')", (tid, f"F-{s}"))
        eid = conn.execute("SELECT id FROM estoque WHERE item_tipo_id = ?", (tid,)).fetchone()[0]
    pid = sob.criar([{"estoque_id": eid, "quantidade": 2, "observacao": "original"}], f"Cli{s}", uid, nome="Caixa")
    yield {"s": s, "uid": uid, "eid": eid, "tid": tid, "pid": pid}
    with db() as conn:
        conn.execute("DELETE FROM auditoria WHERE user_id = ?", (uid,))
        conn.execute("DELETE FROM sobressalente_pacote WHERE id = ?", (pid,))
        conn.execute("DELETE FROM estoque_movimentos WHERE estoque_id = ?", (eid,))
        conn.execute("DELETE FROM estoque WHERE id = ?", (eid,))
        conn.execute("DELETE FROM item_tipo WHERE id = ?", (tid,))
        conn.execute("DELETE FROM users WHERE id = ?", (uid,))


def test_adiciona_com_autor_e_alvo_sem_mexer_na_original(pacote):
    assert sob.adicionar_observacao(pacote["pid"], "  no pacote  ", pacote["uid"]) == "Pacote inteiro"
    assert sob.adicionar_observacao(pacote["pid"], "no item", pacote["uid"], pacote["eid"]) == f"Fusivel {pacote['s']}"
    p = sob.buscar(pacote["pid"])
    assert [(o["conteudo"], o["alvo_texto"], o["usuario_nome"]) for o in p["observacoes"]] == [
        ("no item", f"Fusivel {pacote['s']}", "Ana Obs"), ("no pacote", "Pacote inteiro", "Ana Obs")]
    assert all(o["criado_em"] for o in p["observacoes"])
    assert p["itens"][0]["observacao"] == "original"
    assert "Observação adicionada" in [e["tipo_texto"] for e in p["eventos"]]
    envio = next(e for e in sob.envios_relatorio() if e["pacote_id"] == pacote["pid"])
    assert [o["conteudo"] for o in envio["adicionadas"]] == ["no item", "no pacote"]


@pytest.mark.parametrize("texto,alvo,msg", [("   ", None, "Escreva"), ("x" * 1001, None, "até 1000"), ("ok", 99999999, "não faz parte")])
def test_recusa_invalida(pacote, texto, alvo, msg):
    with pytest.raises(ValueError, match=msg):
        sob.adicionar_observacao(pacote["pid"], texto, pacote["uid"], alvo)
    assert sob.buscar(pacote["pid"])["observacoes"] == []


def test_rota_da_janela_e_da_pagina_e_auditoria(pacote):
    from fastapi.testclient import TestClient
    import main
    from app import usuarios
    import app.auditoria as aud
    nome = f"adm_ob_{pacote['s']}"
    usuarios.criar("Adm Ob", nome, "Teste#Observ26", True)
    try:
        with TestClient(main.app) as c:
            c.post("/login", data={"username": nome, "password": "Teste#Observ26"})
            url = f"/sobressalente/{pacote['pid']}/observacao"
            j = c.post(url, data={"texto": "pela janela", "estoque_id": ""}, headers={"X-Requested-With": "fetch"})
            assert j.status_code == 200 and "pela janela" in j.text and "Adm Ob" in j.text and "<html" not in j.text
            e = c.post(url, data={"texto": " "}, headers={"X-Requested-With": "fetch"})
            assert "Escreva a observação" in e.text
            r = c.post(url, data={"texto": "pela pagina", "estoque_id": str(pacote["eid"])}, follow_redirects=False)
            assert r.status_code == 302 and r.headers["location"].endswith("?ok=observacao")
            assert "pela pagina" in c.get(f"/sobressalente/{pacote['pid']}").text
            assert "pela pagina" in c.get("/reports/sobressalentes", params={"busca": "pela pagina"}).text
        with db() as conn:
            uid2 = conn.execute("SELECT id FROM users WHERE username = ?", (nome,)).fetchone()[0]
            reg = dict(conn.execute("SELECT * FROM auditoria WHERE user_id = ? AND detalhe LIKE '%pela pagina%'", (uid2,)).fetchone())
        r2 = aud.interpretar([reg])[0]
        assert r2["titulo"] == "Observação adicionada ao pacote" and r2["obs"] is None
    finally:
        with db() as conn:
            conn.execute("DELETE FROM auditoria WHERE user_nome = 'Adm Ob'")
            conn.execute("UPDATE sobressalente_pacote_evento SET usuario_id = NULL WHERE pacote_id = ?", (pacote["pid"],))
            conn.execute("DELETE FROM users WHERE username = ?", (nome,))
