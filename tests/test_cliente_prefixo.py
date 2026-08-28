import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

os.environ["DB_PATH"] = ":memory:"

from database import init_db, db
import app.clientes as clientes_mod
import app.veiculos as veiculos_mod


@pytest.fixture(autouse=True)
def setup_db():
    init_db()
    with db() as conn:
        conn.executescript("""
            DELETE FROM veiculos;
            DELETE FROM garagens;
            DELETE FROM clientes;
        """)


# ── formatar_numero: função pura, sem banco ─────────────────────────────────

def test_formatar_numero_com_prefixo_e_numero_cru():
    assert clientes_mod.formatar_numero("01", "31001") == "31001-00001"
    assert clientes_mod.formatar_numero("1", "31001") == "31001-00001"
    assert clientes_mod.formatar_numero("42", "31001") == "31001-00042"


def test_formatar_numero_sem_prefixo_passa_direto():
    assert clientes_mod.formatar_numero("01", "") == "01"


def test_formatar_numero_ja_formatado_nao_e_prefixado_de_novo():
    # Tem traço/letra: já não é "número cru" — não mexe (evita prefixar
    # de novo numa reimportação, ou atropelar um número tipo VH-042).
    assert clientes_mod.formatar_numero("31001-00001", "31001") == "31001-00001"
    assert clientes_mod.formatar_numero("VH-042", "31001") == "VH-042"


def test_formatar_numero_vazio():
    assert clientes_mod.formatar_numero("", "31001") == ""


# ── criar(): cadastro manual ─────────────────────────────────────────────────

def test_criar_veiculo_aplica_prefixo_do_cliente():
    with db() as conn:
        conn.execute(
            "INSERT INTO clientes (nome, prefixo, criado_em) VALUES ('REDEMOB', '31001', '2026-01-01')"
        )
    veiculo_id = veiculos_mod.criar("01", "REDEMOB", "Garagem X")
    v = veiculos_mod.buscar(veiculo_id)
    assert v["numero"] == "31001-00001"


def test_criar_veiculo_cliente_sem_prefixo_mantem_numero():
    with db() as conn:
        conn.execute(
            "INSERT INTO clientes (nome, prefixo, criado_em) VALUES ('SEM PREFIXO', '', '2026-01-01')"
        )
    veiculo_id = veiculos_mod.criar("01", "SEM PREFIXO", "Garagem X")
    v = veiculos_mod.buscar(veiculo_id)
    assert v["numero"] == "01"


def test_criar_veiculo_cliente_nao_cadastrado_mantem_numero():
    veiculo_id = veiculos_mod.criar("01", "CLIENTE INEXISTENTE", "Garagem X")
    v = veiculos_mod.buscar(veiculo_id)
    assert v["numero"] == "01"


# ── importar_excel(): planilha ───────────────────────────────────────────────

def _planilha(linhas, cabecalho=("Número do Veículo", "Cliente", "Garagem")):
    import openpyxl, io
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(list(cabecalho))
    for linha in linhas:
        ws.append(list(linha))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_importar_excel_aplica_prefixo_por_linha():
    with db() as conn:
        conn.execute(
            "INSERT INTO clientes (nome, prefixo, criado_em) VALUES ('REDEMOB', '31001', '2026-01-01')"
        )
    xlsx = _planilha([("01", "REDEMOB", "Garagem X"), ("02", "REDEMOB", "Garagem X")])
    resultado = veiculos_mod.importar_excel(xlsx)
    assert resultado["inseridos"] == 2
    numeros = {v["numero"] for v in veiculos_mod.listar()}
    assert numeros == {"31001-00001", "31001-00002"}


def test_importar_excel_cliente_sem_prefixo_mantem_numero_da_planilha():
    xlsx = _planilha([("VH-100", "CLIENTE SEM PREFIXO", "Garagem X")])
    resultado = veiculos_mod.importar_excel(xlsx)
    assert resultado["inseridos"] == 1
    assert veiculos_mod.listar()[0]["numero"] == "VH-100"
