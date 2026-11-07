"""Menu lateral: grupos por assunto, atalhos só pra quem pode abrir; a tela
aberta acende um atalho só."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SECRET_KEY", "chave-de-teste-nao-usar-em-producao")

import app.menu as menu


def _ativos(caminho, query="", user=None, pode=lambda k: True):
    return [i["rotulo"] for g in menu.montar(caminho, query, user or {"admin": True}, pode)
            for i in g["itens"] if i["ativo"]]


def test_grupos_por_assunto():
    assert [g["nome"] for g in menu.montar("/", "", {"admin": True}, lambda k: True)] == [
        "Produção", "Estoque", "Kits e pedidos", "Veículos e clientes", "RMA",
        "Importações", "Downloads", "Relatórios", "Sistema"]


def test_permissoes():
    so_bipagem = menu.montar("/", "", {"admin": False}, lambda k: k == "ver_bipagem")
    # O certificado do celular não depende de tela: vai pra todo mundo.
    assert [g["nome"] for g in so_bipagem] == ["Produção", "Downloads"]
    assert [i["rotulo"] for i in so_bipagem[0]["itens"]] == ["Bipagem"]
    assert [i["rotulo"] for i in so_bipagem[1]["itens"]] == ["Certificado do celular"]
    # Ação que exige permissão própria: sem estoque_editar, some o atalho.
    sem_editar = menu.montar("/", "", {"admin": False}, lambda k: k != "estoque_editar")
    rotulos = [i["rotulo"] for g in sem_editar for i in g["itens"]]
    assert "Ajustar estoque por planilha" not in rotulos and "Importar veículos" in rotulos
    assert "Auditoria" not in rotulos and "Padrão visual" not in rotulos     # só admin
    assert "Usuários" not in rotulos
    assert "Usuários" in [i["rotulo"] for g in menu.montar("/", "", {"admin": True}, lambda k: True) for i in g["itens"]]
    assert _ativos("/admin/usuarios") == ["Usuários"]
    assert menu.montar("/", "", None, lambda k: True) == []


def test_tela_aberta_acende_um_atalho_so():
    assert _ativos("/") == ["Bipagem"]
    assert _ativos("/session/12") == ["Bipagem"]
    assert _ativos("/admin/producao") == ["Acompanhar produção"]
    assert _ativos("/admin/producao/remessas") == ["Remessas"]
    assert _ativos("/admin/items") == ["Estoque de itens"]
    assert _ativos("/admin/items", "tab=sobressalentes") == ["Sobressalentes"]
    assert _ativos("/admin/items", "tab=patrimonios") == ["Patrimônios"]
    assert _ativos("/admin/items/patrimonio/ABC") == ["Patrimônios"]
    assert _ativos("/admin/templates") == ["Kits"]
    assert _ativos("/admin/templates", "tab=pedido") == ["Pedidos"]
    assert _ativos("/admin/clientes/3") == ["Veículos"]
    assert _ativos("/producao/tv") == []               # painel abre em aba nova, não "acende"
    assert _ativos("/admin/veiculos/import/previa/abc") == ["Importar veículos"]
    assert _ativos("/reports") == ["Kits finalizados"]
    assert _ativos("/reports/patrimonio") == ["Mudanças de patrimônio"]
    assert _ativos("/admin/hardware/7") == ["Ocorrências de RMA"]
    assert _ativos("/admin/patrimonios/importar") == ["Importar patrimônios"]
    assert _ativos("/reports/patrimonios") == ["Patrimônios cadastrados"]
    assert _ativos("/reports/patrimonio") == ["Mudanças de patrimônio"]
    grupos = menu.montar("/reports/validacoes", "", {"admin": True}, lambda k: True)
    assert [g["nome"] for g in grupos if g["ativo"]] == ["Relatórios"]


def test_downloads_centralizados():
    g = {x["nome"]: x for x in menu.montar("/", "", {"admin": True}, lambda k: True)}
    d = g["Downloads"]["itens"]
    assert all(i["tipo"] == "download" for i in d)
    assert {"Planilha de ajuste de estoque", "Estoque completo", "Modelo de veículos",
            "Certificado do celular"} <= {i["rotulo"] for i in d}
    tv = [i["href"] for gr in g.values() for i in gr["itens"] if i["tipo"] == "tv"]
    assert tv == ["/producao/tv", "/prateleira/tv"]
