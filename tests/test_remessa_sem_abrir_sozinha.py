"""Remessa que bate o alvo FECHA e a próxima não abre sozinha — a Produção
mostra o aviso com o atalho "Abrir a próxima" até alguém abrir."""
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


def test_bater_o_alvo_fecha_sem_abrir_outra_e_avisa(cliente):
    r = remessas_mod.abrir("Lote A", 5, cliente)
    with db() as conn:
        conn.execute("INSERT INTO remessa_kit (remessa_id, veiculo_id, entrou_em) VALUES (?, NULL, '2026-10-01')", (r["id"],))
    remessas_mod.definir_alvo(r["id"], 1)          # a meta cai pra baixo do que já entrou
    assert remessas_mod.listar_uma(r["id"])["status"] == "fechada"
    assert _abertas(cliente) == []                 # nada abriu sozinho
    aviso = [a for a in remessas_mod.aguardando_proxima() if a["cliente"] == cliente]
    assert [a["id"] for a in aviso] == [r["id"]] and aviso[0]["alvo"] == 1
    # O operador abre a próxima: o aviso some.
    remessas_mod.abrir("Lote B", 1, cliente)
    assert not [a for a in remessas_mod.aguardando_proxima() if a["cliente"] == cliente]


def test_fechar_a_mao_nao_gera_aviso(cliente):
    r = remessas_mod.abrir("Lote M", 10, cliente)
    remessas_mod.fechar(r["id"], motivo="fechada à mão")
    assert not [a for a in remessas_mod.aguardando_proxima() if a["cliente"] == cliente]
