"""Conferência manual (celular): kits em montagem item a item, só leitura."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import uuid

import pytest

os.environ["DB_PATH"] = ":memory:"
os.environ.setdefault("SECRET_KEY", "chave-de-teste-nao-usar-em-producao")

from database import init_db, db
import app.conferencia_manual as conf


@pytest.fixture
def kit():
    """Modelo com 3 itens (antena x1, cabo x4, display x1); bipagem em andamento
    com a antena, 2 cabos e um item que não é do modelo."""
    init_db()
    s = uuid.uuid4().hex[:6]
    with db() as conn:
        conn.execute("INSERT INTO users (username, nome, password_hash, admin, ativo) VALUES (?, 'Op Conf', 'x', 0, 1)", (f"oc-{s}",))
        uid = conn.execute("SELECT id FROM users WHERE username = ?", (f"oc-{s}",)).fetchone()["id"]
        tipos = {}
        for nome in ("Antena", "Cabo", "Display", "Parafuso"):
            conn.execute("INSERT INTO item_tipo (nome, criado_em) VALUES (?, '2026-01-01')", (f"{nome} {s}",))
            tipos[nome] = conn.execute("SELECT id FROM item_tipo WHERE nome = ?", (f"{nome} {s}",)).fetchone()["id"]
        conn.execute("INSERT INTO kit_template (nome, cliente, versao, ativo, tipo) VALUES (?, 'CliConf', 1, 1, 'kit')", (f"Kit {s}",))
        tid = conn.execute("SELECT id FROM kit_template WHERE nome = ?", (f"Kit {s}",)).fetchone()["id"]
        for nome, q in (("Antena", 1), ("Cabo", 4), ("Display", 1)):
            conn.execute("INSERT INTO kit_template_items (kit_template_id, item_tipo_id, quantidade_exigida, obrigatorio) "
                         "VALUES (?, ?, ?, 1)", (tid, tipos[nome], q))
        conn.execute("INSERT INTO veiculos (numero, cliente, garagem, ativo, criado_em) VALUES (?, 'CliConf', 'G', 1, '2026-01-01')",
                     (f"31001-5{s[:4]}",))
        vid = conn.execute("SELECT id FROM veiculos WHERE numero = ?", (f"31001-5{s[:4]}",)).fetchone()["id"]
        conn.execute("INSERT INTO scan_session (kit_template_id, kit_template_versao, operador_id, iniciado_em, veiculo_id, status) "
                     "VALUES (?, 1, ?, '2026-11-04 08:00:00', ?, 'em_andamento')", (tid, uid, vid))
        sid = conn.execute("SELECT id FROM scan_session WHERE veiculo_id = ?", (vid,)).fetchone()["id"]
        for nome, cod, q in (("Antena", f"ANT-{s}", 1), ("Cabo", f"CAB-{s}", 2), ("Parafuso", f"PAR-{s}", 1)):
            conn.execute("INSERT INTO scan_session_items (sessao_id, codigo_barra, item_tipo_id, status, bipado_em, operador_id, quantidade) "
                         "VALUES (?, ?, ?, 'completo', '2026-11-04 08:10:00', ?, ?)", (sid, cod, tipos[nome], uid, q))
    yield {"sid": sid, "s": s, "veiculo": f"31001-5{s[:4]}"}
    with db() as conn:
        conn.execute("DELETE FROM scan_session_items WHERE sessao_id = ?", (sid,))
        conn.execute("DELETE FROM scan_session WHERE id = ?", (sid,))
        conn.execute("DELETE FROM veiculos WHERE id = ?", (vid,))
        conn.execute("DELETE FROM kit_template_items WHERE kit_template_id = ?", (tid,))
        conn.execute("DELETE FROM kit_template WHERE id = ?", (tid,))
        conn.execute("DELETE FROM item_tipo WHERE nome LIKE ?", (f"% {s}",))
        conn.execute("DELETE FROM users WHERE id = ?", (uid,))


def test_lista_o_que_falta_primeiro(kit):
    k = next(x for x in conf.listar() if x["id"] == kit["sid"])
    assert k["veiculo"] == kit["veiculo"] and k["cliente"] == "CliConf"
    estados = [(i["descricao"].split()[0], i["estado"], i["bipado"], i["exigido"]) for i in k["itens"]]
    assert estados == [("Display", "falta", 0, 1), ("Cabo", "parcial", 2, 4),
                       ("Antena", "completo", 1, 1), ("Parafuso", "fora", 1, 0)]
    assert (k["faltam"], k["completos"], k["total"]) == (2, 1, 3)
    cabo = next(i for i in k["itens"] if i["descricao"].startswith("Cabo"))
    assert cabo["faltam"] == 2


def test_tela_no_celular_nao_grava_nada(kit):
    from fastapi.testclient import TestClient
    import main
    from app import usuarios
    nome = f"op_cm_{kit['s']}"
    usuarios.criar("Op CM", nome, "Teste#Conf2026", False)
    with db() as conn:
        antes = conn.execute("SELECT COUNT(*) FROM scan_session_items").fetchone()[0]
    try:
        celular = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Mobile/15E148"}
        with TestClient(main.app) as c:
            c.post("/login", data={"username": nome, "password": "Teste#Conf2026"})
            r = c.get("/mobile/conferencia", headers=celular, follow_redirects=False)
            assert r.status_code == 200                      # o portão do celular deixa passar
            assert kit["veiculo"] in r.text and "falta 2" in r.text and "bipado 2 de 4" in r.text
            assert "Conferência manual" in c.get("/mobile", headers=celular).text
        with db() as conn:
            assert conn.execute("SELECT COUNT(*) FROM scan_session_items").fetchone()[0] == antes
    finally:
        with db() as conn:
            conn.execute("DELETE FROM users WHERE username = ?", (nome,))
