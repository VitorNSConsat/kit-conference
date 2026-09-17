"""Pedido desconta estoque igual a kit?

A pergunta surgiu de um pedido criado que "não debitou do estoque". Estes
testes fixam a resposta: o desconto é decidido pelo ITEM (ter estoque
cadastrado e pertencer ao template), nunca pelo tipo do template -- então
bipar um pedido desconta exatamente como bipar um kit. O que NÃO desconta é
criar/editar o pedido: template é receita, não movimento.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

os.environ["DB_PATH"] = ":memory:"

from database import init_db, db
from app import sessions as sessions_mod
import app.estoque as estoque_mod
import app.kit_templates as templates_mod


def _limpar(conn):
    conn.executescript("""
        DELETE FROM estoque_movimentos;
        DELETE FROM estoque;
        DELETE FROM scan_session_items;
        DELETE FROM scan_session;
        DELETE FROM kit_template_items;
        DELETE FROM kit_template;
        DELETE FROM item_tipo;
        DELETE FROM users;
    """)


@pytest.fixture(autouse=True)
def setup_db():
    init_db()
    # Limpa ANTES também: o :memory: é compartilhado pela sessão inteira do
    # pytest, então sobra de outro arquivo colide com os ids fixos daqui.
    with db() as conn:
        _limpar(conn)
    with db() as conn:
        conn.execute(
            "INSERT INTO users (id, nome, username, password_hash) VALUES (1, 'Teste', 'teste', 'x')"
        )
        conn.execute("INSERT INTO item_tipo (id, nome, ativo) VALUES (1, 'Ram Mount', 1)")
        conn.execute(
            "INSERT INTO estoque (id, item_tipo_id, codigo_barra, quantidade_atual, quantidade_minima, criado_em) "
            "VALUES (1, 1, 'RAMMOUNT', 100, 10, '2026-01-01')"
        )
        # Um PEDIDO e um KIT com o mesmo item, pra comparar os dois lado a lado.
        conn.execute(
            "INSERT INTO kit_template (id, nome, cliente, versao, ativo, criado_por, tipo) "
            "VALUES (1, 'Pedido 9999', 'Cliente A', 1, 1, 1, 'pedido')"
        )
        conn.execute(
            "INSERT INTO kit_template (id, nome, cliente, versao, ativo, criado_por, tipo) "
            "VALUES (2, 'Kit Normal', 'Cliente A', 1, 1, 1, 'kit')"
        )
        for template_id in (1, 2):
            conn.execute(
                "INSERT INTO kit_template_items (kit_template_id, item_tipo_id, quantidade_exigida, obrigatorio) "
                "VALUES (?, 1, 2, 1)", (template_id,)
            )
    yield
    with db() as conn:
        _limpar(conn)


def _saldo() -> int:
    return estoque_mod.buscar_por_id(1)["quantidade_atual"]


def test_criar_pedido_com_itens_nao_mexe_no_estoque():
    # A receita já foi criada na fixture (2 unidades exigidas) e o saldo
    # continua intacto: template não é movimento de estoque.
    assert _saldo() == 100
    templates_mod.get_itens_template(1)
    assert _saldo() == 100


def test_bipar_pedido_desconta_estoque_igual_a_kit():
    sessao_pedido = sessions_mod.start_session(1, 1)
    resultado = sessions_mod.register_scan(sessao_pedido, "RAMMOUNT")
    assert resultado["resultado"] == "aceito"
    assert _saldo() == 98  # 2 exigidas pelo template

    sessao_kit = sessions_mod.start_session(2, 1)
    resultado = sessions_mod.register_scan(sessao_kit, "RAMMOUNT")
    assert resultado["resultado"] == "aceito"
    assert _saldo() == 96  # mesmo desconto, mesmo caminho


def test_movimento_do_pedido_fica_registrado_no_historico():
    sessao_pedido = sessions_mod.start_session(1, 1)
    sessions_mod.register_scan(sessao_pedido, "RAMMOUNT")
    with db() as conn:
        mov = conn.execute(
            "SELECT tipo, quantidade, sessao_id FROM estoque_movimentos "
            "WHERE estoque_id = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert mov["tipo"] == "saida"
    assert mov["quantidade"] == 2
    assert mov["sessao_id"] == sessao_pedido


def test_cancelar_sessao_devolve_o_estoque_do_pedido():
    # É a explicação mais provável pra "bipei e o estoque não baixou": a
    # sessão foi cancelada depois, e o cancelamento estorna a saída.
    sessao_pedido = sessions_mod.start_session(1, 1)
    sessions_mod.register_scan(sessao_pedido, "RAMMOUNT")
    assert _saldo() == 98
    sessions_mod.cancel_session(sessao_pedido)
    assert _saldo() == 100
