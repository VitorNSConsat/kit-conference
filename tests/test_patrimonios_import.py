"""Importação de patrimônios por planilha, reconhecimento na bipagem e o
relatório de patrimônios."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import io
import uuid
from datetime import date

import pytest

os.environ["DB_PATH"] = ":memory:"
os.environ.setdefault("SECRET_KEY", "chave-de-teste-nao-usar-em-producao")

from database import init_db, db
import app.patrimonios_import as imp
import app.relatorio_patrimonios as rel


def _planilha(titulo, colunas, extra=None):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append([titulo])
    for linha in zip(*colunas):
        ws.append(list(linha))
    for v in extra or []:
        ws.append([v])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture
def base():
    """Tipo "Microfone de teste" num kit (1 por kit), com estoque de 10."""
    init_db()
    s = uuid.uuid4().hex[:6]
    n = int(s[:4], 16) % 9000 + 1000          # números de 4 dígitos, únicos por teste
    with db() as conn:
        conn.execute("INSERT INTO users (username, nome, password_hash, admin, ativo) VALUES (?, 'Op Pat', 'x', 1, 1)",
                     (f"op-{s}",))
        uid = conn.execute("SELECT id FROM users WHERE username = ?", (f"op-{s}",)).fetchone()["id"]
        conn.execute("INSERT INTO item_tipo (nome, criado_em) VALUES (?, '2026-01-01')", (f"Microfone teste {s}",))
        tid = conn.execute("SELECT id FROM item_tipo WHERE nome = ?", (f"Microfone teste {s}",)).fetchone()["id"]
        conn.execute("INSERT INTO kit_template (nome, cliente, versao, ativo, tipo) VALUES (?, 'CliPat', 1, 1, 'kit')",
                     (f"Kit pat {s}",))
        kid = conn.execute("SELECT id FROM kit_template WHERE nome = ?", (f"Kit pat {s}",)).fetchone()["id"]
        conn.execute("INSERT INTO kit_template_items (kit_template_id, item_tipo_id, quantidade_exigida, obrigatorio) "
                     "VALUES (?, ?, 1, 1)", (kid, tid))
        conn.execute("INSERT INTO estoque (item_tipo_id, codigo_barra, quantidade_atual, quantidade_minima, criado_em) "
                     "VALUES (?, ?, 10, 1, '2026-01-01')", (tid, f"EST-{s}"))
    yield {"s": s, "n": n, "uid": uid, "tid": tid, "kid": kid}
    with db() as conn:
        conn.execute("DELETE FROM scan_session_items WHERE sessao_id IN (SELECT id FROM scan_session WHERE kit_template_id = ?)", (kid,))
        conn.execute("DELETE FROM estoque_movimentos WHERE estoque_id IN (SELECT id FROM estoque WHERE item_tipo_id = ?)", (tid,))
        conn.execute("DELETE FROM scan_session WHERE kit_template_id = ?", (kid,))
        conn.execute("DELETE FROM estoque WHERE item_tipo_id = ?", (tid,))
        conn.execute("DELETE FROM item_master WHERE item_tipo_id = ?", (tid,))
        conn.execute("DELETE FROM kit_template_items WHERE kit_template_id = ?", (kid,))
        conn.execute("DELETE FROM kit_template WHERE id = ?", (kid,))
        conn.execute("DELETE FROM item_tipo WHERE id = ?", (tid,))
        conn.execute("DELETE FROM users WHERE id = ?", (uid,))


def test_le_a_grade_e_ignora_titulo_cabecalho_e_data(base):
    n = base["n"]
    dados = _planilha("Controle de patrimônio Microfones",
                      [[n, n + 1, n + 2], [n + 10, n + 11, None]],
                      extra=["Patrimônio", date(2026, 11, 5), "ABC-77", 0, "000"])
    lido = imp.ler_planilha(dados)
    assert lido["titulo"] == "Controle de patrimônio Microfones"
    # coluna a coluna, como a planilha é organizada
    assert lido["codigos"] == [str(n), str(n + 1), str(n + 2), "ABC-77", str(n + 10), str(n + 11)]


def test_previa_completa_zeros_e_separa_o_que_ja_existe(base):
    n, s = base["n"], base["s"]
    with db() as conn:   # já existe SEM zeros: não pode nascer um 00xxxx ao lado
        conn.execute("INSERT INTO item_master (codigo_barra, item_tipo_id, criado_em) VALUES (?, ?, '2026-01-01')",
                     (str(n + 1), base["tid"]))
    p = imp.previa([str(n), str(n + 1), str(n), f"EST-{s}"], True)
    assert p["novos"] == [str(n).zfill(6)]
    assert [c["no_sistema"] for c in p["cadastrados"]] == [str(n + 1)]
    assert p["estoque"] == [f"EST-{s}"] and p["repetidos"] == [str(n).zfill(6)]
    assert imp.previa([str(n)], False)["novos"] == [str(n)]


def test_importa_uma_vez_e_bipagem_reconhece_e_desconta_estoque(base):
    import app.sessions as sessions
    n, tid, uid = base["n"], base["tid"], base["uid"]
    r = imp.importar([str(n), str(n + 1)], True, tid, uid, "Microfones.xlsx")
    assert r["criados"] == 2
    assert imp.importar([str(n), str(n + 1)], True, tid, uid, "Microfones.xlsx")["criados"] == 0
    with db() as conn:
        row = conn.execute("SELECT origem, origem_ref FROM item_master WHERE codigo_barra = ?", (str(n).zfill(6),)).fetchone()
        assert (row["origem"], row["origem_ref"]) == ("importacao", "Microfones.xlsx")
        conn.execute("INSERT INTO scan_session (kit_template_id, kit_template_versao, operador_id, status, garagem) "
                     "VALUES (?, 1, ?, 'em_andamento', 'G')", (base["kid"], uid))
        sid = conn.execute("SELECT MAX(id) FROM scan_session WHERE kit_template_id = ?", (base["kid"],)).fetchone()[0]

    def saldo():
        with db() as conn:
            return conn.execute("SELECT quantidade_atual FROM estoque WHERE item_tipo_id = ?", (tid,)).fetchone()[0]

    # Bipado SEM os zeros: acha o 00xxxx importado, não pergunta o tipo.
    res = sessions.register_scan(sid, str(n), operador_id=uid)
    assert res["resultado"] == "aceito", res
    assert res["codigo_barra"] == str(n).zfill(6)
    assert saldo() == 9          # primeira bipagem do importado desconta 1
    with db() as conn:
        assert conn.execute("SELECT codigo_barra FROM scan_session_items WHERE sessao_id = ?", (sid,)).fetchone()[0] \
            == str(n).zfill(6)


def test_sugere_tipo_pelo_titulo():
    tipos = [{"id": 1, "nome": "Antena multi CON 006-0009"}, {"id": 2, "nome": "Microfone de lapela"},
             {"id": 3, "nome": "Cable HDMI to micro HDMI"}]
    assert imp.sugerir_tipo("Controle de patrimônio Microfones", tipos) == 2
    assert imp.sugerir_tipo("Patrimônio Antena Externa para CVC", tipos) == 1
    assert imp.sugerir_tipo("Controle de patrimônio", tipos) is None
    # "micro" de "micro HDMI" não é "Microfones"
    assert imp.sugerir_tipo("Controle de patrimônio Microfones", [tipos[2]]) is None


def test_relatorio_mostra_origem_e_situacao_e_exporta(base):
    from fastapi.testclient import TestClient
    import main, openpyxl
    from app import usuarios
    n, tid, uid, s = base["n"], base["tid"], base["uid"], base["s"]
    imp.importar([str(n)], True, tid, uid, "Antenas.xlsx")
    linha = next(i for i in rel.listar() if i["codigo_barra"] == str(n).zfill(6))
    assert (linha["origem_chave"], linha["situacao"]) == ("importacao", "nunca_bipado")
    assert rel.filtrar([linha], origens=["bipagem"]) == []
    nome = f"adm_rpat_{s}"
    usuarios.criar("Adm RPat", nome, "Teste#RelPat26", True)
    try:
        with TestClient(main.app) as c:
            c.post("/login", data={"username": nome, "password": "Teste#RelPat26"})
            h = c.get("/reports/patrimonios", params={"busca": str(n).zfill(6), "origem": "importacao"})
            assert h.status_code == 200 and str(n).zfill(6) in h.text and "Importação" in h.text
            x = c.get("/reports/patrimonios/export", params={"busca": str(n).zfill(6)})
            ws = openpyxl.load_workbook(io.BytesIO(x.content)).active
            assert ws.cell(2, 1).value == str(n).zfill(6) and ws.cell(2, 3).value == "Importação"
            assert c.get("/admin/patrimonios/importar").status_code == 200
            p = c.post("/admin/patrimonios/importar", data={"completar_zeros": "1"},
                       files={"arquivo": ("Microfones.xlsx", _planilha("Controle Microfones", [[n + 5]]),
                                          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
            assert p.status_code == 200 and "Confira antes de importar" in p.text and str(n + 5).zfill(6) in p.text
            ok = c.post("/admin/patrimonios/importar/confirmar",
                        data={"codigos": f"{n + 5}", "completar_zeros": "1", "item_tipo_id": str(tid), "arquivo": "M.xlsx"})
            assert ok.status_code == 200 and "Importação concluída" in ok.text
    finally:
        with db() as conn:
            conn.execute("DELETE FROM item_master WHERE criado_por IN (SELECT id FROM users WHERE username = ?)", (nome,))
            conn.execute("DELETE FROM users WHERE username = ?", (nome,))
