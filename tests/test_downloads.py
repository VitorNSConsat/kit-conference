"""Downloads: a planilha grande não trava o servidor, a janela de progresso
consegue acompanhar o que ele está fazendo, e os Sobressalentes enviados
estão em Downloads com tudo que foi em cada envio."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import io
import uuid

import openpyxl
import pytest

os.environ["DB_PATH"] = ":memory:"
os.environ.setdefault("SECRET_KEY", "chave-de-teste-nao-usar-em-producao")

from database import init_db, db
import app.downloads as downloads
import app.planilhas as planilhas
import app.menu as menu_mod
import app.sobressalentes as sob


def _abrir(conteudo: bytes):
    return openpyxl.load_workbook(io.BytesIO(conteudo))


# ── Andamento em memória ────────────────────────────────────────────────────

def test_progresso_registra_etapas_e_contagem_sem_encher_o_log():
    p = downloads.Progresso("dl-" + uuid.uuid4().hex)
    p.etapa("Lendo os kits")
    for n in range(0, 5000, 1000):
        p.etapa("Montando a aba Detalhes", n, 5000, registrar=False)
    p.etapa("Fechando o arquivo")
    e = downloads.ler(p.ident)
    assert e["log"] == ["Lendo os kits", "Fechando o arquivo"]
    assert e["etapa"] == "Fechando o arquivo"


def test_sem_id_nao_registra_nada():
    p = downloads.Progresso("")
    p.etapa("qualquer coisa")
    assert downloads.ler("") is None


# ── Planilha rápida ─────────────────────────────────────────────────────────

def test_planilha_texto_continua_texto_e_numero_continua_numero():
    pl = planilhas.Planilha()
    pl.aba("Teste", ["A", "B", "C", "D"], [10, 10, 10, 10],
           [["=SOMA(1;2)", "0012", 7, None], ["http://x", "", 2.5, "ok"]])
    ws = _abrir(pl.conteudo())["Teste"]
    assert [c.value for c in ws[1]] == ["A", "B", "C", "D"]
    assert ws["A2"].value == "=SOMA(1;2)" and ws["A2"].data_type == "s"   # não vira fórmula
    assert ws["B2"].value == "0012"                                        # não perde o zero
    assert ws["C2"].value == 7 and ws["C3"].value == 2.5
    assert ws.freeze_panes == "A2"
    assert ws["A1"].fill.fgColor.rgb.endswith("1A3A5C")                    # cabeçalho azul de sempre


# ── Rotas ───────────────────────────────────────────────────────────────────

@pytest.fixture
def cliente():
    from fastapi.testclient import TestClient
    import main
    from app import usuarios
    init_db()
    nome = f"adm_dl_{uuid.uuid4().hex[:6]}"
    usuarios.criar("Adm Downloads", nome, "Teste#Dl2026", True)
    with db() as conn:
        conn.execute("INSERT OR IGNORE INTO item_tipo (id, nome, ativo) VALUES (901, 'Antena DL', 1)")
        conn.execute("INSERT OR IGNORE INTO estoque (id, item_tipo_id, codigo_barra, quantidade_atual, "
                     "quantidade_minima) VALUES (901, 901, 'ANT-DL', 10, 1)")
    with TestClient(main.app) as c:
        c.post("/login", data={"username": nome, "password": "Teste#Dl2026"})
        yield c
    with db() as conn:
        uid = conn.execute("SELECT id FROM users WHERE username = ?", (nome,)).fetchone()["id"]
        pacotes = [r[0] for r in conn.execute(
            "SELECT id FROM sobressalente_pacote WHERE criado_por = ?", (uid,))]
        for t in ("sobressalente_pacote_evento", "sobressalente_pacote_item"):
            conn.executemany(f"DELETE FROM {t} WHERE pacote_id = ?", [(x,) for x in pacotes])
        conn.execute("DELETE FROM estoque_movimentos WHERE criado_por = ?", (uid,))
        conn.execute("DELETE FROM sobressalente_pacote WHERE criado_por = ?", (uid,))
        conn.execute("DELETE FROM users WHERE id = ?", (uid,))


def test_kits_finalizados_conta_as_etapas_pra_janela(cliente):
    ident = "dl-" + uuid.uuid4().hex
    r = cliente.get("/reports/exportar-todos.xlsx", headers={"X-Download-Id": ident})
    assert r.status_code == 200 and "spreadsheetml" in r.headers["content-type"]
    assert r.headers.get("content-encoding") == "identity"     # xlsx já é zip
    assert {"Resumo", "Detalhes"} <= set(_abrir(r.content).sheetnames)
    andamento = cliente.get(f"/downloads/progresso/{ident}").json()
    assert andamento["log"][0] == "Procurando os kits finalizados"
    assert andamento["etapa"].startswith("Planilha pronta")
    assert cliente.get("/downloads/progresso/nao-existe").json() == {"etapa": None, "log": []}


def test_estoque_completo_sai_pela_planilha_rapida_fora_do_loop(cliente):
    import main
    assert main.admin_estoque_exportar.__wrapped__.__wrapped__   # @require_login → @_em_thread
    r = cliente.get("/admin/estoque/exportar.xlsx")
    wb = _abrir(r.content)
    assert wb.sheetnames == ["Estoque", "Histórico"]
    linhas = [[c.value for c in row] for row in wb["Estoque"].iter_rows(min_row=2)]
    assert ["Antena DL", "ANT-DL", 10, 1, "OK"] in [l[:5] for l in linhas]


def test_sobressalentes_enviados_no_menu_downloads_com_cada_item(cliente):
    grupos = menu_mod.montar("/", "", {"id": 1, "admin": True}, lambda k: True)
    downloads_grupo = next(g for g in grupos if g["nome"] == "Downloads")
    hrefs = [a["href"] for s in downloads_grupo["itens"] for a in (s.get("itens") or [s])]
    assert "/reports/sobressalentes/export" in hrefs

    with db() as conn:
        uid = conn.execute("SELECT id FROM users WHERE nome = 'Adm Downloads' "
                           "ORDER BY id DESC LIMIT 1").fetchone()["id"]
    pid = sob.criar([{"estoque_id": 901, "quantidade": 2, "observacao": "na caixa azul"}],
                    "CLIENTE DL", uid, nome="Reposição DL")
    r = cliente.get("/reports/sobressalentes/export")
    ws = _abrir(r.content)["Itens"]
    cab = [c.value for c in ws[1]]
    assert cab[:2] == ["Envio", "Nome do pacote"] and cab[-1] == "Observação do item"
    linha = next(l for l in ([c.value for c in row] for row in ws.iter_rows(min_row=2))
                 if l[0] == sob.rotulo(pid))
    assert linha[1] == "Reposição DL" and linha[4] == "Antena DL" and linha[6] == 2
    assert "na caixa azul" in linha[-1]


def test_janela_de_downloads_esta_na_casca(cliente):
    h = cliente.get("/").text
    assert "window.baixarComProgresso" in h and "X-Download-Id" in h
    assert "'/cert'" in h          # certificado continua abrindo direto no navegador
