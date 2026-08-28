import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

os.environ["DB_PATH"] = ":memory:"

from database import init_db, db
import app.estoque as estoque_mod


@pytest.fixture(autouse=True)
def setup_db():
    init_db()
    with db() as conn:
        conn.executescript("""
            DELETE FROM estoque_movimentos;
            DELETE FROM estoque;
            DELETE FROM item_tipo;
        """)
        conn.execute(
            "INSERT INTO item_tipo (id, nome, ativo) VALUES (1, 'Antena', 1)"
        )
        conn.execute(
            "INSERT INTO estoque (id, item_tipo_id, codigo_barra, quantidade_atual, quantidade_minima) "
            "VALUES (1, 1, 'ANT-001', 500, 10)"
        )
        for i in range(120):
            conn.execute(
                "INSERT INTO estoque_movimentos (estoque_id, tipo, quantidade, criado_em) "
                "VALUES (1, 'entrada', 1, ?)",
                (f"2026-01-01 {i:02d}:00:00",)
            )
    yield
    # Banco em memória é compartilhado entre arquivos de teste na mesma
    # sessão do pytest — sem isso, o próximo arquivo a rodar herdaria o
    # item_tipo id=1 daqui e colidiria com o dele.
    with db() as conn:
        conn.executescript("""
            DELETE FROM estoque_movimentos;
            DELETE FROM estoque;
            DELETE FROM item_tipo;
        """)


def test_contar_historico_conta_tudo():
    assert estoque_mod.contar_historico(1) == 120


def test_listar_historico_pagina_1_traz_50_mais_recentes():
    pagina1 = estoque_mod.listar_historico(1, limit=50, offset=0)
    assert len(pagina1) == 50


def test_listar_historico_paginas_seguintes_alcancam_todo_o_historico():
    vistos = set()
    for offset in (0, 50, 100):
        for row in estoque_mod.listar_historico(1, limit=50, offset=offset):
            vistos.add(row["id"])
    assert len(vistos) == 120  # nada duplicado, nada faltando


def test_listar_historico_limit_8_continua_funcionando_sem_offset():
    # Chamada usada no painel embutido (main.py) — precisa continuar
    # aceitando só `limit`, sem passar offset.
    assert len(estoque_mod.listar_historico(1, limit=8)) == 8
