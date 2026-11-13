"""Pacote do front-end (Padrão visual → Baixar): o .zip leva a cara do sistema
sem as regras dele, e o que vai dentro funciona sozinho."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SECRET_KEY", "chave-de-teste-nao-usar-em-producao")

import io
import json
import types
import zipfile

import jinja2

import app.kit_frontend as kit


def _zip():
    return zipfile.ZipFile(io.BytesIO(kit.montar_zip()))


def test_pacote_tem_os_arquivos_do_front():
    nomes = set(_zip().namelist())
    for arq in ("README.md", "static/style.css", "static/logo.png", "templates/base.html",
                "templates/_icones.html", "app/menu.py", "tokens.json",
                "exemplo/app.py", "exemplo/templates/inicio.html"):
        assert "frontend-consat/" + arq in nomes, arq
    css = _zip().read("frontend-consat/static/style.css").decode("utf-8")
    assert css == open(os.path.join(kit.RAIZ, "static", "style.css"), encoding="utf-8").read()


def test_base_generico_sem_o_que_e_deste_sistema_e_renderiza():
    base = kit.base_generico()
    for especifico in ("stock-alert-box", "abrirPainelVeiculo", "abrirPainelPatrimonio",
                       "salvarRegiaoVeiculo", "Leitores de código de barras", "kit:especifico"):
        assert especifico not in base, especifico
    for casca in ('class="ds-topo-nav"', 'class="ds-sidebar"', 'id="ds-irpara"', "function abrirPopup",
                  "function alternarMega", "_ajustarBarraTopo", "function abrirJanelaPainel"):
        assert casca in base, casca
    # Renderiza com o contexto que o README pede (e o menu de exemplo).
    menu = types.ModuleType("menu_exemplo")
    exec(compile(kit.menu_generico(), "menu.py", "exec"), menu.__dict__)
    user = {"id": 1, "nome": "Ana Souza", "admin": False}
    grupos = menu.montar("/produtos", "", user, lambda k: True)
    env = jinja2.Environment(loader=jinja2.DictLoader({
        "base.html": base,
        "_icones.html": open(os.path.join(kit.RAIZ, "templates", "_icones.html"), encoding="utf-8").read(),
        "tela.html": '{% extends "base.html" %}{% block title %}Produtos{% endblock %}{% block content %}<p>ok</p>{% endblock %}',
    }))
    req = types.SimpleNamespace(url=types.SimpleNamespace(hostname="10.0.0.5", path="/produtos"))
    html = env.get_template("tela.html").render(request=req, user=user, pode=lambda k: True, menu_grupos=grupos,
                                                menu_trilha=menu.trilha(grupos), menu_ir_para=menu.ir_para(grupos))
    assert "<h1>Produtos</h1>" in html and "<p>ok</p>" in html
    assert 'title="Cadastros"' in html and "Lista de produtos" in html and ">AS</span>" in html
    assert '<p class="ds-trilha"><span>Cadastros</span>' in html          # caminho: Cadastros › Produtos
    assert "http://10.0.0.5:8001/" in html                                # botão do Portal
    assert "Usuários" not in html                                          # admin=True e o usuário não é admin


def test_menu_de_exemplo_e_tokens():
    m = kit.menu_generico()
    assert '("Cadastros", "kits", [' in m and "Bipagem" not in m and "_CURTO = {}" in m
    t = json.loads(_zip().read("frontend-consat/tokens.json"))
    assert t["claro"]["--brand"] == "#2a5fd8" and t["escuro"]["--bg"].startswith("#")
    assert set(t["claro"]) >= {"--surface", "--text", "--radius-card", "--shadow-pop"}
    assert "bipagem" in kit.icones() and "atividade" in kit.icones()


def test_download_so_pra_admin():
    from fastapi.testclient import TestClient
    import main
    from app import usuarios
    from database import db
    usuarios.criar("Adm Kit", "adm_kit_front", "Teste#Kit26", True)
    usuarios.criar("Op Kit", "op_kit_front", "Teste#Kit26", False)
    try:
        with TestClient(main.app) as c:
            c.post("/login", data={"username": "op_kit_front", "password": "Teste#Kit26"})
            r = c.get("/admin/padrao-visual/pacote-frontend.zip", follow_redirects=False)
            assert r.status_code != 200 or r.headers.get("content-type") != "application/zip"
        with TestClient(main.app) as c:
            c.post("/login", data={"username": "adm_kit_front", "password": "Teste#Kit26"})
            r = c.get("/admin/padrao-visual/pacote-frontend.zip")
            assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
            assert "frontend-consat.zip" in r.headers["content-disposition"]
            h = c.get("/admin/padrao-visual").text
            assert 'href="/admin/padrao-visual/pacote-frontend.zip"' in h and 'class="esteira"' in h
    finally:
        with db() as conn:
            conn.execute("DELETE FROM users WHERE username IN ('adm_kit_front', 'op_kit_front')")
