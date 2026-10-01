"""Zerar sequência (SEQ da etiqueta Em Andamento): botão na barra de cima da
Produção, liberado pra qualquer usuário; a exibição da TV continua só admin."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import uuid

import pytest

os.environ["DB_PATH"] = ":memory:"
os.environ.setdefault("SECRET_KEY", "chave-de-teste-nao-usar-em-producao")

from fastapi.testclient import TestClient

from database import init_db, db
import main
import app.producao as producao_mod
from app import usuarios

SENHA = "Teste#Seq2026"


@pytest.fixture
def operador():
    init_db()
    nome = f"op_seq_{uuid.uuid4().hex[:6]}"
    usuarios.criar("Operador Seq", nome, SENHA, False)
    yield nome
    with db() as conn:
        uid = conn.execute("SELECT id FROM users WHERE username = ?", (nome,)).fetchone()["id"]
        conn.execute("DELETE FROM user_permissoes_negadas WHERE user_id = ?", (uid,))
        conn.execute("DELETE FROM users WHERE id = ?", (uid,))


def test_usuario_comum_ve_o_valor_atual_e_zera(operador):
    with db() as conn:
        conn.execute("UPDATE producao_sequencia SET valor = 540 WHERE id = 1")
    with TestClient(main.app) as c:
        c.post("/login", data={"username": operador, "password": SENHA})
        h = c.get("/admin/producao").text
        assert 'id="popup-zerar-seq"' in h and ">0540<" in h
        # Exibição da TV é configuração de admin: nem botão nem janela pro operador.
        assert 'popup-tv-config' not in h
        r = c.post("/admin/producao/zerar-sequencia", follow_redirects=False)
        assert r.status_code == 302 and "sequencia_zerada" in r.headers["location"]
    assert producao_mod.valor_sequencia() == 0
