"""Ficha técnica do veículo (chassi, PG, tipo): normalização e importação por planilha."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import io
import uuid

import openpyxl
import pytest

os.environ["DB_PATH"] = ":memory:"
os.environ.setdefault("SECRET_KEY", "chave-de-teste-nao-usar-em-producao")

from database import init_db, db
import app.veiculos as veic


@pytest.fixture
def numero():
    init_db()
    n = f"FT-{uuid.uuid4().hex[:6]}"
    yield n
    with db() as conn:
        conn.execute("DELETE FROM veiculos WHERE numero = ?", (n,))


def _xlsx(cabecalho, linhas):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(cabecalho)
    for l in linhas:
        ws.append(l)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _v(numero):
    with db() as conn:
        return dict(conn.execute("SELECT * FROM veiculos WHERE numero = ?", (numero,)).fetchone())


def test_normalizar_tipo():
    assert veic.normalizar_tipo("eletrico") == "Elétrico"
    assert veic.normalizar_tipo(" ELÉTRICO ") == "Elétrico"
    assert veic.normalizar_tipo("Combustao") == "Combustão"
    assert veic.normalizar_tipo("Diesel") == "Combustão"
    assert veic.normalizar_tipo("") == ""
    assert veic.normalizar_tipo("nave espacial") is None


def test_importa_chassi_pg_e_tipo(numero):
    r = veic.importar_excel(_xlsx(["Número do Veículo", "Cliente", "Garagem", "Chassi", "PG", "Tipo"],
                                  [[numero, "CliFicha", "G1", " 9bm 384 ", "PG-07", "eletrico"]]))
    assert r["inseridos"] == 1
    v = _v(numero)
    assert (v["chassi"], v["pg"], v["tipo"]) == ("9BM384", "PG-07", "Elétrico")
    assert r["itens"][0]["chassi_depois"] == "9BM384" and r["itens"][0]["tipo_depois"] == "Elétrico"


def test_reimportar_so_com_chassi_novo_conta_como_alterado_e_vazio_preserva(numero):
    veic.importar_excel(_xlsx(["Número do Veículo", "Cliente", "Chassi", "PG", "Tipo"],
                              [[numero, "CliFicha", "AAA", "PG-1", "Combustão"]]))
    # muda só o chassi; PG e tipo vêm vazios e devem ser preservados
    r = veic.importar_excel(_xlsx(["Número do Veículo", "Cliente", "Chassi", "PG", "Tipo"],
                                  [[numero, "CliFicha", "BBB", "", ""]]))
    assert r["atualizados"] == 1 and r["itens"][0]["situacao"] == "alterado"
    v = _v(numero)
    assert (v["chassi"], v["pg"], v["tipo"]) == ("BBB", "PG-1", "Combustão")
    # de novo igual: nada muda
    r = veic.importar_excel(_xlsx(["Número do Veículo", "Cliente", "Chassi"], [[numero, "CliFicha", "BBB"]]))
    assert r["itens"][0]["situacao"] == "igual"


def test_tipo_desconhecido_vira_aviso_e_nao_estraga_o_cadastro(numero):
    veic.importar_excel(_xlsx(["Número do Veículo", "Cliente", "Tipo"], [[numero, "CliFicha", "Elétrico"]]))
    r = veic.importar_excel(_xlsx(["Número do Veículo", "Cliente", "Tipo"], [[numero, "CliFicha", "foguete"]]))
    assert "não reconhecido" in r["itens"][0]["erro"]
    assert _v(numero)["tipo"] == "Elétrico"


def test_planilha_antiga_sem_as_colunas_continua_funcionando(numero):
    r = veic.importar_excel(_xlsx(["Número do Veículo", "Cliente"], [[numero, "CliFicha"]]))
    assert r["inseridos"] == 1 and _v(numero)["chassi"] == ""


def test_atualizar_ficha_mantem_none_e_recusa_tipo_invalido(numero):
    vid = veic.criar(numero, "CliFicha", "", "", "abc 123", "PG-9", "diesel")
    v = veic.buscar(vid)
    assert (v["chassi"], v["pg"], v["tipo"]) == ("ABC123", "PG-9", "Combustão")
    veic.atualizar_ficha(vid, pg="PG-10")
    assert veic.buscar(vid)["chassi"] == "ABC123" and veic.buscar(vid)["pg"] == "PG-10"
    with pytest.raises(ValueError):
        veic.atualizar_ficha(vid, tipo="xyz")


def test_planilha_exportada_volta_como_importacao(numero):
    """A planilha de Veículos e Clientes (com as colunas extras dela) serve de
    entrada: reimportar sem mexer não altera nada; preencher o chassi atualiza."""
    veic.criar(numero, "CliFicha", "G1", "")
    cab = ["Número", "Cliente", "Garagem", "Modelo (Kit)", "Chassi", "PG", "Tipo",
           "Localização atual", "Kits enviados", "Último envio", "Cadastrado em"]
    linha = [numero, "CliFicha", "G1", None, None, None, None, "", 0, None, "2026-01-01 10:00:00"]
    r = veic.importar_excel(_xlsx(cab, [linha]))
    assert r["itens"][0]["situacao"] == "igual" and not r["erros"]
    linha[4], linha[5], linha[6] = "XYZ987", "PG-3", "Combustão"
    r = veic.importar_excel(_xlsx(cab, [linha]))
    assert r["itens"][0]["situacao"] == "alterado"
    v = _v(numero)
    assert (v["chassi"], v["pg"], v["tipo"]) == ("XYZ987", "PG-3", "Combustão")
