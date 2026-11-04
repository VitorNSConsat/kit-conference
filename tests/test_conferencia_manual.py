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


def _finalizar(sid, kit_id):
    with db() as conn:
        s = conn.execute("SELECT * FROM scan_session WHERE id = ?", (sid,)).fetchone()
        conn.execute("UPDATE scan_session SET status = 'finalizado' WHERE id = ?", (sid,))
        conn.execute("INSERT INTO kit_record (kit_id, sessao_id, kit_template_id, kit_template_versao, operador_id, "
                     "veiculo_id, veiculo, finalizado_em) VALUES (?, ?, ?, 1, ?, ?, '', '2026-11-04 12:00:00')",
                     (kit_id, sid, s["kit_template_id"], s["operador_id"], s["veiculo_id"]))


def _conferidos(kit_id):
    with db() as conn:
        return {r[0] for r in conn.execute("SELECT item_tipo_id FROM kit_verificacao_itens WHERE kit_id = ?", (kit_id,))}


@pytest.mark.parametrize("acao", ["aplicar", "ignorar"])
def test_pre_conferencia_registrada_e_aplicada_no_kit_pronto(kit, acao):
    from fastapi.testclient import TestClient
    import main
    from app import usuarios
    nome, kit_id = f"op_pc_{kit['s']}_{acao[:2]}", f"KPC-{kit['s']}-{acao[:2]}"
    usuarios.criar("Op PC", nome, "Teste#PreConf26", False)
    k = next(x for x in conf.listar() if x["id"] == kit["sid"])
    ids = {i["descricao"].split()[0]: i["item_tipo_id"] for i in k["itens"]}
    try:
        with TestClient(main.app) as c:
            c.post("/login", data={"username": nome, "password": "Teste#PreConf26"})
            # Celular: pré-confere Antena e Cabo; o Display (que falta) fica de fora.
            # O Parafuso (fora do modelo) é ignorado mesmo se vier na lista.
            r = c.post(f"/mobile/conferencia/{kit['sid']}/registrar",
                       data={"item_tipo_id": [ids["Antena"], ids["Cabo"], ids["Parafuso"]]})
            assert r.json()["ok"] and r.json()["itens"] == 2
            assert _conferidos(kit_id) == set()                       # nada na conferência oficial
            # A peça que faltava chega e é bipada; aí o kit fecha.
            with db() as conn:
                conn.execute("INSERT INTO scan_session_items (sessao_id, codigo_barra, item_tipo_id, status, bipado_em, quantidade) "
                             "VALUES (?, ?, ?, 'completo', '2026-11-04 11:00:00', 1)", (kit["sid"], f"DSP-{kit['s']}", ids["Display"]))
            _finalizar(kit["sid"], kit_id)
            h = c.get(f"/kit/{kit_id}").text
            assert "Pré-conferência encontrada" in h and "Display" in h.split("Não pré-conferido")[1][:200]
            c.post(f"/kit/{kit_id}/pre-conferencia", data={"acao": acao})
            if acao == "aplicar":
                assert _conferidos(kit_id) == {ids["Antena"], ids["Cabo"]}   # Display segue pendente
            else:
                assert _conferidos(kit_id) == set()
            assert "Pré-conferência encontrada" not in c.get(f"/kit/{kit_id}").text   # decidido, some
            # Kit que não está mais em montagem não aceita nova pré-conferência.
            assert c.post(f"/mobile/conferencia/{kit['sid']}/registrar",
                          data={"item_tipo_id": [ids["Antena"]]}).status_code == 400
    finally:
        with db() as conn:
            conn.execute("DELETE FROM kit_verificacao_itens WHERE kit_id = ?", (kit_id,))
            conn.execute("DELETE FROM kit_itens_exigidos WHERE kit_id = ?", (kit_id,))
            conn.execute("DELETE FROM kit_record WHERE kit_id = ?", (kit_id,))
            conn.execute("DELETE FROM pre_conferencia WHERE sessao_id = ?", (kit["sid"],))
            conn.execute("DELETE FROM users WHERE username = ?", (nome,))


def test_conjunto_marca_junto_como_na_verificacao(kit):
    """Antena e Cabo no mesmo conjunto (os dois já bipados) andam juntos; o
    Display, do mesmo conjunto mas ainda não bipado, fica de fora. Com o
    conjunto marcado pra conferir item a item, ninguém anda junto."""
    with db() as conn:
        tid = conn.execute("SELECT kit_template_id FROM scan_session WHERE id = ?", (kit["sid"],)).fetchone()[0]
        conn.execute("UPDATE kit_template_items SET componente_codigo = 'CJ1' WHERE kit_template_id = ?", (tid,))
    k = next(x for x in conf.listar() if x["id"] == kit["sid"])
    g = {i["descricao"].split()[0]: i for i in k["itens"]}
    assert g["Antena"]["grupo"] == g["Cabo"]["grupo"] == "CJ1"
    assert g["Antena"]["junto_com"] == [g["Cabo"]["descricao"]]
    assert g["Display"]["grupo"] == ""                        # não chegou: segue sozinho e pendente
    with db() as conn:
        conn.execute("INSERT INTO kit_template_conjuntos (kit_template_id, componente_codigo, verifica_em_conjunto) "
                     "VALUES (?, 'CJ1', 0)", (tid,))
    try:
        k = next(x for x in conf.listar() if x["id"] == kit["sid"])
        assert all(i["grupo"] == "" for i in k["itens"])
    finally:
        with db() as conn:
            conn.execute("DELETE FROM kit_template_conjuntos WHERE kit_template_id = ?", (tid,))


def test_relatorio_pre_check_acompanha_as_tres_situacoes(kit):
    from fastapi.testclient import TestClient
    import main, io, openpyxl
    from app import usuarios
    k = next(x for x in conf.listar() if x["id"] == kit["sid"])
    ids = {i["descricao"].split()[0]: i["item_tipo_id"] for i in k["itens"]}
    kit_id = f"KRP-{kit['s']}"
    with db() as conn:
        uid = conn.execute("SELECT operador_id FROM scan_session WHERE id = ?", (kit["sid"],)).fetchone()[0]
    conf.registrar(kit["sid"], [ids["Antena"], ids["Cabo"]], uid)

    def linha():
        return next(l for l in conf.relatorio(busca=kit["veiculo"]) if l["sessao_id"] == kit["sid"])
    try:
        l = linha()
        assert l["situacao"] == "montagem" and len(l["pre_itens"]) == 2
        assert [n.split()[0] for n in l["nao_pre"]] == ["Display"]
        _finalizar(kit["sid"], kit_id)
        l = linha()
        assert l["situacao"] == "aguardando" and l["decisao_texto"] == "Sem decisão ainda"
        assert {n.split()[0] for n in l["pendentes_conferencia"]} == {"Antena", "Cabo", "Display"}
        conf.decidir(kit_id, True, uid)
        assert [n.split()[0] for n in linha()["pendentes_conferencia"]] == ["Display"]
        with db() as conn:
            conn.execute("INSERT INTO kit_validacoes (kit_id, validado_por, validado_em) VALUES (?, ?, '2026-11-04 15:00:00')",
                         (kit_id, uid))
        l = linha()
        assert l["situacao"] == "dupla" and l["validacao"]["validado_em"].startswith("2026-11-04 15:00")
        assert [x["sessao_id"] for x in conf.relatorio(situacao=["aguardando"], busca=kit["veiculo"])] == []

        nome = f"adm_rp_{kit['s']}"
        usuarios.criar("Adm RP", nome, "Teste#RelPre26", True)
        with TestClient(main.app) as c:
            c.post("/login", data={"username": nome, "password": "Teste#RelPre26"})
            h = c.get("/reports/pre-check", params={"busca": kit["veiculo"]}).text
            assert "Verificado 2 vezes" in h and kit["veiculo"] in h
            x = c.get("/reports/pre-check/export", params={"busca": kit["veiculo"]})
            ws = openpyxl.load_workbook(io.BytesIO(x.content)).active
            assert ws.cell(2, 1).value == kit["veiculo"] and ws.cell(2, 4).value == "Verificado 2 vezes"
    finally:
        with db() as conn:
            conn.execute("DELETE FROM kit_validacoes WHERE kit_id = ?", (kit_id,))
            conn.execute("DELETE FROM kit_verificacao_itens WHERE kit_id = ?", (kit_id,))
            conn.execute("DELETE FROM kit_record WHERE kit_id = ?", (kit_id,))
            conn.execute("DELETE FROM pre_conferencia WHERE sessao_id = ?", (kit["sid"],))
            conn.execute("DELETE FROM users WHERE username = ?", (f"adm_rp_{kit['s']}",))
