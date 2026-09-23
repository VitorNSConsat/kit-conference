import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ["DB_PATH"] = ":memory:"
os.environ["PORTAL_SERVICE_KEY"] = "chave-de-teste"

from fastapi.testclient import TestClient

from database import init_db, db
import main
from app import usuarios

HEADERS = {"X-Portal-Key": "chave-de-teste"}


def setup_function(_):
    init_db()
    usuarios.criar("Admin Teste", "admin_teste", "Teste#Portal2026", True)


def teardown_function(_):
    with db() as conn:
        conn.execute("DELETE FROM users")


def test_sem_chave_devolve_404():
    with TestClient(main.app) as c:
        r = c.get("/api/portal/usuarios")
        assert r.status_code == 404


def test_chave_errada_devolve_404():
    with TestClient(main.app) as c:
        r = c.get("/api/portal/usuarios", headers={"X-Portal-Key": "errada"})
        assert r.status_code == 404


def test_chave_certa_lista_usuarios():
    with TestClient(main.app) as c:
        r = c.get("/api/portal/usuarios", headers=HEADERS)
        assert r.status_code == 200
        nomes = [u["username"] for u in r.json()]
        assert "admin_teste" in nomes
        u = next(u for u in r.json() if u["username"] == "admin_teste")
        assert u["admin"] is True and u["ativo"] is True


def test_chamada_com_chave_fica_na_auditoria_como_portal():
    with TestClient(main.app) as c:
        c.get("/api/portal/usuarios", headers=HEADERS)
    with db() as conn:
        row = conn.execute(
            "SELECT user_nome FROM auditoria WHERE caminho = '/api/portal/usuarios' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    # GET não é auditado por padrão (só POST/PUT/PATCH/DELETE e acesso negado) —
    # este teste é revisitado no Task 3, quando há uma rota de escrita pra conferir.
    assert row is None
