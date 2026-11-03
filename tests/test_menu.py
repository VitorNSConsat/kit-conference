"""Menu lateral: grupos e atalhos só pra quem pode abrir; a tela aberta acende."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SECRET_KEY", "chave-de-teste-nao-usar-em-producao")

import app.menu as menu


def _ativos(caminho, query="", user=None, pode=lambda k: True):
    return [i["rotulo"] for g in menu.montar(caminho, query, user or {"admin": True}, pode)
            for i in g["itens"] if i["ativo"]]


def test_grupos_e_permissoes():
    so_bipagem = menu.montar("/", "", {"admin": False}, lambda k: k == "ver_bipagem")
    assert [g["nome"] for g in so_bipagem] == ["Operação"]
    assert [i["rotulo"] for i in so_bipagem[0]["itens"]] == ["Bipagem"]
    # Ação que exige permissão própria: sem estoque_editar, some o atalho.
    sem_editar = menu.montar("/", "", {"admin": False}, lambda k: k != "estoque_editar")
    rotulos = [i["rotulo"] for g in sem_editar for i in g["itens"]]
    assert "Ajustar estoque por planilha" not in rotulos and "Importar veículos" in rotulos
    assert "Auditoria" not in rotulos and "Padrão Visual" not in rotulos     # só admin
    assert menu.montar("/", "", None, lambda k: True) == []


def test_tela_aberta_acende_um_atalho_so():
    assert _ativos("/") == ["Bipagem"]
    assert _ativos("/session/12") == ["Bipagem"]
    assert _ativos("/admin/producao/remessas") == ["Remessas"]
    assert _ativos("/admin/items", "tab=sobressalentes") == ["Sobressalentes"]
    assert _ativos("/admin/items", "tab=catalogo") == ["Itens & Estoque"]
    assert _ativos("/admin/veiculos/import/previa/abc") == ["Importar veículos"]
    assert _ativos("/reports") == ["Kits finalizados"]
    assert _ativos("/reports/patrimonio") == ["Mudanças de patrimônio"]
    assert _ativos("/admin/hardware/7") == ["RMAs"]
    # O grupo da tela aberta vem marcado (é o que o mantém aberto).
    grupos = menu.montar("/reports/validacoes", "", {"admin": True}, lambda k: True)
    assert [g["nome"] for g in grupos if g["ativo"]] == ["Relatórios"]
