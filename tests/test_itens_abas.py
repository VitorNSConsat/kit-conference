"""Itens & Estoque renderiza a aba Sobressalentes (escondida) em TODAS as abas: o contexto de
cada aba precisa trazer o que ela usa, senão a tela inteira dá erro 500."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ["DB_PATH"] = ":memory:"
os.environ.setdefault("SECRET_KEY", "chave-de-teste-nao-usar-em-producao")

import pytest

from database import init_db
import main

CHAVES = ("pag_sobressalentes", "sob_total", "sob_clientes_opcoes", "sob_tem_filtro",
          "sobressalente_itens_enviados", "estoque_itens", "sob_clientes")


@pytest.mark.parametrize("aba", ["catalogo", "novo", "patrimonios", "codigos", "sobressalentes", ""])
def test_contexto_de_toda_aba_traz_o_que_a_aba_sobressalentes_usa(aba):
    init_db()
    ctx = main._admin_items_context(tab=aba)
    faltando = [k for k in CHAVES if k not in ctx]
    assert not faltando, f"aba {aba!r} sem {faltando}"
    assert ctx["pag_sobressalentes"]["total"] >= 0
