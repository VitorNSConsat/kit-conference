"""Relatório de mudanças de patrimônio (montado do histórico das bipagens) e
relatório de sobressalentes agrupado por envio (SOB-0001 com os itens)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import uuid

import pytest

os.environ["DB_PATH"] = ":memory:"
os.environ.setdefault("SECRET_KEY", "chave-de-teste-nao-usar-em-producao")

from database import init_db, db
import app.items as items_mod
import app.relatorio_patrimonio as rel


@pytest.fixture
def cenario():
    """Dois veículos (A e B), cada um com um kit finalizado; no kit de A, um
    patrimônio bipado na montagem."""
    init_db()
    s = uuid.uuid4().hex[:6]
    with db() as conn:
        conn.execute("INSERT INTO users (username, nome, password_hash, admin, ativo) "
                     "VALUES (?, 'Op Rel', 'x', 1, 1)", (f"oprel-{s}",))
        uid = conn.execute("SELECT id FROM users WHERE username = ?", (f"oprel-{s}",)).fetchone()["id"]
        conn.execute("INSERT INTO item_tipo (nome, controle_externo, criado_em) "
                     "VALUES (?, 1, '2026-01-01')", (f"Antena {s}",))
        tipo = conn.execute("SELECT id FROM item_tipo WHERE nome = ?", (f"Antena {s}",)).fetchone()["id"]
        conn.execute("INSERT INTO kit_template (nome, cliente, versao) VALUES (?, 'CliRel', 1)", (f"Kit {s}",))
        tid = conn.execute("SELECT id FROM kit_template WHERE nome = ?", (f"Kit {s}",)).fetchone()["id"]
        conn.execute("INSERT INTO kit_template_items (kit_template_id, item_tipo_id, quantidade_exigida) "
                     "VALUES (?, ?, 1)", (tid, tipo))
        veic, sess = {}, {}
        for lado in ("A", "B"):
            numero = f"{lado}-{s}"
            conn.execute("INSERT INTO veiculos (numero, cliente, garagem, ativo, criado_em) "
                         "VALUES (?, 'CliRel', 'G', 1, '2026-01-01')", (numero,))
            vid = conn.execute("SELECT id FROM veiculos WHERE numero = ?", (numero,)).fetchone()["id"]
            conn.execute("INSERT INTO scan_session (kit_template_id, kit_template_versao, operador_id, "
                         "iniciado_em, veiculo_id, status) VALUES (?, 1, ?, '2026-09-01 08:00:00', ?, 'finalizado')",
                         (tid, uid, vid))
            sid = conn.execute("SELECT id FROM scan_session WHERE veiculo_id = ?", (vid,)).fetchone()["id"]
            conn.execute("INSERT INTO kit_record (kit_id, sessao_id, kit_template_id, kit_template_versao, "
                         "operador_id, veiculo_id, veiculo, finalizado_em, status_producao) "
                         "VALUES (?, ?, ?, 1, ?, ?, ?, '2026-09-01 09:00:00', 'transito')",
                         (f"K{lado}-{s}", sid, tid, uid, vid, numero))
            veic[lado], sess[lado] = numero, sid
        conn.execute("INSERT INTO scan_session_items (sessao_id, codigo_barra, item_tipo_id, status, "
                     "bipado_em, operador_id, quantidade) VALUES (?, ?, ?, 'completo', '2026-09-01 08:30:00', ?, 1)",
                     (sess["A"], f"PAT-{s}", tipo, uid))
    yield {"s": s, "uid": uid, "tipo": tipo, "veic": veic, "pat": f"PAT-{s}", "kitA": f"KA-{s}"}
    with db() as conn:
        for lado in ("A", "B"):
            sid = sess[lado]
            conn.execute("DELETE FROM scan_session_items WHERE sessao_id = ?", (sid,))
            conn.execute("DELETE FROM kit_record WHERE sessao_id = ?", (sid,))
            conn.execute("DELETE FROM scan_session WHERE id = ?", (sid,))
        conn.execute("DELETE FROM veiculos WHERE numero LIKE ?", (f"%-{s}",))
        conn.execute("DELETE FROM item_master WHERE codigo_barra LIKE ?", (f"%-{s}%",))
        conn.execute("DELETE FROM kit_template_items WHERE kit_template_id = (SELECT id FROM kit_template WHERE nome = ?)", (f"Kit {s}",))
        conn.execute("DELETE FROM kit_template WHERE nome = ?", (f"Kit {s}",))
        conn.execute("DELETE FROM item_tipo WHERE id = ?", (tipo,))


def _do_cenario(c):
    return rel.listar_mudancas(busca=c["s"])


def test_movido_vira_um_evento_com_origem_destino_e_onde_esta(cenario):
    c = cenario
    items_mod.mover_patrimonio(c["pat"], c["veic"]["B"], "instalado no outro ônibus", c["uid"])
    ev = _do_cenario(c)
    assert len(ev) == 1
    e = ev[0]
    assert e["tipo"] == "movido" and e["saiu_de"] == c["veic"]["A"] and e["entrou_em"] == c["veic"]["B"]
    assert e["motivo"] == "instalado no outro ônibus" and e["por"] == "Op Rel"
    assert e["agora_veiculo"] == c["veic"]["B"] and e["agora_etapa"] == "Em trânsito"


def test_retirada_reposicao_e_correcao(cenario):
    c = cenario
    items_mod.retirar_do_kit(c["pat"], "peça queimada na instalação")
    items_mod.atribuir_patrimonio(f"NOVO-{c['s']}", c["kitA"], c["tipo"], "reposição da queimada", "", c["uid"])
    items_mod.corrigir_patrimonio(f"NOVO-{c['s']}", f"NOVO2-{c['s']}", None, "digitado errado", c["uid"])
    ev = {e["tipo"]: e for e in _do_cenario(c)}
    assert set(ev) == {"retirado", "reposicao", "correcao"}
    r = ev["retirado"]
    assert r["saiu_de"] == c["veic"]["A"] and r["agora_texto"] == "Fora de kit (retirado)"
    assert r["motivo"] == "peça queimada na instalação"
    assert ev["reposicao"]["entrou_em"] == c["veic"]["A"] and ev["reposicao"]["patrimonio"] == f"NOVO2-{c['s']}"
    assert ev["correcao"]["veiculo"] == c["veic"]["A"] and ev["correcao"]["motivo"] == "digitado errado"
    assert f"NOVO-{c['s']} para NOVO2-{c['s']}" in ev["correcao"]["detalhe"]
    # Filtro por tipo
    assert [e["tipo"] for e in rel.listar_mudancas(tipos=["retirado"], busca=c["s"])] == ["retirado"]


def test_pagina_e_excel_do_relatorio(cenario):
    from fastapi.testclient import TestClient
    import main
    import io, openpyxl
    from app import usuarios
    c = cenario
    items_mod.mover_patrimonio(c["pat"], c["veic"]["B"], "instalado no outro ônibus", c["uid"])
    nome = f"adm_rel_{c['s']}"
    usuarios.criar("Adm Rel", nome, "Teste#Rel2026", True)
    try:
        with TestClient(main.app) as cli:
            cli.post("/login", data={"username": nome, "password": "Teste#Rel2026"})
            h = cli.get("/reports/patrimonio", params={"busca": c["s"]}).text
            assert c["pat"] in h and "Movido entre veículos" in h
            x = cli.get("/reports/patrimonio/export", params={"busca": c["s"]})
            ws = openpyxl.load_workbook(io.BytesIO(x.content)).active
            linha = [cel.value for cel in ws[2]]
            assert linha[2] == c["pat"] and linha[5] == c["veic"]["A"] and linha[6] == c["veic"]["B"]
    finally:
        with db() as conn:
            conn.execute("DELETE FROM users WHERE username = ?", (nome,))
