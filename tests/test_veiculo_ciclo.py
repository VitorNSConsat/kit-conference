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
