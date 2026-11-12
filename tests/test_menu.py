"""Menu lateral: grupos por assunto, atalhos só pra quem pode abrir; a tela
aberta acende um atalho só."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SECRET_KEY", "chave-de-teste-nao-usar-em-producao")

import app.menu as menu


def _ativos(caminho, query="", user=None, pode=lambda k: True):
    return [i["rotulo"] for g in menu.montar(caminho, query, user or {"admin": True}, pode)
            for i in menu.atalhos(g) if i["ativo"]]


def _rotulos(user, pode):
    return [i["rotulo"] for g in menu.montar("/", "", user, pode) for i in menu.atalhos(g)]


def test_grupos_por_assunto():
    assert [g["nome"] for g in menu.montar("/", "", {"admin": True}, lambda k: True)] == [
        "Bipagem", "Fila de impressão", "Acompanhamento", "Remessas", "Painéis", "Estoque", "Kits e pedidos",
        "Veículos e clientes", "RMA",
        "Importações", "Downloads", "Relatórios", "Sistema"]


def test_permissoes():
    so_bipagem = menu.montar("/", "", {"admin": False}, lambda k: k == "ver_bipagem")
    # O certificado do celular não depende de tela: vai pra todo mundo.
    assert [g["nome"] for g in so_bipagem] == ["Bipagem", "Downloads"]
    assert so_bipagem[0]["direto"] and so_bipagem[0]["href"] == "/"
    assert [i["rotulo"] for i in so_bipagem[0]["itens"]] == ["Bipagem"]
    assert [i["rotulo"] for i in so_bipagem[1]["itens"]] == ["Certificado do celular"]
    # Ação que exige permissão própria: sem estoque_editar, some o atalho.
    sem_editar = menu.montar("/", "", {"admin": False}, lambda k: k != "estoque_editar")
    rotulos = [i["rotulo"] for g in sem_editar for i in menu.atalhos(g)]
    assert "Ajustar estoque por planilha" not in rotulos and "Importar veículos" in rotulos
    assert "Auditoria" not in rotulos and "Padrão visual" not in rotulos     # só admin
    assert "Usuários" not in rotulos
    assert "Usuários" in _rotulos({"admin": True}, lambda k: True)
    assert _ativos("/admin/usuarios") == ["Usuários"]
    assert menu.montar("/", "", None, lambda k: True) == []


def test_tela_aberta_acende_um_atalho_so():
    assert _ativos("/") == ["Bipagem"]
    assert _ativos("/session/12") == ["Bipagem"]
    assert _ativos("/admin/producao") == ["Acompanhar produção"]
    assert _ativos("/admin/producao/remessas") == ["Remessas"]
    pain = {g["nome"]: g for g in menu.montar("/admin/producao", "", {"admin": True}, lambda k: True)}["Painéis"]
    assert [i["rotulo"] for i in pain["itens"]] == ["Painel da produção (TV)", "Prateleira", "Painel da prateleira"]
    assert _ativos("/print-queue") == ["Fila de impressão"] and _ativos("/admin/prateleira") == ["Prateleira"]
    g = {x["nome"]: x for x in menu.montar("/admin/producao", "", {"admin": True}, lambda k: True)}
    assert g["Acompanhamento"]["direto"] and g["Acompanhamento"]["ativo"] and not g["Remessas"]["ativo"]
    assert not g["Painéis"]["ativo"] and g["Fila de impressão"]["direto"]
    assert _ativos("/admin/items") == ["Estoque de itens"]
    assert _ativos("/admin/items", "tab=sobressalentes") == ["Sobressalentes"]
    g = {x["nome"]: x for x in menu.montar("/admin/items", "tab=sobressalentes", {"admin": True}, lambda k: True)}
    assert g["Kits e pedidos"]["ativo"] and not g["Estoque"]["ativo"]
    assert "Sobressalentes" not in [i["rotulo"] for i in menu.atalhos(g["Estoque"])]
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
    d = list(menu.atalhos(g["Downloads"]))
    assert all(i["tipo"] == "download" for i in d)
    assert {"Planilha de ajuste de estoque", "Estoque completo", "Modelo de veículos",
            "Certificado do celular"} <= {i["rotulo"] for i in d}
    tv = [i["href"] for gr in g.values() for i in menu.atalhos(gr) if i["tipo"] == "tv"]
    assert tv == ["/producao/tv", "/prateleira/tv"]


def test_submenus_agrupam_por_assunto_e_somem_com_um_item_so():
    g = {x["nome"]: x for x in menu.montar("/reports/patrimonios", "", {"admin": True}, lambda k: True)}
    subs = {i["rotulo"]: i for i in g["Relatórios"]["itens"] if i.get("sub")}
    assert [i["rotulo"] for i in subs["Patrimônio"]["itens"]] == ["Patrimônios cadastrados", "Mudanças de patrimônio"]
    assert subs["Patrimônio"]["ativo"] and not subs["Kits e produção"]["ativo"]
    assert subs["Patrimônio"]["chave"] == "relatórios/patrimônio"
    # O servidor já entrega aberto só o caminho da tela atual (sanfona).
    assert g["Relatórios"]["sub_ativo"] and not g["Estoque"]["sub_ativo"]
    assert [x for x in g if g[x]["ativo"]] == ["Relatórios"]
    # Sem admin, em Sistema → Configurações sobram Rede e Backup (continua submenu);
    # só com "ver_rede", sobra um: vira atalho solto.
    so_rede = {x["nome"]: x for x in menu.montar("/", "", {"admin": False}, lambda k: k == "ver_rede")}
    assert [(i["rotulo"], bool(i.get("sub"))) for i in so_rede["Sistema"]["itens"]] == [("Rede e acesso", False)]


def test_barra_do_topo_colunas_caminho_e_ir_para():
    g = {x["nome"]: x for x in menu.montar("/reports/pre-check", "", {"admin": True}, lambda k: True)}
    # Nome curto só onde 13 entradas dividem a barra; o nome inteiro segue no grupo.
    assert g["Fila de impressão"]["curto"] == "Impressão" and g["Veículos e clientes"]["curto"] == "Veículos"
    assert g["Relatórios"]["curto"] == "Relatórios"
    # Submenu vira coluna com título; atalhos soltos ao lado ganham o nome do grupo.
    cols = g["Relatórios"]["colunas"]
    assert [c["titulo"] for c in cols] == ["Kits e produção", "Patrimônio", "Relatórios"]
    assert [i["rotulo"] for i in cols[2]["itens"]] == ["Sobressalentes enviados", "Auditoria"]
    # Grupo sem submenu: uma coluna sem título. Entrada direta: nenhuma.
    assert [c["titulo"] for c in g["Kits e pedidos"]["colunas"]] == [""]
    assert g["Bipagem"]["colunas"] == []
    # Caminho acima do título: grupo › submenu da tela aberta; entrada direta não tem.
    assert menu.trilha(list(g.values())) == ["Relatórios", "Kits e produção"]
    assert menu.trilha(menu.montar("/", "", {"admin": True}, lambda k: True)) == []
    assert menu.trilha(menu.montar("/admin/hardware", "", {"admin": True}, lambda k: True)) == ["RMA"]
    # Ir para: todas as telas que o usuário pode abrir, com o lugar no menu.
    todos = menu.ir_para(list(g.values()))
    por_nome = {i["rotulo"]: i for i in todos}
    assert por_nome["Pré-check de kits"]["onde"] == "Relatórios › Kits e produção"
    assert por_nome["Bipagem"]["onde"] == "" and por_nome["Estoque completo"]["tipo"] == "download"
    assert len(todos) == sum(1 for x in g.values() for _ in menu.atalhos(x))
    so_bip = menu.ir_para(menu.montar("/", "", {"admin": False}, lambda k: k == "ver_bipagem"))
    assert [i["rotulo"] for i in so_bip] == ["Bipagem", "Certificado do celular"]
