import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

os.environ["DB_PATH"] = ":memory:"

from database import init_db, db
import app.producao as producao_mod


@pytest.fixture(autouse=True)
def setup_db():
    init_db()
    with db() as conn:
        conn.executescript("""
            DELETE FROM kit_record;
            DELETE FROM scan_session;
            DELETE FROM kit_template;
            DELETE FROM users;
        """)
        conn.execute(
            "INSERT INTO users (id, nome, username, password_hash) VALUES (1, 'Teste', 'teste', 'x')"
        )
        conn.execute(
            "INSERT INTO kit_template (id, nome, cliente, versao, tipo) VALUES (1, 'Kit X', 'Cliente X', 1, 'kit')"
        )
        conn.execute(
            "INSERT INTO scan_session (id, kit_template_id, kit_template_versao, operador_id) VALUES (1, 1, 1, 1)"
        )
        for kit_id, status in [("K1", "cliente_instalando"), ("K2", "cliente_instalando"),
                                ("K3", "cliente_concluido"), ("K4", "transito")]:
            conn.execute(
                "INSERT INTO kit_record (kit_id, sessao_id, kit_template_id, kit_template_versao, "
                "operador_id, status_producao) VALUES (?, 1, 1, 1, 1, ?)",
                (kit_id, status)
            )
    yield
    with db() as conn:
        conn.executescript("""
            DELETE FROM kit_record;
            DELETE FROM scan_session;
            DELETE FROM kit_template;
            DELETE FROM users;
        """)


def test_marca_em_lote_so_os_que_estao_instalando():
    n = producao_mod.marcar_cliente_concluido_lote(["K1", "K2", "K3", "K4"])
    assert n == 2  # só K1 e K2 estavam em cliente_instalando
    with db() as conn:
        status = {r["kit_id"]: r["status_producao"] for r in
                   conn.execute("SELECT kit_id, status_producao FROM kit_record").fetchall()}
    assert status["K1"] == "cliente_concluido"
    assert status["K2"] == "cliente_concluido"
    assert status["K3"] == "cliente_concluido"  # já estava, não muda
    assert status["K4"] == "transito"  # não estava instalando, ignorado


def test_lista_vazia_nao_faz_nada():
    assert producao_mod.marcar_cliente_concluido_lote([]) == 0
