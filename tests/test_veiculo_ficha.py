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
           "Localização atual", "Kits montados", "Última montagem", "Remessa", "Cadastrado em"]
    linha = [numero, "CliFicha", "G1", None, None, None, None, "", 0, None, "R-1", "2026-01-01 10:00:00"]
    r = veic.importar_excel(_xlsx(cab, [linha]))
    assert r["itens"][0]["situacao"] == "igual" and not r["erros"]
    linha[4], linha[5], linha[6] = "XYZ987", "PG-3", "Combustão"
    r = veic.importar_excel(_xlsx(cab, [linha]))
    assert r["itens"][0]["situacao"] == "alterado"
    v = _v(numero)
    assert (v["chassi"], v["pg"], v["tipo"]) == ("XYZ987", "PG-3", "Combustão")


def test_regiao_pela_planilha_e_pelo_lapis(numero):
    """Região de Operação: entra pela planilha (só ela muda), normalizada em
    maiúsculas; célula vazia preserva; o lápis da janela troca só a região."""
    veic.criar(numero, "CliFicha", "G1", "", "CH1", "PG-1", "Elétrico")
    cab = ["Número do Veículo", "Cliente", "Região de Operação"]
    r = veic.importar_excel(_xlsx(cab, [[numero, "CliFicha", "  t. bandeiras "]]))
    assert r["itens"][0]["situacao"] == "alterado" and r["itens"][0]["regiao_depois"] == "T. BANDEIRAS"
    v = _v(numero)
    assert v["regiao"] == "T. BANDEIRAS" and (v["chassi"], v["pg"], v["garagem"]) == ("CH1", "PG-1", "G1")
    r = veic.importar_excel(_xlsx(cab, [[numero, "CliFicha", None]]))
    assert r["itens"][0]["situacao"] == "igual" and _v(numero)["regiao"] == "T. BANDEIRAS"
    assert "T. BANDEIRAS" in veic.regioes_disponiveis()
    assert veic.atualizar_regiao(v["id"], "pa campus") == "PA CAMPUS" and _v(numero)["regiao"] == "PA CAMPUS"


def test_tela_filtra_e_exporta_a_regiao(numero):
    import main
    vid = veic.criar(numero, "CliFicha", "G1", "", regiao="T. Garavelo")
    ctx = main._admin_veiculos_context(busca=numero, regiao=["T. GARAVELO"])
    assert [v["numero"] for v in ctx["veiculos_export"]] == [numero]
    assert main._admin_veiculos_context(busca=numero, regiao=["__sem__"])["veiculos_export"] == []
    assert ctx["col_ativa"]["regiao"] and any(c["rotulo"] == "Região: T. GARAVELO" for c in ctx["chips_veiculos"])
    assert veic.buscar(vid)["regiao"] == "T. GARAVELO"


def test_validador_simula_e_so_grava_ao_confirmar(numero):
    """Enviar a planilha só mostra a prévia (nada gravado); Confirmar importa;
    Cancelar descarta."""
    from fastapi.testclient import TestClient
    import main
    from app import usuarios
    veic.criar(numero, "CliFicha", "G1", "")
    planilha = _xlsx(["Número do Veículo", "Cliente", "Região de Operação"],
                     [[numero, "CliFicha", "T. Dergo"]])
    # A simulação calcula tudo e desfaz.
    r = veic.importar_excel(planilha, simular=True)
    assert r["itens"][0]["situacao"] == "alterado" and _v(numero)["regiao"] == ""
    nome = f"adm_val_{uuid.uuid4().hex[:6]}"
    usuarios.criar("Adm Val", nome, "Teste#Val2026", True)
    try:
        with TestClient(main.app) as c:
            c.post("/login", data={"username": nome, "password": "Teste#Val2026"})
            r = c.post("/admin/veiculos/import", files={"arquivo": ("p.xlsx", planilha)},
                       follow_redirects=False)
            previa = r.headers["location"]
            assert previa.startswith("/admin/veiculos/import/previa/")
            h = c.get(previa).text
            assert "T. DERGO" in h and "Confirmar importação" in h
            assert _v(numero)["regiao"] == ""                       # nada gravado ainda
            r = c.post(previa + "/confirmar", follow_redirects=False)
            assert r.headers["location"].startswith("/admin/veiculos/import/")
            assert _v(numero)["regiao"] == "T. DERGO"
            # A mesma prévia não pode ser confirmada de novo.
            assert "não existe mais" in c.post(previa + "/confirmar").text
            # Cancelar descarta sem gravar.
            outra = _xlsx(["Número do Veículo", "Cliente", "Região de Operação"],
                          [[numero, "CliFicha", "PA CAMPUS"]])
            previa2 = c.post("/admin/veiculos/import", files={"arquivo": ("p.xlsx", outra)},
                             follow_redirects=False).headers["location"]
            c.post(previa2 + "/cancelar")
            assert _v(numero)["regiao"] == "T. DERGO"
            assert "não existe mais" in c.get(previa2).text
    finally:
        with db() as conn:
            uid = conn.execute("SELECT id FROM users WHERE username = ?", (nome,)).fetchone()["id"]
            conn.execute("DELETE FROM importacao_item WHERE importacao_id IN "
                         "(SELECT id FROM importacao WHERE criada_por = ?)", (uid,))
            conn.execute("DELETE FROM importacao WHERE criada_por = ?", (uid,))
            conn.execute("DELETE FROM users WHERE id = ?", (uid,))
