"""Criar um template para vários clientes gera uma cópia por cliente."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import uuid

import pytest

os.environ["DB_PATH"] = ":memory:"
os.environ.setdefault("SECRET_KEY", "chave-de-teste-nao-usar-em-producao")

from database import init_db, db
import app.kit_templates as tpl


@pytest.fixture
def base():
    init_db()
    sufixo = uuid.uuid4().hex[:6]
    with db() as conn:
        conn.execute("INSERT INTO users (username, nome, password_hash, admin, ativo) VALUES (?, 'U', 'x', 1, 1)",
                     (f"u-{sufixo}",))
        uid = conn.execute("SELECT id FROM users WHERE username = ?", (f"u-{sufixo}",)).fetchone()["id"]
        conn.execute("INSERT INTO item_tipo (nome, ativo) VALUES (?, 1)", (f"Tipo {sufixo}",))
        tid = conn.execute("SELECT id FROM item_tipo WHERE nome = ?", (f"Tipo {sufixo}",)).fetchone()["id"]
    yield {"uid": uid, "tid": tid, "nome": f"Kit {sufixo}"}
    with db() as conn:
        ids = [r["id"] for r in conn.execute("SELECT id FROM kit_template WHERE nome = ?", (f"Kit {sufixo}",))]
        for i in ids:
            conn.execute("DELETE FROM kit_template_items WHERE kit_template_id = ?", (i,))
            conn.execute("DELETE FROM kit_template WHERE id = ?", (i,))
        conn.execute("DELETE FROM item_tipo WHERE id = ?", (tid,))
        conn.execute("DELETE FROM users WHERE id = ?", (uid,))


def test_um_template_por_cliente_com_os_mesmos_itens(base):
    itens = [{"item_tipo_id": base["tid"], "quantidade_exigida": 3}]
    ids = tpl.criar_templates(base["nome"], ["Alfa", " Beta ", "Alfa", ""], base["uid"], itens)
    assert len(ids) == 2
    clientes = [tpl.buscar_template(i)["cliente"] for i in ids]
    assert clientes == ["Alfa", "Beta"]
    for i in ids:
        assert [(x["item_tipo_id"], x["quantidade_exigida"]) for x in tpl.get_itens_template(i)] == [(base["tid"], 3)]


def test_sem_cliente_e_recusado(base):
    with pytest.raises(ValueError):
        tpl.criar_templates(base["nome"], [], base["uid"], [{"item_tipo_id": base["tid"], "quantidade_exigida": 1}])


def test_aceita_cliente_unico_como_texto(base):
    ids = tpl.criar_templates(base["nome"], "Unico", base["uid"], [{"item_tipo_id": base["tid"], "quantidade_exigida": 1}])
    assert len(ids) == 1 and tpl.buscar_template(ids[0])["cliente"] == "Unico"
