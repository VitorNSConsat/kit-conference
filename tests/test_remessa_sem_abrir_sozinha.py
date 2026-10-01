"""Remessa que bate o alvo FECHA e a próxima não abre sozinha — quem abre a
seguinte é o operador."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import uuid

import pytest

os.environ["DB_PATH"] = ":memory:"
os.environ.setdefault("SECRET_KEY", "chave-de-teste-nao-usar-em-producao")

from database import init_db, db
import app.remessas as remessas_mod


@pytest.fixture
def cliente():
    init_db()
    nome = f"CliRem-{uuid.uuid4().hex[:6]}"
    yield nome
    with db() as conn:
        conn.execute("DELETE FROM remessa_kit WHERE remessa_id IN (SELECT id FROM remessa WHERE cliente = ?)", (nome,))
        conn.execute("DELETE FROM remessa WHERE cliente = ?", (nome,))


def _abertas(cli):
    return [r for r in remessas_mod.listar_abertas() if r["cliente"] == cli]


def test_bater_o_alvo_fecha_sem_abrir_outra(cliente):
    r = remessas_mod.abrir("Lote A", 5, cliente)
    with db() as conn:
        conn.execute("INSERT INTO remessa_kit (remessa_id, veiculo_id, entrou_em) VALUES (?, NULL, '2026-10-01')", (r["id"],))
    remessas_mod.definir_alvo(r["id"], 1)          # a meta cai pra baixo do que já entrou
    assert remessas_mod.listar_uma(r["id"])["status"] == "fechada"
    assert _abertas(cliente) == []                 # nada abriu sozinho
    with db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM remessa WHERE cliente = ?", (cliente,)).fetchone()[0] == 1
    # O operador abre a próxima quando quiser.
    remessas_mod.abrir("Lote B", 1, cliente)
    assert [x["nome"] for x in _abertas(cliente)] == ["Lote B"]


@pytest.fixture
def veiculos(cliente):
    """Três veículos do cliente: dois prontos pra produzir (com modelo de kit
    ativo) e um com cadastro incompleto (sem garagem)."""
    s = uuid.uuid4().hex[:6]
    with db() as conn:
        conn.execute("INSERT INTO kit_template (nome, cliente, versao, ativo, tipo) VALUES (?, ?, 1, 1, 'kit')",
                     (f"Modelo {s}", cliente))
        for n, gar in ((f"N1-{s}", "G1"), (f"N2-{s}", "G1"), (f"N3-{s}", "")):
            conn.execute("INSERT INTO veiculos (numero, cliente, garagem, modelo, ativo, criado_em) "
                         "VALUES (?, ?, ?, ?, 1, '2026-10-01')", (n, cliente, gar, f"Modelo {s}"))
    yield {"n1": f"N1-{s}", "n2": f"N2-{s}", "n3": f"N3-{s}"}
    with db() as conn:
        conn.execute("DELETE FROM remessa_kit WHERE veiculo_id IN (SELECT id FROM veiculos WHERE numero LIKE ?)", (f"%-{s}",))
        conn.execute("DELETE FROM veiculos WHERE numero LIKE ?", (f"%-{s}",))
        conn.execute("DELETE FROM kit_template WHERE nome = ?", (f"Modelo {s}",))


def test_acrescentar_digitando_os_numeros(cliente, veiculos):
    v = veiculos
    r = remessas_mod.abrir("Lote N", 10, cliente)
    achados = remessas_mod.refs_por_numeros(f"{v['n1'].lower()}, {v['n3']}  NAOEXISTE-99")
    assert len(achados["refs"]) == 1 and achados["refs"][0].startswith("veic:")
    assert achados["sem_etapa"] == [v["n3"]] and achados["nao_cadastrado"] == ["NAOEXISTE-99"]
    remessas_mod.adicionar_itens(r["id"], achados["refs"])
    # Já está na remessa: digitar de novo diz isso em vez de duplicar.
    de_novo = remessas_mod.refs_por_numeros(f"{v['n1']}\n{v['n2']}")
    assert de_novo["ja_em_remessa"] == [v["n1"]] and len(de_novo["refs"]) == 1
    assert [k["veiculo"] for k in remessas_mod.kits_da_remessa(r["id"])] == [v["n1"]]
