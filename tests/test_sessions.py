import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

# Use banco em memória para testes
os.environ["DB_PATH"] = ":memory:"

from database import init_db, db
from app import sessions as sessions_mod
import app.items as items_mod
import app.kit_templates as templates_mod


@pytest.fixture(autouse=True)
def setup_db():
    init_db()
    with db() as conn:
        conn.execute(
            "INSERT INTO users (id, nome, username, password_hash) VALUES (1, 'Teste', 'teste', 'x')"
        )
        # Tipos de item
        conn.execute("INSERT INTO item_tipo (id, nome, ativo) VALUES (1, 'Antena', 1)")
        conn.execute("INSERT INTO item_tipo (id, nome, ativo) VALUES (2, 'Cabo', 1)")
        conn.execute("INSERT INTO item_tipo (id, nome, ativo) VALUES (3, 'Roteador', 1)")
        # Patrimônios pré-cadastrados
        conn.execute(
            "INSERT INTO item_master (codigo_barra, item_tipo_id, ativo, criado_por) "
            "VALUES ('ANT001', 1, 1, 1)"
        )
        conn.execute(
            "INSERT INTO item_master (codigo_barra, item_tipo_id, ativo, criado_por) "
            "VALUES ('ANT002', 1, 1, 1)"
        )
        conn.execute(
            "INSERT INTO item_master (codigo_barra, item_tipo_id, ativo, criado_por) "
            "VALUES ('ANT003', 1, 1, 1)"
        )
        conn.execute(
            "INSERT INTO item_master (codigo_barra, item_tipo_id, ativo, criado_por) "
            "VALUES ('CAB001', 2, 1, 1)"
        )
        conn.execute(
            "INSERT INTO item_master (codigo_barra, item_tipo_id, ativo, criado_por) "
            "VALUES ('ROT001', 3, 1, 1)"
        )
        # Template: 2 Antenas (obrigatório) + 1 Cabo (obrigatório)
        conn.execute(
            "INSERT INTO kit_template (id, nome, cliente, versao, ativo, criado_por) "
            "VALUES (1, 'Kit Teste', 'Cliente A', 1, 1, 1)"
        )
        conn.execute(
            "INSERT INTO kit_template_items (kit_template_id, item_tipo_id, quantidade_exigida, obrigatorio) "
            "VALUES (1, 1, 2, 1)"  # 2 antenas
        )
        conn.execute(
            "INSERT INTO kit_template_items (kit_template_id, item_tipo_id, quantidade_exigida, obrigatorio) "
            "VALUES (1, 2, 1, 1)"  # 1 cabo
        )
    yield
    with db() as conn:
        conn.executescript("""
            DELETE FROM scan_session_items;
            DELETE FROM scan_session;
            DELETE FROM kit_template_items;
            DELETE FROM kit_template;
            DELETE FROM item_master;
            DELETE FROM item_tipo;
            DELETE FROM users;
        """)


def test_start_session():
    sessao_id = sessions_mod.start_session(1, 1)
    assert sessao_id > 0
    session = sessions_mod.get_session(sessao_id)
    assert session["status"] == "em_andamento"
    assert session["kit_template_id"] == 1


def test_register_scan_aceito():
    sessao_id = sessions_mod.start_session(1, 1)
    result = sessions_mod.register_scan(sessao_id, "ANT001")
    assert result["resultado"] == "aceito"
    assert result["contagem_atual"] == 1
    assert result["quantidade_exigida"] == 2
    assert result["item_tipo_id"] == 1


def test_register_scan_item_nao_cadastrado_retorna_desconhecido():
    sessao_id = sessions_mod.start_session(1, 1)
    result = sessions_mod.register_scan(sessao_id, "PAT_NOVO_999")
    assert result["resultado"] == "desconhecido"
    assert "tipos" in result
    assert len(result["tipos"]) > 0


def test_register_scan_identificar_e_aceitar():
    """Patrimônio desconhecido é identificado pelo operador e aceito."""
    sessao_id = sessions_mod.start_session(1, 1)
    result = sessions_mod.register_scan(sessao_id, "PAT_NOVO_001")
    assert result["resultado"] == "desconhecido"
    # operador seleciona tipo 1 (Antena)
    result = sessions_mod.register_scan(sessao_id, "PAT_NOVO_001", item_tipo_id=1)
    assert result["resultado"] == "aceito"
    assert result["contagem_atual"] == 1


def test_register_scan_item_fora_do_kit():
    sessao_id = sessions_mod.start_session(1, 1)
    result = sessions_mod.register_scan(sessao_id, "ROT001")  # Roteador não está no kit
    assert result["resultado"] == "rejeitado"
    assert "não pertence a este kit" in result["mensagem"]


def test_register_scan_quantidade_excedida():
    sessao_id = sessions_mod.start_session(1, 1)
    sessions_mod.register_scan(sessao_id, "ANT001")  # 1ª antena
    sessions_mod.register_scan(sessao_id, "ANT002")  # 2ª antena — atinge máximo
    result = sessions_mod.register_scan(sessao_id, "ANT003")  # 3ª — rejeitado
    assert result["resultado"] == "rejeitado"
    assert "quantidade máxima" in result["mensagem"]


def test_register_scan_duplicata_na_sessao():
    """Mesmo patrimônio não pode ser bipado duas vezes na mesma sessão."""
    sessao_id = sessions_mod.start_session(1, 1)
    sessions_mod.register_scan(sessao_id, "ANT001")
    result = sessions_mod.register_scan(sessao_id, "ANT001")
    assert result["resultado"] == "rejeitado"
    assert "já foi bipado" in result["mensagem"]


def test_validate_kit_incompleto():
    sessao_id = sessions_mod.start_session(1, 1)
    sessions_mod.register_scan(sessao_id, "ANT001")
    result = sessions_mod.validate_kit_complete(sessao_id)
    assert result["status"] == "incompleto"
    tipo_ids = [i["item_tipo_id"] for i in result["itens_faltantes"]]
    assert 1 in tipo_ids  # ainda falta 1 antena
    assert 2 in tipo_ids  # falta o cabo


def test_validate_kit_completo():
    sessao_id = sessions_mod.start_session(1, 1)
    sessions_mod.register_scan(sessao_id, "ANT001")
    sessions_mod.register_scan(sessao_id, "ANT002")
    sessions_mod.register_scan(sessao_id, "CAB001")
    result = sessions_mod.validate_kit_complete(sessao_id)
    assert result["status"] == "completo"
    assert result["itens_faltantes"] == []


def test_cancel_session():
    sessao_id = sessions_mod.start_session(1, 1)
    sessions_mod.cancel_session(sessao_id)
    session = sessions_mod.get_session(sessao_id)
    assert session["status"] == "cancelado"
