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


def test_criar_usuario():
    with TestClient(main.app) as c:
        r = c.post("/api/portal/usuarios", headers=HEADERS,
                   json={"nome": "Novo", "username": "novo_teste", "senha": "Teste#Portal2026", "admin": False})
        assert r.status_code == 200, r.text
        uid = r.json()["id"]
        u = next(x for x in usuarios.listar() if x["id"] == uid)
        assert u["username"] == "novo_teste" and not u["admin"]


def test_criar_usuario_com_login_repetido_e_400():
    with TestClient(main.app) as c:
        c.post("/api/portal/usuarios", headers=HEADERS,
              json={"nome": "A", "username": "dup_teste", "senha": "Teste#Portal2026", "admin": False})
        r = c.post("/api/portal/usuarios", headers=HEADERS,
                   json={"nome": "B", "username": "dup_teste", "senha": "Teste#Portal2026", "admin": False})
        assert r.status_code == 400


def test_editar_usuario_nome_e_admin():
    with TestClient(main.app) as c:
        uid = usuarios.criar("Editar", "editar_teste", "Teste#Portal2026", False)
        r = c.put(f"/api/portal/usuarios/{uid}", headers=HEADERS,
                  json={"nome": "Editado", "admin": True, "ativo": True})
        assert r.status_code == 200, r.text
        u = usuarios.buscar(uid)
        assert u["nome"] == "Editado" and u["admin"]


def test_nao_deixa_desativar_o_ultimo_admin():
    with TestClient(main.app) as c:
        with db() as conn:
            conn.execute("DELETE FROM users WHERE username != 'admin_teste'")
        uid = next(u["id"] for u in usuarios.listar() if u["username"] == "admin_teste")
        r = c.put(f"/api/portal/usuarios/{uid}", headers=HEADERS,
                  json={"nome": "Admin Teste", "admin": False, "ativo": True})
        assert r.status_code == 400


def test_editar_usuario_rejeitado_nao_deixa_o_nome_mudado():
    # Reprodução do bug: demote do último admin ativo junto com uma troca de
    # nome tinha que devolver 400 SEM deixar o nome mudado (renomear() era
    # chamado primeiro, com commit próprio, antes de definir_admin() estourar).
    with TestClient(main.app) as c:
        with db() as conn:
            conn.execute("DELETE FROM users WHERE username != 'admin_teste'")
        uid = next(u["id"] for u in usuarios.listar() if u["username"] == "admin_teste")
        r = c.put(f"/api/portal/usuarios/{uid}", headers=HEADERS,
                  json={"nome": "Nome Novo", "admin": False, "ativo": True})
        assert r.status_code == 400
    u = usuarios.buscar(uid)
    assert u["nome"] == "Admin Teste"


def test_editar_usuario_detalhe_de_auditoria_mostra_o_que_mudou():
    with TestClient(main.app) as c:
        uid = usuarios.criar("Antes", "detalhe_teste", "Teste#Portal2026", False)
        r = c.put(f"/api/portal/usuarios/{uid}", headers=HEADERS,
                  json={"nome": "Depois", "admin": True, "ativo": True})
        assert r.status_code == 200, r.text
    with db() as conn:
        row = conn.execute(
            "SELECT detalhe FROM auditoria WHERE caminho = ? AND metodo = 'PUT' "
            "ORDER BY id DESC LIMIT 1", (f"/api/portal/usuarios/{uid}",)
        ).fetchone()
    assert "Antes" in row["detalhe"] and "Depois" in row["detalhe"]
    assert "administrador" in row["detalhe"]


def test_alterar_permissoes_detalhe_de_auditoria_mostra_o_diff():
    with TestClient(main.app) as c:
        uid = usuarios.criar("Perm", "perm_detalhe_teste", "Teste#Portal2026", False)
        from app import permissoes as permissoes_mod
        todas_menos_uma = set(permissoes_mod.PERMISSOES) - {"veiculos_excluir"}
        r = c.put(f"/api/portal/usuarios/{uid}/permissoes", headers=HEADERS,
                  json={"permitidas": list(todas_menos_uma)})
        assert r.status_code == 200, r.text
    with db() as conn:
        row = conn.execute(
            "SELECT detalhe FROM auditoria WHERE caminho = ? AND metodo = 'PUT' "
            "ORDER BY id DESC LIMIT 1", (f"/api/portal/usuarios/{uid}/permissoes",)
        ).fetchone()
    assert "negou" in row["detalhe"]
    assert "Excluir veículos" in row["detalhe"]


def test_trocar_senha():
    with TestClient(main.app) as c:
        uid = usuarios.criar("Senha", "senha_teste", "Teste#Portal2026", False)
        r = c.post(f"/api/portal/usuarios/{uid}/senha", headers=HEADERS, json={"senha": "OutraSenha#2026"})
        assert r.status_code == 200, r.text


def test_escrita_com_chave_fica_na_auditoria_como_portal():
    with TestClient(main.app) as c:
        c.post("/api/portal/usuarios", headers=HEADERS,
              json={"nome": "Audit", "username": "audit_teste", "senha": "Teste#Portal2026", "admin": False})
    with db() as conn:
        row = conn.execute(
            "SELECT user_nome, detalhe FROM auditoria WHERE caminho = '/api/portal/usuarios' "
            "AND metodo = 'POST' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row["user_nome"] == "Portal"
    assert "senha" not in row["detalhe"] or "***" in row["detalhe"]


# ── Fix 1: compare_digest não pode estourar 500 com valor fora do ASCII ───────

def test_chave_configurada_com_acento_e_chave_errada_devolve_404(monkeypatch):
    monkeypatch.setenv("PORTAL_SERVICE_KEY", "chave-ção")
    with TestClient(main.app) as c:
        r = c.get("/api/portal/usuarios", headers={"X-Portal-Key": "errada"})
    assert r.status_code == 404


def test_cabecalho_com_acento_e_chave_configurada_ascii_devolve_404():
    # PORTAL_SERVICE_KEY continua "chave-de-teste" (ASCII); só o cabeçalho
    # recebido tem acento. httpx recusa mandar valor de cabeçalho fora do
    # ASCII como `str` (o próprio cliente barra antes de sair da máquina) —
    # por bytes já latin-1, contorna essa checagem do cliente e chega no
    # servidor do jeitinho que um pedido real de terceiro chegaria: Starlette
    # decodifica cabeçalho como latin-1, então o "é" sobrevive.
    with TestClient(main.app) as c:
        r = c.get("/api/portal/usuarios",
                  headers={"X-Portal-Key": "chave-é".encode("latin-1")})
    assert r.status_code == 404


# ── Fix 2: a garantia de 404 vale pra QUALQUER pedido malformado ──────────────

def test_metodo_sem_rota_e_sem_chave_devolve_404_nao_405():
    with TestClient(main.app) as c:
        r = c.delete("/api/portal/usuarios")
    assert r.status_code == 404


def test_corpo_json_invalido_e_sem_chave_devolve_404_nao_422():
    with TestClient(main.app) as c:
        r = c.post("/api/portal/usuarios", content="{bad",
                   headers={"Content-Type": "application/json"})
    assert r.status_code == 404


def test_metodo_sem_rota_mas_com_chave_certa_devolve_405():
    # O middleware só barra quando a chave está errada/ausente — com a
    # chave certa, o pedido segue pro roteamento normal do FastAPI, que aí
    # sim responde 405 (rota existe, método não).
    with TestClient(main.app) as c:
        r = c.delete("/api/portal/usuarios", headers=HEADERS)
    assert r.status_code == 405


def test_chave_certa_continua_chegando_na_rota_atraves_do_middleware():
    with TestClient(main.app) as c:
        r = c.get("/api/portal/usuarios", headers=HEADERS)
    assert r.status_code == 200


# ── Fix 5: chave de permissão desconhecida não pode ser aceita em silêncio ────

def test_permissao_desconhecida_devolve_400_e_nao_muda_nada():
    with TestClient(main.app) as c:
        uid = usuarios.criar("Permissao", "permissao_teste", "Teste#Portal2026", False)
        antes = c.get(f"/api/portal/usuarios/{uid}/permissoes", headers=HEADERS).json()["permitidas"]
        r = c.put(f"/api/portal/usuarios/{uid}/permissoes", headers=HEADERS,
                  json={"permitidas": ["lixo_inexistente"]})
        assert r.status_code == 400
        depois = c.get(f"/api/portal/usuarios/{uid}/permissoes", headers=HEADERS).json()["permitidas"]
        assert antes == depois
