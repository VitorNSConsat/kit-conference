import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ["DB_PATH"] = ":memory:"

from fastapi.testclient import TestClient

from database import init_db, db
import main
from app import usuarios

USUARIO = "teste_portal"
SENHA = "Teste#Portal2026"


def test_menu_lateral_tem_link_para_o_portal():
    # O portal fica na 8001 (HTTP) do mesmo host em que o usuário está — o
    # acesso dos notebooks é http://<ip>:8080.
    init_db()
    usuarios.criar("Teste Portal", USUARIO, SENHA, True)
    try:
        with TestClient(main.app, base_url="http://192.168.1.232:8080") as c:
            r = c.post("/login", data={"username": USUARIO, "password": SENHA},
                       follow_redirects=False)
            assert r.status_code == 302
            pagina = c.get("/", follow_redirects=True)
            assert pagina.status_code == 200
            assert 'href="http://192.168.1.232:8001/"' in pagina.text
            assert "<span>Portal</span>" in pagina.text
    finally:
        with db() as conn:
            conn.execute("DELETE FROM users WHERE username = ?", (USUARIO,))
