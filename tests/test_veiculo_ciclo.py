"""ciclo_do_veiculo(): a remessa do veículo e a data de cada etapa, lidas do que
o sistema já grava — incluindo o veículo que só está numa remessa (a produzir)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import uuid

import pytest

os.environ["DB_PATH"] = ":memory:"
os.environ.setdefault("SECRET_KEY", "chave-de-teste-nao-usar-em-producao")

from database import init_db, db
import app.veiculos as veiculos_mod


@pytest.fixture
def cenario():
    init_db()
    sufixo = uuid.uuid4().hex[:8]
    with db() as conn:
        conn.execute("INSERT INTO users (username, nome, password_hash, admin, ativo) "
                     "VALUES (?, 'Op', 'x', 1, 1)", (f"op-{sufixo}",))
        uid = conn.execute("SELECT id FROM users WHERE username = ?", (f"op-{sufixo}",)).fetchone()["id"]
        conn.execute("INSERT INTO veiculos (numero, cliente, garagem, ativo, criado_em) "
                     "VALUES (?, 'CliX', 'G', 1, '2026-01-01')", (f"V-{sufixo}",))
        vid = conn.execute("SELECT id FROM veiculos WHERE numero = ?", (f"V-{sufixo}",)).fetchone()["id"]
        conn.execute("INSERT INTO kit_template (nome, cliente, versao) VALUES (?, 'CliX', 1)", (f"Kit {sufixo}",))
        tid = conn.execute("SELECT id FROM kit_template WHERE nome = ?", (f"Kit {sufixo}",)).fetchone()["id"]
    yield {"uid": uid, "vid": vid, "tid": tid, "sufixo": sufixo}
    with db() as conn:
        conn.execute("DELETE FROM remessa_kit WHERE veiculo_id = ? OR kit_id LIKE ?", (vid, f"K-{sufixo}%"))
        conn.execute("DELETE FROM remessa WHERE nome = ?", (f"R-{sufixo}",))
        conn.execute("DELETE FROM kit_record WHERE veiculo_id = ?", (vid,))
        conn.execute("DELETE FROM scan_session WHERE veiculo_id = ? OR kit_template_id = ?", (vid, tid))
        conn.execute("DELETE FROM kit_template WHERE id = ?", (tid,))
        conn.execute("DELETE FROM veiculos WHERE id = ?", (vid,))
        conn.execute("DELETE FROM users WHERE id = ?", (uid,))


def test_veiculo_sem_nada_devolve_etapas_vazias(cenario):
    ciclo = veiculos_mod.ciclo_do_veiculo(cenario["vid"])
    assert ciclo["remessa"] is None and ciclo["nota"] is None and not ciclo["tem_kit"]
    assert [e["rotulo"] for e in ciclo["etapas"]][:2] == ["Produção iniciada", "Produzido"]
    assert not any(e["feita"] for e in ciclo["etapas"])


def test_veiculo_a_produzir_ja_mostra_a_remessa(cenario):
    with db() as conn:
        conn.execute("INSERT INTO remessa (nome, cliente, alvo, criada_em) VALUES (?, 'CliX', 90, '2026-09-01 08:00:00')",
                     (f"R-{cenario['sufixo']}",))
        rid = conn.execute("SELECT id FROM remessa WHERE nome = ?", (f"R-{cenario['sufixo']}",)).fetchone()["id"]
        conn.execute("INSERT INTO remessa_kit (remessa_id, veiculo_id, entrou_em) VALUES (?, ?, '2026-09-02 09:15:00')",
                     (rid, cenario["vid"]))
    r = veiculos_mod.ciclo_do_veiculo(cenario["vid"])["remessa"]
    assert r["nome"] == f"R-{cenario['sufixo']}" and r["alvo"] == 90 and r["enviados"] == 1
    assert r["entrou_br"] == "02/09/2026 09:15"


def test_kit_em_transito_com_nota_fiscal(cenario):
    c = cenario
    with db() as conn:
        conn.execute("INSERT INTO scan_session (kit_template_id, kit_template_versao, operador_id, iniciado_em, veiculo_id) "
                     "VALUES (?, 1, ?, '2026-09-03 07:00:00', ?)", (c["tid"], c["uid"], c["vid"]))
        sid = conn.execute("SELECT id FROM scan_session WHERE veiculo_id = ?", (c["vid"],)).fetchone()["id"]
        conn.execute(
            "INSERT INTO kit_record (kit_id, sessao_id, kit_template_id, kit_template_versao, operador_id, veiculo_id, "
            "veiculo, finalizado_em, status_producao, transito_em, nota_fiscal, nota_fiscal_data) "
            "VALUES (?, ?, ?, 1, ?, ?, '', '2026-09-03 10:30:00', 'transito', '2026-09-05 16:00:00', 'NF-123', '2026-09-04')",
            (f"K-{c['sufixo']}", sid, c["tid"], c["uid"], c["vid"]))
    ciclo = veiculos_mod.ciclo_do_veiculo(c["vid"])
    datas = {e["rotulo"]: e["data"] for e in ciclo["etapas"]}
    assert datas["Produção iniciada"] == "03/09/2026 07:00"
    assert datas["Produzido"] == "03/09/2026 10:30"
    assert datas["Em trânsito"] == "05/09/2026 16:00"
    assert datas["Chegou ao cliente"] is None
    assert [e["rotulo"] for e in ciclo["etapas"] if e["atual"]] == ["Em trânsito"]
    assert ciclo["nota"] == {"numero": "NF-123", "data": "04/09/2026"}


def _remessa(c, status="aberta"):
    with db() as conn:
        conn.execute("INSERT INTO remessa (nome, cliente, alvo, criada_em, status) "
                     "VALUES (?, 'CliX', 90, '2026-09-01 08:00:00', ?)", (f"R-{c['sufixo']}", status))
        return conn.execute("SELECT id FROM remessa WHERE nome = ?", (f"R-{c['sufixo']}",)).fetchone()["id"]


def test_remessa_dos_veiculos_pela_linha_e_pelo_kit(cenario):
    """Coluna Remessa de Veículos e Clientes: acha o veículo pela linha dele
    (a produzir) e também pelo kit, quando a linha não guarda o veiculo_id."""
    import app.remessas as remessas_mod
    c = cenario
    assert c["vid"] not in remessas_mod.remessa_dos_veiculos()
    rid = _remessa(c, "fechada")
    with db() as conn:
        conn.execute("INSERT INTO scan_session (kit_template_id, kit_template_versao, operador_id, iniciado_em, veiculo_id) "
                     "VALUES (?, 1, ?, '2026-09-03 07:00:00', ?)", (c["tid"], c["uid"], c["vid"]))
        sid = conn.execute("SELECT id FROM scan_session WHERE veiculo_id = ?", (c["vid"],)).fetchone()["id"]
        conn.execute("INSERT INTO kit_record (kit_id, sessao_id, kit_template_id, kit_template_versao, operador_id, "
                     "veiculo_id, veiculo, finalizado_em) VALUES (?, ?, ?, 1, ?, ?, '', '2026-09-03 10:30:00')",
                     (f"K-{c['sufixo']}", sid, c["tid"], c["uid"], c["vid"]))
        conn.execute("INSERT INTO remessa_kit (remessa_id, kit_id, entrou_em) VALUES (?, ?, '2026-09-03 10:31:00')",
                     (rid, f"K-{c['sufixo']}"))
    info = remessas_mod.remessa_dos_veiculos()[c["vid"]]
    assert info == {"remessa_id": rid, "nome": f"R-{c['sufixo']}", "status": "fechada"}


def test_tela_de_veiculos_filtra_por_remessa(cenario):
    import main
    c = cenario
    numero = f"V-{c['sufixo']}"

    def numeros(**kw):
        ctx = main._admin_veiculos_context(busca=numero, **kw)
        return [v["numero"] for v in ctx["veiculos_export"]], ctx

    nums, ctx = numeros(remessa=["__sem__"])
    assert nums == [numero] and ctx["veiculos_export"][0]["remessa"] is None
    rid = _remessa(c)
    with db() as conn:
        conn.execute("INSERT INTO remessa_kit (remessa_id, veiculo_id, entrou_em) VALUES (?, ?, '2026-09-02 09:15:00')",
                     (rid, c["vid"]))
    assert numeros(remessa=["__sem__"])[0] == []
    nums, ctx = numeros(remessa=[str(rid)])
    assert nums == [numero]
    assert (str(rid), f"R-{c['sufixo']}") in ctx["remessa_opcoes"]
    assert ctx["col_ativa"]["remessa"] and any(ch["rotulo"] == f"Remessa: R-{c['sufixo']}"
                                               for ch in ctx["chips_veiculos"])


def test_depois_da_nota_pergunta_se_coloca_em_transito(cenario):
    """Nota atribuída a kit em Produzido: a tela pergunta se já é pra pôr Em
    Trânsito (com o kit na janela). Kit já em trânsito não gera a pergunta."""
    from fastapi.testclient import TestClient
    import main
    from app import usuarios
    c = cenario
    login = f"nf-{c['sufixo']}"
    usuarios.criar("Adm NF", login, "Teste#Nota26", True)
    with db() as conn:
        conn.execute("INSERT INTO scan_session (kit_template_id, kit_template_versao, operador_id, iniciado_em, veiculo_id) "
                     "VALUES (?, 1, ?, '2026-09-03 07:00:00', ?)", (c["tid"], c["uid"], c["vid"]))
        sid = conn.execute("SELECT id FROM scan_session WHERE veiculo_id = ?", (c["vid"],)).fetchone()["id"]
        for k, etapa in ((f"K-{c['sufixo']}a", "produzido"), (f"K-{c['sufixo']}b", "transito")):
            conn.execute(
                "INSERT INTO kit_record (kit_id, sessao_id, kit_template_id, kit_template_versao, operador_id, veiculo_id, "
                "veiculo, finalizado_em, status_producao) VALUES (?, ?, ?, 1, ?, ?, ?, '2026-09-03 10:30:00', ?)",
                (k, sid, c["tid"], c["uid"], c["vid"], k, etapa))
    try:
        with TestClient(main.app) as cli:
            cli.post("/login", data={"username": login, "password": "Teste#Nota26"})
            r = cli.post(f"/admin/producao/K-{c['sufixo']}a/nota-fiscal",
                         data={"nota_fiscal": "777", "nota_fiscal_data": "2026-09-04"}, follow_redirects=False)
            assert r.headers["location"].endswith(f"ok=nota_fiscal&transitar=K-{c['sufixo']}a")
            r = cli.post("/admin/producao/nota-lote", follow_redirects=False,
                         data={"kit_ids": [f"K-{c['sufixo']}a", f"K-{c['sufixo']}b"], "nota_fiscal": "778",
                               "nota_fiscal_data": "2026-09-04", "motivo": "teste"})
            assert f"transitar=K-{c['sufixo']}a" in r.headers["location"]
            assert f"K-{c['sufixo']}b" not in r.headers["location"]
            h = cli.get(f"/admin/producao?ok=nota_lote&n=2&transitar=K-{c['sufixo']}a,K-{c['sufixo']}b").text
            assert 'id="popup-oferta-transito"' in h and "Colocar em trânsito?" in h
            assert f'name="kit_ids" value="K-{c["sufixo"]}a"' in h
            assert f'name="kit_ids" value="K-{c["sufixo"]}b"' not in h.split('id="popup-oferta-transito"')[1].split("</form>")[0]
            assert 'id="popup-oferta-transito"' not in cli.get("/admin/producao?ok=nota_fiscal").text
    finally:
        with db() as conn:
            conn.execute("DELETE FROM users WHERE username = ?", (login,))
            conn.execute("DELETE FROM kit_record WHERE kit_id LIKE ?", (f"K-{c['sufixo']}%",))
