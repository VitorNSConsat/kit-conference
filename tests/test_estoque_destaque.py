"""Destaque "o que mudou, quem mudou" embaixo da linha do item de estoque.

Cobre as funções novas em app/estoque.py: _formatar_alteracao,
ultima_alteracao_item e ultimas_alteracoes. O pedido original foi reorganizar
o popup de estoque e, junto, mostrar quem alterou o quê nas últimas N horas
(configurável, 0 = desliga) tanto na lista quanto no popup.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime, timedelta

import pytest

os.environ["DB_PATH"] = ":memory:"

from database import init_db, db
import app.estoque as estoque_mod


def _limpar(conn):
    conn.executescript("""
        DELETE FROM estoque_movimentos;
        DELETE FROM estoque;
        DELETE FROM scan_session;
        DELETE FROM kit_template;
        DELETE FROM item_tipo;
        DELETE FROM users;
    """)


@pytest.fixture(autouse=True)
def setup_db():
    init_db()
    with db() as conn:
        _limpar(conn)
    with db() as conn:
        conn.execute(
            "INSERT INTO users (id, nome, username, password_hash) VALUES (1, 'Ana', 'ana', 'x')"
        )
        conn.execute(
            "INSERT INTO users (id, nome, username, password_hash) VALUES (2, 'Beto', 'beto', 'x')"
        )
        conn.execute("INSERT INTO item_tipo (id, nome, ativo) VALUES (1, 'Ram Mount', 1)")
        conn.execute("INSERT INTO item_tipo (id, nome, ativo) VALUES (2, 'Cabo USB', 1)")
        conn.execute(
            "INSERT INTO estoque (id, item_tipo_id, codigo_barra, quantidade_atual, quantidade_minima, criado_em) "
            "VALUES (1, 1, 'RAMMOUNT', 100, 10, '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO estoque (id, item_tipo_id, codigo_barra, quantidade_atual, quantidade_minima, criado_em) "
            "VALUES (2, 2, 'CABOUSB', 50, 5, '2026-01-01')"
        )
        # Pra simular o desconto automático de kit/pedido (registrar_saida),
        # que sempre grava sessao_id -- precisa de uma sessão de verdade
        # (FK), não só um número qualquer.
        conn.execute(
            "INSERT INTO kit_template (id, nome, cliente, versao, ativo, criado_por) "
            "VALUES (1, 'Kit Teste', 'Cliente A', 1, 1, 1)"
        )
        conn.execute(
            "INSERT INTO scan_session (id, kit_template_id, kit_template_versao, operador_id, status) "
            "VALUES (1, 1, 1, 1, 'em_andamento')"
        )
    yield
    with db() as conn:
        _limpar(conn)


def _inserir_movimento(estoque_id, tipo, quantidade, criado_por, observacao, horas_atras):
    quando = (datetime.now() - timedelta(hours=horas_atras)).strftime("%Y-%m-%d %H:%M:%S")
    with db() as conn:
        conn.execute(
            "INSERT INTO estoque_movimentos "
            "(estoque_id, tipo, quantidade, criado_por, observacao, criado_em) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (estoque_id, tipo, quantidade, criado_por, observacao, quando)
        )


def _inserir_movimento_de_sessao(estoque_id, tipo, quantidade, criado_por, observacao, horas_atras):
    """Mesma coisa, mas com sessao_id preenchido -- é assim que
    registrar_saida() (bipagem de kit/pedido) e o estorno de troca de kit
    gravam o movimento. É o sinal que ultima_alteracao_item/ultimas_alteracoes
    usam pra saber que NÃO foi um ajuste manual."""
    quando = (datetime.now() - timedelta(hours=horas_atras)).strftime("%Y-%m-%d %H:%M:%S")
    with db() as conn:
        conn.execute(
            "INSERT INTO estoque_movimentos "
            "(estoque_id, tipo, quantidade, sessao_id, criado_por, observacao, criado_em) "
            "VALUES (?, ?, ?, 1, ?, ?, ?)",
            (estoque_id, tipo, quantidade, criado_por, observacao, quando)
        )


# ── ultima_alteracao_item / ultimas_alteracoes: janela de horas ─────────────

def test_horas_zero_desliga_o_destaque():
    _inserir_movimento(1, "entrada", 10, 1, "", horas_atras=0.01)
    assert estoque_mod.ultima_alteracao_item(1, 0) is None
    assert estoque_mod.ultimas_alteracoes(0) == {}


def test_movimento_dentro_da_janela_aparece():
    _inserir_movimento(1, "entrada", 10, 1, "chegou lote novo", horas_atras=1)
    r = estoque_mod.ultima_alteracao_item(1, 24)
    assert r is not None
    assert "Entrada de 10 unidade(s)" in r["texto"]
    assert "chegou lote novo" in r["texto"]
    assert "Ana" in r["texto"]


def test_movimento_fora_da_janela_nao_aparece():
    _inserir_movimento(1, "entrada", 10, 1, "", horas_atras=30)
    assert estoque_mod.ultima_alteracao_item(1, 24) is None
    assert estoque_mod.ultimas_alteracoes(24) == {}


def test_movimento_exatamente_no_limite_da_janela_ainda_aparece():
    # horas=24 -> limite é "agora - 24h"; um movimento de 23h59min atrás
    # ainda está dentro (>=), um de 24h01min já não está.
    _inserir_movimento(1, "entrada", 5, 1, "", horas_atras=23.98)
    assert estoque_mod.ultima_alteracao_item(1, 24) is not None
    with db() as conn:
        _limpar_movimentos_item(conn, 1)
    _inserir_movimento(1, "entrada", 5, 1, "", horas_atras=24.02)
    assert estoque_mod.ultima_alteracao_item(1, 24) is None


def _limpar_movimentos_item(conn, estoque_id):
    conn.execute("DELETE FROM estoque_movimentos WHERE estoque_id = ?", (estoque_id,))


# ── _formatar_alteracao: texto por tipo de movimento ─────────────────────────

def test_formatar_entrada_com_motivo():
    mov = {"tipo": "entrada", "quantidade": 7, "observacao": "reposição mensal",
           "criado_em": "2026-09-17 10:30:00", "autor_nome": "Ana"}
    r = estoque_mod._formatar_alteracao(mov)
    assert r["texto"] == "Entrada de 7 unidade(s) (reposição mensal) — Ana, 2026-09-17 10:30"


def test_formatar_saida_sem_motivo():
    mov = {"tipo": "saida", "quantidade": 3, "observacao": "",
           "criado_em": "2026-09-17 10:30:00", "autor_nome": "Beto"}
    r = estoque_mod._formatar_alteracao(mov)
    assert r["texto"] == "Saída de 3 unidade(s) — Beto, 2026-09-17 10:30"


def test_formatar_correcao_usa_observacao_existente():
    # corrigir_quantidade() já grava "Quantidade corrigida: X -> Y" na
    # observação -- _formatar_alteracao não deve remontar isso a partir da
    # quantidade (que na correção é o valor absoluto novo, não uma diferença).
    mov = {"tipo": "correcao", "quantidade": 120, "observacao": "Quantidade corrigida: 100 → 120",
           "criado_em": "2026-09-17 10:30:00", "autor_nome": "Ana"}
    r = estoque_mod._formatar_alteracao(mov)
    assert r["texto"] == "Quantidade corrigida: 100 → 120 — Ana, 2026-09-17 10:30"


def test_formatar_sem_autor_mostra_travessao():
    mov = {"tipo": "entrada", "quantidade": 1, "observacao": "",
           "criado_em": "2026-09-17 10:30:00", "autor_nome": None}
    r = estoque_mod._formatar_alteracao(mov)
    assert "— —, 2026-09-17 10:30" in r["texto"]


# ── ultimas_alteracoes: bulk, um mapa por estoque_id, só o mais recente ─────

def test_ultimas_alteracoes_pega_so_o_mais_recente_de_cada_item():
    _inserir_movimento(1, "entrada", 10, 1, "primeiro", horas_atras=5)
    _inserir_movimento(1, "saida", 3, 2, "segundo", horas_atras=1)
    _inserir_movimento(2, "correcao", 40, 1, "Quantidade corrigida: 50 → 40", horas_atras=2)

    mapa = estoque_mod.ultimas_alteracoes(24)
    assert set(mapa.keys()) == {1, 2}
    assert "segundo" in mapa[1]["texto"]
    assert "primeiro" not in mapa[1]["texto"]
    assert "Quantidade corrigida" in mapa[2]["texto"]


def test_ultimas_alteracoes_ignora_ajuste_minimo_e_status_compra():
    _inserir_movimento(1, "ajuste_minimo", 20, 1, "Mínimo alterado: 10 → 20", horas_atras=1)
    _inserir_movimento(1, "status_compra", 0, 1, "Status de compra: ... → ...", horas_atras=1)
    assert estoque_mod.ultimas_alteracoes(24) == {}
    assert estoque_mod.ultima_alteracao_item(1, 24) is None


def test_ultimas_alteracoes_sem_movimentos_recentes_fica_vazio():
    assert estoque_mod.ultimas_alteracoes(24) == {}


# ── só manual: kit/pedido bipado e estorno de troca de kit ficam de fora ────

def test_saida_de_bipagem_de_kit_nao_aparece_no_destaque():
    # registrar_saida() (chamada pela bipagem de kit ou pedido) grava
    # sessao_id -- o pedido foi explícito: o destaque não deve contar isso.
    _inserir_movimento_de_sessao(1, "saida", 2, 1, "Kit", horas_atras=1)
    assert estoque_mod.ultima_alteracao_item(1, 24) is None
    assert estoque_mod.ultimas_alteracoes(24) == {}


def test_estorno_de_troca_de_kit_nao_aparece_no_destaque():
    # Troca de kit do kit pronto grava entrada/saída de estorno com
    # sessao_id também -- mesmo raciocínio: não é um ajuste manual do
    # operador na tela de estoque.
    _inserir_movimento_de_sessao(1, "entrada", 5, 1, "Estorno — troca de kit do kit pronto abc12345", horas_atras=1)
    assert estoque_mod.ultima_alteracao_item(1, 24) is None
    assert estoque_mod.ultimas_alteracoes(24) == {}


def test_saida_manual_aparece_mesmo_com_saida_de_kit_mais_recente():
    # A saída manual (− Remover, sem sessao_id) é a última alteração que
    # importa pro destaque, mesmo que uma saída de kit tenha acontecido
    # depois -- o destaque busca o mais recente MANUAL, não o mais recente
    # de qualquer tipo.
    _inserir_movimento(1, "saida", 4, 1, "conferência física", horas_atras=2)
    _inserir_movimento_de_sessao(1, "saida", 1, 2, "Kit", horas_atras=1)

    r = estoque_mod.ultima_alteracao_item(1, 24)
    assert r is not None
    assert "conferência física" in r["texto"]

    mapa = estoque_mod.ultimas_alteracoes(24)
    assert "conferência física" in mapa[1]["texto"]
