import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

os.environ["DB_PATH"] = ":memory:"

from database import init_db, db
import app.veiculos as veiculos_mod


@pytest.fixture(autouse=True)
def setup_db():
    init_db()
    with db() as conn:
        conn.executescript("""
            DELETE FROM importacao_item;
            DELETE FROM importacao;
            DELETE FROM remessa_kit;
            DELETE FROM remessa;
            DELETE FROM kit_record;
            DELETE FROM scan_session;
            DELETE FROM kit_template;
            DELETE FROM veiculos;
            DELETE FROM users;
        """)
        conn.execute(
            "INSERT INTO users (id, nome, username, password_hash) VALUES (1, 'Teste', 'teste', 'x')"
        )
        conn.execute(
            "INSERT INTO veiculos (id, numero, cliente, garagem, ativo, criado_em) "
            "VALUES (1, 'VH-001', 'Cliente X', 'Garagem X', 1, '2026-01-01')"
        )
    yield
    # Banco em memória é compartilhado entre arquivos de teste na mesma
    # sessão do pytest — sem isso, o próximo arquivo a rodar herdaria os
    # ids fixos usados aqui (kit_template 1, scan_session 1, users 1...).
    with db() as conn:
        conn.executescript("""
            DELETE FROM importacao_item;
            DELETE FROM importacao;
            DELETE FROM remessa_kit;
            DELETE FROM remessa;
            DELETE FROM kit_record;
            DELETE FROM scan_session;
            DELETE FROM kit_template;
            DELETE FROM veiculos;
            DELETE FROM users;
        """)


def test_deletar_veiculo_ainda_nao_produzido_em_remessa_sem_kit():
    """Veículo reservado numa remessa mas ainda não produzido: a linha da
    remessa não tem kit_id, então não tem o que preservar — some junto."""
    with db() as conn:
        conn.execute(
            "INSERT INTO remessa (id, nome, alvo, criada_em) VALUES (1, 'Lote 1', 10, '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO remessa_kit (remessa_id, veiculo_id, entrou_em) VALUES (1, 1, '2026-01-01')"
        )

    veiculos_mod.deletar(1)  # não pode levantar sqlite3.IntegrityError

    with db() as conn:
        assert conn.execute("SELECT * FROM veiculos WHERE id=1").fetchone() is None
        assert conn.execute("SELECT * FROM remessa_kit WHERE veiculo_id=1").fetchone() is None
        assert conn.execute("SELECT COUNT(*) FROM remessa_kit").fetchone()[0] == 0


def test_deletar_veiculo_ja_produzido_em_remessa_preserva_o_kit():
    """Veículo já produzido (remessa_kit tem kit_id): a linha fica, só solta
    o vínculo — o kit continua existindo e mostrando o número do veículo."""
    with db() as conn:
        conn.execute(
            "INSERT INTO remessa (id, nome, alvo, criada_em) VALUES (1, 'Lote 1', 10, '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO kit_template (id, nome, cliente, versao) VALUES (1, 'Kit X', 'Cliente X', 1)"
        )
        conn.execute(
            "INSERT INTO scan_session (id, kit_template_id, kit_template_versao, operador_id) "
            "VALUES (1, 1, 1, 1)"
        )
        conn.execute(
            "INSERT INTO kit_record (kit_id, sessao_id, kit_template_id, kit_template_versao, "
            "operador_id, veiculo_id, veiculo) "
            "VALUES ('K1', 1, 1, 1, 1, 1, '')"
        )
        conn.execute(
            "INSERT INTO remessa_kit (remessa_id, kit_id, veiculo_id, entrou_em) "
            "VALUES (1, 'K1', 1, '2026-01-01')"
        )

    veiculos_mod.deletar(1)

    with db() as conn:
        rk = conn.execute("SELECT * FROM remessa_kit WHERE kit_id='K1'").fetchone()
        assert rk is not None
        assert rk["veiculo_id"] is None
        kr = conn.execute("SELECT * FROM kit_record WHERE kit_id='K1'").fetchone()
        assert kr["veiculo_id"] is None
        assert kr["veiculo"] == "VH-001"


def test_deletar_veiculo_com_linha_de_importacao():
    """importacao_item também referencia veiculos(id) — precisa soltar o
    vínculo sem perder o número em texto que a tela de conferência usa."""
    with db() as conn:
        conn.execute(
            "INSERT INTO importacao (id, criada_em) VALUES (1, '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO importacao_item (importacao_id, linha, numero, situacao, veiculo_id) "
            "VALUES (1, 1, 'VH-001', 'novo', 1)"
        )

    veiculos_mod.deletar(1)

    with db() as conn:
        item = conn.execute("SELECT * FROM importacao_item WHERE numero='VH-001'").fetchone()
        assert item is not None
        assert item["veiculo_id"] is None
