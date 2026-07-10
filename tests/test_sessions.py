import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import sqlite3
from unittest.mock import patch

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
        conn.execute(
            "INSERT INTO item_master (codigo_barra, descricao, unidade, ativo, controla_serial) "
            "VALUES ('ANT001', 'Antena', 'UN', 1, 0)"
        )
        conn.execute(
            "INSERT INTO item_master (codigo_barra, descricao, unidade, ativo, controla_serial) "
            "VALUES ('CAB001', 'Cabo', 'UN', 1, 0)"
        )
        conn.execute(
            "INSERT INTO item_master (codigo_barra, descricao, unidade, ativo, controla_serial) "
            "VALUES ('SER001', 'Item Serial', 'UN', 1, 1)"
        )
        conn.execute(
            "INSERT INTO kit_template (id, nome, cliente, versao, ativo, criado_por) "
            "VALUES (1, 'Kit Teste', 'Cliente A', 1, 1, 1)"
        )
        conn.execute(
            "INSERT INTO kit_template_items (kit_template_id, codigo_barra, quantidade_exigida, obrigatorio) "
            "VALUES (1, 'ANT001', 2, 1)"
        )
        conn.execute(
            "INSERT INTO kit_template_items (kit_template_id, codigo_barra, quantidade_exigida, obrigatorio) "
            "VALUES (1, 'CAB001', 1, 1)"
        )
    yield
    # limpar
    with db() as conn:
        conn.executescript("""
            DELETE FROM scan_session_items;
            DELETE FROM scan_session;
            DELETE FROM kit_template_items;
            DELETE FROM kit_template;
            DELETE FROM item_master;
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


def test_register_scan_item_nao_cadastrado():
    sessao_id = sessions_mod.start_session(1, 1)
    result = sessions_mod.register_scan(sessao_id, "XXXX999")
    assert result["resultado"] == "rejeitado"
    assert "não encontrado no catálogo" in result["mensagem"]


def test_register_scan_item_fora_do_kit():
    sessao_id = sessions_mod.start_session(1, 1)
    result = sessions_mod.register_scan(sessao_id, "SER001")
    assert result["resultado"] == "rejeitado"
    assert "não pertence a este kit" in result["mensagem"]


def test_register_scan_quantidade_excedida():
    sessao_id = sessions_mod.start_session(1, 1)
    sessions_mod.register_scan(sessao_id, "ANT001")
    sessions_mod.register_scan(sessao_id, "ANT001")
    result = sessions_mod.register_scan(sessao_id, "ANT001")
    assert result["resultado"] == "rejeitado"
    assert "quantidade máxima" in result["mensagem"]


def test_validate_kit_incompleto():
    sessao_id = sessions_mod.start_session(1, 1)
    sessions_mod.register_scan(sessao_id, "ANT001")
    result = sessions_mod.validate_kit_complete(sessao_id)
    assert result["status"] == "incompleto"
    codigos_faltantes = [i["codigo_barra"] for i in result["itens_faltantes"]]
    assert "ANT001" in codigos_faltantes  # falta 1 antena
    assert "CAB001" in codigos_faltantes


def test_validate_kit_completo():
    sessao_id = sessions_mod.start_session(1, 1)
    sessions_mod.register_scan(sessao_id, "ANT001")
    sessions_mod.register_scan(sessao_id, "ANT001")
    sessions_mod.register_scan(sessao_id, "CAB001")
    result = sessions_mod.validate_kit_complete(sessao_id)
    assert result["status"] == "completo"
    assert result["itens_faltantes"] == []


def test_cancel_session():
    sessao_id = sessions_mod.start_session(1, 1)
    sessions_mod.cancel_session(sessao_id)
    session = sessions_mod.get_session(sessao_id)
    assert session["status"] == "cancelado"
