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
        conn.execute("DELETE FROM user_permissoes_negadas")
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


def test_catalogo_de_permissoes():
    with TestClient(main.app) as c:
        r = c.get("/api/portal/permissoes/catalogo", headers=HEADERS)
        assert r.status_code == 200
        grupos = dict(r.json()["grupos"])
        assert "veiculos_excluir" in dict(grupos["Veículos"])


def test_permissoes_de_um_usuario_novo_comecam_todas_permitidas():
    with TestClient(main.app) as c:
        uid = usuarios.criar("Comum", "comum_teste", "Teste#Portal2026", False)
        r = c.get(f"/api/portal/usuarios/{uid}/permissoes", headers=HEADERS)
        assert r.status_code == 200
        assert "veiculos_excluir" in r.json()["permitidas"]


def test_alterar_permissoes_de_um_usuario():
    with TestClient(main.app) as c:
        uid = usuarios.criar("Comum2", "comum_teste2", "Teste#Portal2026", False)
        from app import permissoes as permissoes_mod
        todas_menos_uma = set(permissoes_mod.PERMISSOES) - {"veiculos_excluir"}
        r = c.put(f"/api/portal/usuarios/{uid}/permissoes", headers=HEADERS,
                  json={"permitidas": list(todas_menos_uma)})
        assert r.status_code == 200
        r2 = c.get(f"/api/portal/usuarios/{uid}/permissoes", headers=HEADERS)
        assert "veiculos_excluir" not in r2.json()["permitidas"]
