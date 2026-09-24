"""Pacote de sobressalentes (o "kit coringa"): desconta o estoque na criação,
liga cada baixa ao pacote, é tudo ou nada e guarda o histórico."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import uuid

import pytest

os.environ["DB_PATH"] = ":memory:"
os.environ.setdefault("SECRET_KEY", "chave-de-teste-nao-usar-em-producao")

from database import init_db, db
import app.sobressalentes as sob
import app.estoque as estoque_mod
import app.zpl as zpl


def _limpar(conn):
    for t in ("sobressalente_pacote_evento", "sobressalente_pacote_item", "sobressalente_pacote",
              "estoque_movimentos", "estoque", "item_tipo"):
        conn.execute(f"DELETE FROM {t}")


@pytest.fixture
def cenario():
    init_db()
    username = f"op-{uuid.uuid4().hex[:8]}"
    with db() as conn:
        _limpar(conn)
        conn.execute("INSERT INTO users (username, nome, password_hash, admin, ativo) "
                     "VALUES (?, 'Op Teste', 'x', 1, 1)", (username,))
        uid = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()["id"]
        conn.execute("INSERT INTO item_tipo (id, nome, ativo) VALUES (1, 'Antena', 1), (2, 'Cabo', 1)")
        conn.execute("INSERT INTO estoque (id, item_tipo_id, codigo_barra, quantidade_atual, quantidade_minima) "
                     "VALUES (1, 1, 'ANT-1', 10, 1), (2, 2, 'CAB-1', 3, 1)")
    yield uid
    with db() as conn:
        _limpar(conn)
        conn.execute("DELETE FROM users WHERE id = ?", (uid,))


def _saldo(eid):
    with db() as conn:
        return conn.execute("SELECT quantidade_atual FROM estoque WHERE id = ?", (eid,)).fetchone()[0]


def test_criar_desconta_estoque_e_liga_as_baixas_ao_pacote(cenario):
    pid = sob.criar([{"estoque_id": 1, "quantidade": 4, "observacao": "urgente"},
                     {"estoque_id": 2, "quantidade": 2}], "REDEMOB", cenario, nome="Reposição Sul")
    assert (_saldo(1), _saldo(2)) == (6, 1)
    p = sob.buscar(pid)
    assert p["rotulo"] == f"SOB-{pid:04d}" and p["nome"] == "Reposição Sul"
    assert p["total_unidades"] == 6 and {i["tipo_nome"] for i in p["itens"]} == {"Antena", "Cabo"}
    movs = estoque_mod.listar_sobressalentes(cliente="REDEMOB")
    assert len(movs) == 2 and all(m["pacote_id"] == pid and m["pacote_rotulo"] == p["rotulo"] for m in movs)
    assert all(m["observacao"].startswith(p["rotulo"]) for m in movs)
    assert [e["tipo"] for e in p["eventos"]] == ["criado"]


def test_falta_de_estoque_nao_cria_pacote_nem_desconta(cenario):
    with pytest.raises(ValueError):
        sob.criar([{"estoque_id": 1, "quantidade": 2}, {"estoque_id": 2, "quantidade": 9}], "REDEMOB", cenario)
    assert (_saldo(1), _saldo(2)) == (10, 3)
    assert sob.listar("REDEMOB") == []


def test_cliente_e_obrigatorio(cenario):
    with pytest.raises(ValueError):
        sob.criar([{"estoque_id": 1, "quantidade": 1}], "  ", cenario)


def test_etiqueta_registra_evento_e_lista_resume(cenario):
    pid = sob.criar([{"estoque_id": 1, "quantidade": 2}], "ClienteA", cenario)
    sob.registrar_etiqueta(pid, cenario)
    assert [e["tipo"] for e in sob.buscar(pid)["eventos"]] == ["etiqueta", "criado"]
    lista = sob.listar("ClienteA")
    assert len(lista) == 1 and lista[0]["total_unidades"] == 2 and lista[0]["total_itens"] == 1
    assert sob.listar("OutroCliente") == []


def test_etiqueta_sobressalente_usa_o_qr_e_formato_do_kit():
    html = zpl.generate_sobressalente_html_label("SOB-0001", "http://x/sobressalente/1")
    assert "100mm 150mm" in html and "SOB-0001" in html
    assert "width:70mm;height:70mm" in html and 'class="barcode-img"' in html
    assert "SOBRESSALENTES" not in html


def test_leitor_resolve_url_rotulo_e_numero(cenario):
    import main
    pid = sob.criar([{"estoque_id": 1, "quantidade": 1}], "REDEMOB", cenario)
    assert main._resolver_sobressalente_id(f"http://x/sobressalente/{pid}") == pid
    assert main._resolver_sobressalente_id(f"SOB-{pid:04d}") == pid
    assert main._resolver_sobressalente_id(f" sob-{pid} ") == pid
    assert main._resolver_sobressalente_id(str(pid)) == pid
    assert main._resolver_sobressalente_id("SOB-9999") is None
    assert main._resolver_sobressalente_id("banana") is None
    assert main._resolver_sobressalente_id("") is None


def test_lista_filtra_por_varios_clientes_e_traz_resumo_dos_itens(cenario):
    a = sob.criar([{"estoque_id": 1, "quantidade": 2}, {"estoque_id": 2, "quantidade": 1}], "CliA", cenario, nome="Sul")
    b = sob.criar([{"estoque_id": 1, "quantidade": 1}], "CliB", cenario)
    c = sob.criar([{"estoque_id": 1, "quantidade": 1}], "CliC", cenario)
    todos = sob.listar()
    assert [p["id"] for p in todos] == [c, b, a]              # mais recente primeiro
    dois = sob.listar(["CliA", "CliB"])
    assert {p["id"] for p in dois} == {a, b}
    assert sob.listar("CliC")[0]["id"] == c                   # texto solto continua valendo
    pa = [p for p in todos if p["id"] == a][0]
    assert pa["total_unidades"] == 3 and pa["total_itens"] == 2
    assert "Antena ×2" in pa["itens_texto"] and "Cabo ×1" in pa["itens_texto"]
    assert set(sob.clientes_com_pacote()) == {"CliA", "CliB", "CliC"}


def test_etiquetas_em_lote_uma_por_folha():
    html = zpl.generate_sobressalente_html_labels_lote(
        [{"rotulo": f"SOB-000{n}", "url_qr": f"http://x/sobressalente/{n}"} for n in (1, 2, 3)])
    assert html.count('class="folha"') == 3 and "page-break-after: always" in html
    assert all(f"SOB-000{n}" in html for n in (1, 2, 3))
