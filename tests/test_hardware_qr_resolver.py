"""Testes de main._resolver_hardware_id(): resolve o texto lido pelo scanner
do /mobile (URL da etiqueta, id puro ou rótulo RMA-000X digitado à mão) pro
id da ocorrência -- mesmo papel de _resolver_kit_id() só que pra hardware/RMA,
onde o id é um inteiro simples em vez de UUID."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import uuid

import pytest

os.environ["DB_PATH"] = ":memory:"
os.environ.setdefault("SECRET_KEY", "chave-de-teste-nao-usar-em-producao")

from database import init_db, db
import app.hardware as hw


def _limpar_tabelas_hardware(conn):
    for tabela in ("hardware_importacao_item", "hardware_importacao", "hardware_anexo",
                   "hardware_ocorrencia_evento", "hardware_ocorrencia", "hardware_produto"):
        conn.execute(f"DELETE FROM {tabela}")


@pytest.fixture(autouse=True)
def setup_db():
    init_db()
    with db() as conn:
        _limpar_tabelas_hardware(conn)
    yield
    with db() as conn:
        _limpar_tabelas_hardware(conn)


@pytest.fixture
def ocorrencia_id():
    username = f"teste-{uuid.uuid4().hex[:8]}"
    with db() as conn:
        conn.execute(
            "INSERT INTO users (username, nome, password_hash, admin, ativo) "
            "VALUES (?, 'Usuário Teste', 'x', 1, 1)", (username,))
        uid = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()["id"]
    return hw.criar({"cliente": "Eletra", "produto_nome": "MX4", "categoria_defeito": "Não liga"}, uid)


@pytest.fixture
def resolver():
    import main
    return main._resolver_hardware_id


def test_resolve_url_completa_do_qr(resolver, ocorrencia_id):
    url = f"http://localhost:8011/hardware/{ocorrencia_id}"
    assert resolver(url) == ocorrencia_id


def test_resolve_url_com_https_e_dominio_real(resolver, ocorrencia_id):
    url = f"https://kits.consat.com.br/hardware/{ocorrencia_id}"
    assert resolver(url) == ocorrencia_id


def test_resolve_id_puro_digitado(resolver, ocorrencia_id):
    assert resolver(str(ocorrencia_id)) == ocorrencia_id


def test_resolve_rotulo_rma_digitado(resolver, ocorrencia_id):
    rotulo = hw.rotulo(ocorrencia_id)  # "RMA-0001" etc.
    assert resolver(rotulo) == ocorrencia_id
    assert resolver(rotulo.lower()) == ocorrencia_id


def test_id_inexistente_retorna_none(resolver):
    assert resolver("999999") is None
    assert resolver("http://x/hardware/999999") is None


def test_texto_vazio_ou_lixo_retorna_none(resolver):
    assert resolver("") is None
    assert resolver(None) is None
    assert resolver("qualquer coisa sem numero") is None


def test_nao_confunde_com_uuid_de_kit(resolver):
    # Um QR de kit (UUID) não deve, por acidente, casar com o resolver de
    # hardware e devolver algum id numérico aleatório.
    assert resolver("http://x/kit/016064a8-106c-4ead-b0a9-f2f173d3d9ce") is None
