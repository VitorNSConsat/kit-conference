"""Menu do sistema (barra do topo; no celular, a gaveta lateral): os grupos e
os atalhos de cada um.

O menu é o lugar central do sistema: além das telas principais, leva direto
ao que antes só se achava por dentro de outras telas (importações, downloads,
relatórios, remessas, painéis de TV…). As telas, por sua vez, deixaram de
repetir esses botões no topo — só ficam lá as ações da própria tela e as
exportações que levam os FILTROS da tela (essas não têm como morar no menu).

Cada atalho só aparece pra quem pode abrir o destino — a regra é a MESMA do
porteiro das rotas (permissão da tela) mais a permissão de ação quando a tela
pede uma (ex.: ajustar estoque exige estoque_editar).

`ativo` diz se o atalho é a tela aberta agora: casa pelo caminho e, quando o
atalho aponta pra uma aba (`?tab=…`), pela aba também.

Dentro de um grupo, atalhos do mesmo assunto ficam num SUBMENU (`_sub`), que
abre e fecha. Submenu que, pelas permissões, sobrar com um atalho só vira
atalho solto — submenu de um item não organiza nada.
"""
from urllib.parse import urlsplit, parse_qs


def _a(rotulo, icone, href, perms=(), prefixos=(), excluir=(), admin=False, tipo=""):
    """Um atalho. `tipo`: "" (tela), "download" (baixa um arquivo) ou
    "tv" (abre numa aba nova — painel pra deixar numa tela/TV)."""
    return {"rotulo": rotulo, "icone": icone, "href": href, "perms": list(perms),
            "prefixos": list(prefixos), "excluir": list(excluir), "admin": admin, "tipo": tipo}


def _sub(rotulo, icone, itens):
    """Submenu dentro de um grupo: um título que abre/fecha e os atalhos."""
    return {"sub": True, "rotulo": rotulo, "icone": icone, "itens": list(itens)}


# Organizado por ASSUNTO, com o nome que o operador usa. Antes "Cadastros"
# misturava veículo, peça, kit e RMA — agora cada assunto tem o seu grupo.
_GRUPOS = [
    # Entradas DIRETAS (no lugar da lista de atalhos vem um atalho só): as telas
    # do dia a dia, que abrem num clique, no mesmo nível dos grupos.
    ("Bipagem", "bipagem", _a("Bipagem", "bipagem", "/", ["ver_bipagem"], ["/", "/session"])),
    ("Fila de impressão", "impressao",
     _a("Fila de impressão", "impressao", "/print-queue", ["ver_impressao"], ["/print-queue"])),
    ("Acompanhamento", "atividade",
     _a("Acompanhar produção", "atividade", "/admin/producao", ["ver_producao"], ["/admin/producao", "/producao"],
        ["/admin/producao/remessas", "/admin/producao/historico", "/producao/tv"])),
    ("Remessas", "producao", _a("Remessas", "producao", "/admin/producao/remessas", ["ver_producao"],
                             ["/admin/producao/remessas"])),
    # Telas pra deixar abertas numa TV/monitor e a prateleira.
    ("Painéis", "monitor", [
        _a("Painel da produção (TV)", "monitor", "/producao/tv", ["ver_producao"], tipo="tv"),
        _a("Prateleira", "prateleira", "/admin/prateleira", ["ver_prateleira"], ["/admin/prateleira"]),
        _a("Painel da prateleira", "monitor", "/prateleira/tv", ["ver_prateleira"], tipo="tv"),
    ]),
    ("Estoque", "itens", [
        _sub("Itens", "itens", [
            _a("Estoque de itens", "itens", "/admin/items?tab=catalogo", ["ver_itens"],
               ["/admin/items", "/admin/tipos", "/admin/estoque"]),
            _a("Cadastrar item", "mais", "/admin/items?tab=novo", ["ver_itens"], ["/admin/items"]),
            _a("Etiquetas e códigos", "impressao", "/admin/items?tab=codigos", ["ver_itens"], ["/admin/items"]),
        ]),
        _a("Patrimônios", "clipboard", "/admin/items?tab=patrimonios", ["ver_itens"], ["/admin/items"]),
    ]),
    ("Kits e pedidos", "kits", [
        _a("Kits", "kits", "/admin/templates?tab=kit", ["ver_kits"], ["/admin/templates"]),
        _a("Pedidos", "arquivo", "/admin/templates?tab=pedido", ["ver_kits"], ["/admin/templates"]),
        _a("Conjuntos", "caixa", "/admin/templates?tab=conjuntos", ["ver_kits"], ["/admin/templates"]),
        _a("Sobressalentes", "caixa", "/admin/items?tab=sobressalentes", ["ver_itens"], ["/admin/items"]),
    ]),
    ("Veículos e clientes", "veiculos", [
        _sub("Veículos", "veiculos", [
            _a("Veículos", "veiculos", "/admin/veiculos", ["ver_veiculos"],
               ["/admin/veiculos", "/admin/clientes", "/admin/garagens"],
               ["/admin/veiculos/import", "/admin/veiculos/importacoes"]),
            _a("Novo veículo", "mais", "/admin/veiculos#novo", ["ver_veiculos"]),
        ]),
        _sub("Clientes e garagens", "cliente", [
            _a("Clientes", "cliente", "/admin/veiculos#clientes", ["ver_veiculos"]),
            _a("Garagens", "garagem", "/admin/veiculos#garagens", ["ver_veiculos"]),
        ]),
    ]),
    ("RMA", "alerta", [
        _a("Ocorrências de RMA", "alerta", "/admin/hardware", ["ver_hardware"], ["/admin/hardware"],
           ["/admin/hardware/importar", "/admin/hardware/importacoes", "/admin/hardware/produtos"]),
        _a("Produtos de RMA", "clipboard", "/admin/hardware/produtos", ["ver_hardware"], ["/admin/hardware/produtos"]),
    ]),
    ("Importações", "importar", [
        _sub("Veículos", "veiculos", [
            _a("Importar veículos", "importar", "/admin/veiculos/import", ["ver_veiculos"], ["/admin/veiculos/import"]),
            _a("Importações de veículos", "relogio", "/admin/veiculos/importacoes", ["ver_veiculos"], ["/admin/veiculos/importacoes"]),
        ]),
        _sub("RMA", "alerta", [
            _a("Importar RMAs", "importar", "/admin/hardware/importar", ["ver_hardware"], ["/admin/hardware/importar"]),
            _a("Importações de RMAs", "relogio", "/admin/hardware/importacoes", ["ver_hardware"], ["/admin/hardware/importacoes"]),
        ]),
        _sub("Estoque e patrimônios", "itens", [
            _a("Ajustar estoque por planilha", "importar", "/admin/items?tab=catalogo#importar-ajuste",
               ["ver_itens", "estoque_editar"]),
            _a("Importar itens (BOM)", "importar", "/admin/items?tab=novo#importar-bom", ["ver_itens"]),
            _a("Importar patrimônios", "importar", "/admin/patrimonios/importar", ["ver_itens", "estoque_editar"],
               ["/admin/patrimonios/importar"]),
        ]),
        _sub("Kits e pedidos", "kits", [
            _a("Criar kit pela BOM", "importar", "/admin/templates?tab=kit#bom", ["ver_kits"]),
            _a("Importar pedido", "importar", "/admin/templates?tab=pedido#importar-pedido",
               ["ver_kits", "pedidos_criar_editar"]),
        ]),
    ]),
    ("Downloads", "baixar", [
        # Planilhas pra preencher e devolver em Importações.
        _sub("Modelos para preencher", "arquivo", [
            _a("Modelo de veículos", "arquivo", "/admin/veiculos/modelo.xlsx", ["ver_veiculos"], tipo="download"),
            _a("Modelo de RMAs", "arquivo", "/admin/hardware/modelo.xlsx", ["ver_hardware"], tipo="download"),
            _a("Planilha de ajuste de estoque", "arquivo", "/admin/estoque/ajuste-modelo.xlsx",
               ["ver_itens", "estoque_editar"], tipo="download"),
        ]),
        # Exportações COMPLETAS (sem filtro). Com filtro, cada tela segue
        # exportando o que está mostrando.
        _sub("Exportações completas", "baixar", [
            _a("Estoque completo", "baixar", "/admin/estoque/exportar.xlsx", ["ver_itens"], tipo="download"),
            _a("Todos os veículos", "baixar", "/admin/veiculos/exportar.xlsx", ["ver_veiculos"], tipo="download"),
            _a("Todas as RMAs", "baixar", "/admin/hardware/exportar.xlsx", ["ver_hardware"], tipo="download"),
            _a("Todos os kits finalizados", "baixar", "/reports/exportar-todos.xlsx", ["ver_relatorios"], tipo="download"),
            _a("Sobressalentes enviados", "baixar", "/reports/sobressalentes/export", ["ver_relatorios"], tipo="download"),
            _a("Histórico da produção", "baixar", "/admin/producao/historico/exportar.xlsx", ["ver_producao"], tipo="download"),
        ]),
        _a("Certificado do celular", "cadeado", "/cert", tipo="download"),
    ]),
    ("Relatórios", "relatorios", [
        _sub("Kits e produção", "kits", [
            _a("Kits finalizados", "relatorios", "/reports", ["ver_relatorios"]),
            _a("Kits por operador", "usuarios", "/reports/operadores", ["ver_relatorios"], ["/reports/operadores"]),
            _a("Verificações", "check-circulo", "/reports/validacoes", ["ver_relatorios"], ["/reports/validacoes"]),
            _a("Pré-check de kits", "clipboard", "/reports/pre-check", ["ver_relatorios"], ["/reports/pre-check"]),
            _a("Histórico da produção", "historico", "/admin/producao/historico", ["ver_producao"], ["/admin/producao/historico"]),
        ]),
        _sub("Patrimônio", "clipboard", [
            _a("Patrimônios cadastrados", "clipboard", "/reports/patrimonios", ["ver_relatorios"], ["/reports/patrimonios"]),
            _a("Mudanças de patrimônio", "historico", "/reports/patrimonio", ["ver_relatorios"], ["/reports/patrimonio"]),
        ]),
        _a("Sobressalentes enviados", "caixa", "/reports/sobressalentes", ["ver_relatorios"], ["/reports/sobressalentes"]),
        _a("Auditoria", "cadeado", "/admin/auditoria", [], ["/admin/auditoria"], admin=True),
    ]),
    ("Sistema", "funcionalidades", [
        _a("Manual do sistema", "funcionalidades", "/funcionalidades", ["ver_funcionalidades"], ["/funcionalidades"]),
        _sub("Configurações", "cadeado", [
            # Usuários, permissões e o tempo de inatividade que encerra o login.
            _a("Usuários", "usuarios", "/admin/usuarios", [], ["/admin/usuarios"], admin=True),
            _a("Rede e acesso", "rede", "/rede", ["ver_rede"], ["/rede"]),
            _a("Backup", "backup", "/admin/backup", ["ver_backup"], ["/admin/backup"]),
            _a("Padrão visual", "paleta", "/admin/padrao-visual", [], ["/admin/padrao-visual"], admin=True),
        ]),
    ]),
]

# Nome curto na barra do topo, onde 13 entradas dividem uma linha só. O nome
# inteiro continua na dica do mouse, na gaveta do celular e no "Ir para".
_CURTO = {"Fila de impressão": "Impressão", "Veículos e clientes": "Veículos"}

# Aba que a tela abre quando o link não diz qual (pra acender o atalho certo).
_ABA_PADRAO = {"/admin/items": "catalogo", "/admin/templates": "kit"}

_RELATORIOS_PROPRIOS = ("operadores", "validacoes", "sobressalentes", "patrimonio", "patrimonios", "pre-check")


def _casa(caminho: str, prefixo: str) -> bool:
    if prefixo == "/":
        return caminho == "/" or caminho.startswith("/session")
    return caminho == prefixo or caminho.startswith(prefixo + "/")


def _aba_atual(caminho: str, aba: str) -> str:
    if aba:
        return aba
    if caminho.startswith("/admin/items/patrimonio"):
        return "patrimonios"            # página de um patrimônio é da aba Patrimônios
    if caminho.startswith(("/admin/tipos", "/admin/estoque")):
        return "catalogo"
    for prefixo, padrao in _ABA_PADRAO.items():
        if caminho == prefixo or caminho.startswith(prefixo + "/"):
            return padrao
    return ""


def _ativo(item: dict, caminho: str, aba: str) -> bool:
    if item["tipo"]:
        return False                 # download e painel de TV não são "a tela aberta"
    destino = urlsplit(item["href"])
    if destino.fragment:
        return False                 # trecho de outra tela: quem acende é a tela dona
    aba_destino = (parse_qs(destino.query).get("tab") or [""])[0]
    if item["href"] == "/reports":
        return caminho == "/reports" or (caminho.startswith("/reports/")
                                         and caminho.split("/")[2] not in _RELATORIOS_PROPRIOS)
    if item["prefixos"]:
        casa = (any(_casa(caminho, p) for p in item["prefixos"])
                and not any(_casa(caminho, e) for e in item["excluir"]))
    else:
        casa = caminho == destino.path
    return casa and (not aba_destino or aba == aba_destino)


def atalhos(grupo: dict):
    """Os atalhos de um grupo já montado, com os de dentro dos submenus."""
    for i in grupo["itens"]:
        if i.get("sub"):
            yield from i["itens"]
        else:
            yield i


def _chave(texto: str) -> str:
    return texto.lower().replace(" ", "-")


def montar(caminho: str, query: str, user: dict | None, pode) -> list[dict]:
    """[{nome, icone, chave, ativo, itens:[atalho | submenu]}] — só os grupos
    com algum atalho que este usuário pode abrir. Entrada direta: o mesmo, com
    `direto` e `href` (um atalho só, aberto num clique). Atalho: {rotulo, icone,
    href, ativo, tipo}; submenu: {sub, rotulo, icone, chave, ativo, itens}."""
    if not user:
        return []
    aba = _aba_atual(caminho, (parse_qs(query or "").get("tab") or [""])[0])

    def visivel(it):
        return not (it["admin"] and not user.get("admin")) and all(pode(p) for p in it["perms"])

    def saida(it):
        return {"rotulo": it["rotulo"], "icone": it["icone"], "href": it["href"],
                "tipo": it["tipo"], "ativo": _ativo(it, caminho, aba)}

    grupos = []
    for nome, icone_grupo, entradas in _GRUPOS:
        if isinstance(entradas, dict):              # entrada direta: um atalho só
            if visivel(entradas):
                it = saida(entradas)
                grupos.append({"nome": nome, "icone": icone_grupo, "chave": _chave(nome), "direto": True,
                               "href": it["href"], "itens": [it]})
            continue
        linhas = []
        for e in entradas:
            if e.get("sub"):
                filhos = [saida(it) for it in e["itens"] if visivel(it)]
                if len(filhos) >= 2:
                    linhas.append({"sub": True, "rotulo": e["rotulo"], "icone": e["icone"],
                                   "chave": f"{_chave(nome)}/{_chave(e['rotulo'])}", "itens": filhos})
                else:
                    linhas.extend(filhos)          # sobrou um só: vira atalho solto
            elif visivel(e):
                linhas.append(saida(e))
        if linhas:
            grupos.append({"nome": nome, "icone": icone_grupo, "chave": _chave(nome), "itens": linhas})
    # A mesma tela com atalhos por aba (Itens & Estoque e as abas dela): a aba
    # com atalho próprio acende sozinha; o atalho geral, só nas outras abas.
    abas_com_atalho = {(urlsplit(i["href"]).path, (parse_qs(urlsplit(i["href"]).query).get("tab") or [""])[0])
                       for g in grupos for i in atalhos(g) if i["ativo"]}
    for g in grupos:
        for i in atalhos(g):
            d = urlsplit(i["href"])
            if i["ativo"] and not d.query and (d.path, aba) in abas_com_atalho and aba:
                i["ativo"] = False
        for i in g["itens"]:
            if i.get("sub"):
                i["ativo"] = any(x["ativo"] for x in i["itens"])
        g["ativo"] = any(i["ativo"] for i in atalhos(g))
        g["sub_ativo"] = any(i.get("sub") and i["ativo"] for i in g["itens"])
        g["curto"] = _CURTO.get(g["nome"], g["nome"])
        g["colunas"] = _colunas(g)
    return grupos


def _colunas(grupo: dict) -> list[dict]:
    """Colunas do painel que abre na barra do topo: cada submenu vira uma
    coluna com título; atalhos soltos seguidos ficam juntos numa coluna (com o
    nome do grupo como título quando há submenus do lado, pra não ficar órfã)."""
    if grupo.get("direto"):
        return []
    cols, soltos = [], []
    for i in grupo["itens"]:
        if i.get("sub"):
            if soltos:
                cols.append({"titulo": "", "icone": "", "itens": soltos})
                soltos = []
            cols.append({"titulo": i["rotulo"], "icone": i["icone"], "itens": i["itens"]})
        else:
            soltos.append(i)
    if soltos:
        cols.append({"titulo": "", "icone": "", "itens": soltos})
    if len(cols) > 1:
        for c in cols:
            if not c["titulo"]:
                c["titulo"], c["icone"] = grupo["nome"], grupo["icone"]
    return cols


def trilha(grupos: list[dict]) -> list[str]:
    """Onde a tela aberta mora no menu, pro caminho acima do título
    (ex.: ["Relatórios", "Kits e produção"]). Entrada direta não tem caminho."""
    for g in grupos:
        if not g["ativo"] or g.get("direto"):
            continue
        for i in g["itens"]:
            if i.get("sub") and i["ativo"]:
                return [g["nome"], i["rotulo"]]
        return [g["nome"]]
    return []


def ir_para(grupos: list[dict]) -> list[dict]:
    """Todas as telas que o usuário pode abrir, pra busca "Ir para…" (Ctrl K):
    rótulo, destino, tipo e onde fica no menu."""
    saida = []
    for g in grupos:
        for i in g["itens"]:
            filhos = i["itens"] if i.get("sub") else [i]
            if g.get("direto"):
                onde = ""
            else:
                onde = " › ".join([g["nome"]] + ([i["rotulo"]] if i.get("sub") else []))
            for f in filhos:
                saida.append({"rotulo": f["rotulo"], "href": f["href"], "tipo": f["tipo"],
                              "icone": f["icone"], "onde": onde})
    return saida
