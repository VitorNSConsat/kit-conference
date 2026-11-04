"""Menu lateral: os grupos e os atalhos de cada um.

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
"""
from urllib.parse import urlsplit, parse_qs


def _a(rotulo, icone, href, perms=(), prefixos=(), excluir=(), admin=False, tipo=""):
    """Um atalho. `tipo`: "" (tela), "download" (baixa um arquivo) ou
    "tv" (abre numa aba nova — painel pra deixar numa tela/TV)."""
    return {"rotulo": rotulo, "icone": icone, "href": href, "perms": list(perms),
            "prefixos": list(prefixos), "excluir": list(excluir), "admin": admin, "tipo": tipo}


# Organizado por ASSUNTO, com o nome que o operador usa. Antes "Cadastros"
# misturava veículo, peça, kit e RMA — agora cada assunto tem o seu grupo.
_GRUPOS = [
    ("Produção", "producao", [
        _a("Bipagem", "bipagem", "/", ["ver_bipagem"], ["/", "/session"]),
        _a("Fila de impressão", "impressao", "/print-queue", ["ver_impressao"], ["/print-queue"]),
        _a("Acompanhar produção", "producao", "/admin/producao", ["ver_producao"], ["/admin/producao", "/producao"],
           ["/admin/producao/remessas", "/admin/producao/historico", "/producao/tv"]),
        _a("Remessas", "caixa", "/admin/producao/remessas", ["ver_producao"], ["/admin/producao/remessas"]),
        _a("Painel da TV", "monitor", "/producao/tv", ["ver_producao"], tipo="tv"),
    ]),
    ("Estoque", "itens", [
        _a("Estoque de itens", "itens", "/admin/items?tab=catalogo", ["ver_itens"],
           ["/admin/items", "/admin/tipos", "/admin/estoque"]),
        _a("Cadastrar item", "mais", "/admin/items?tab=novo", ["ver_itens"], ["/admin/items"]),
        _a("Patrimônios", "clipboard", "/admin/items?tab=patrimonios", ["ver_itens"], ["/admin/items"]),
        _a("Etiquetas e códigos", "impressao", "/admin/items?tab=codigos", ["ver_itens"], ["/admin/items"]),
        _a("Sobressalentes", "caixa", "/admin/items?tab=sobressalentes", ["ver_itens"], ["/admin/items"]),
        _a("Prateleira", "prateleira", "/admin/prateleira", ["ver_prateleira"], ["/admin/prateleira"]),
        _a("Painel da prateleira", "monitor", "/prateleira/tv", ["ver_prateleira"], tipo="tv"),
    ]),
    ("Kits e pedidos", "kits", [
        _a("Kits", "kits", "/admin/templates?tab=kit", ["ver_kits"], ["/admin/templates"]),
        _a("Pedidos", "arquivo", "/admin/templates?tab=pedido", ["ver_kits"], ["/admin/templates"]),
        _a("Conjuntos", "caixa", "/admin/templates?tab=conjuntos", ["ver_kits"], ["/admin/templates"]),
    ]),
    ("Veículos e clientes", "veiculos", [
        _a("Veículos", "veiculos", "/admin/veiculos", ["ver_veiculos"],
           ["/admin/veiculos", "/admin/clientes", "/admin/garagens"],
           ["/admin/veiculos/import", "/admin/veiculos/importacoes"]),
        _a("Novo veículo", "mais", "/admin/veiculos#novo", ["ver_veiculos"]),
        _a("Clientes", "cliente", "/admin/veiculos#clientes", ["ver_veiculos"]),
        _a("Garagens", "garagem", "/admin/veiculos#garagens", ["ver_veiculos"]),
    ]),
    ("RMA", "alerta", [
        _a("Ocorrências de RMA", "alerta", "/admin/hardware", ["ver_hardware"], ["/admin/hardware"],
           ["/admin/hardware/importar", "/admin/hardware/importacoes", "/admin/hardware/produtos"]),
        _a("Produtos de RMA", "clipboard", "/admin/hardware/produtos", ["ver_hardware"], ["/admin/hardware/produtos"]),
    ]),
    ("Importações", "importar", [
        _a("Importar veículos", "importar", "/admin/veiculos/import", ["ver_veiculos"], ["/admin/veiculos/import"]),
        _a("Importações de veículos", "relogio", "/admin/veiculos/importacoes", ["ver_veiculos"], ["/admin/veiculos/importacoes"]),
        _a("Importar RMAs", "importar", "/admin/hardware/importar", ["ver_hardware"], ["/admin/hardware/importar"]),
        _a("Importações de RMAs", "relogio", "/admin/hardware/importacoes", ["ver_hardware"], ["/admin/hardware/importacoes"]),
        _a("Ajustar estoque por planilha", "importar", "/admin/items?tab=catalogo#importar-ajuste",
           ["ver_itens", "estoque_editar"]),
        _a("Importar itens (BOM)", "importar", "/admin/items?tab=novo#importar-bom", ["ver_itens"]),
        _a("Criar kit pela BOM", "importar", "/admin/templates?tab=kit#bom", ["ver_kits"]),
        _a("Importar pedido", "importar", "/admin/templates?tab=pedido#importar-pedido",
           ["ver_kits", "pedidos_criar_editar"]),
    ]),
    ("Downloads", "baixar", [
        # Planilhas pra preencher e devolver em Importações.
        _a("Modelo de veículos", "arquivo", "/admin/veiculos/modelo.xlsx", ["ver_veiculos"], tipo="download"),
        _a("Modelo de RMAs", "arquivo", "/admin/hardware/modelo.xlsx", ["ver_hardware"], tipo="download"),
        _a("Planilha de ajuste de estoque", "arquivo", "/admin/estoque/ajuste-modelo.xlsx",
           ["ver_itens", "estoque_editar"], tipo="download"),
        # Exportações COMPLETAS (sem filtro). Com filtro, cada tela segue
        # exportando o que está mostrando.
        _a("Estoque completo", "baixar", "/admin/estoque/exportar.xlsx", ["ver_itens"], tipo="download"),
        _a("Todos os veículos", "baixar", "/admin/veiculos/exportar.xlsx", ["ver_veiculos"], tipo="download"),
        _a("Todas as RMAs", "baixar", "/admin/hardware/exportar.xlsx", ["ver_hardware"], tipo="download"),
        _a("Todos os kits finalizados", "baixar", "/reports/exportar-todos.xlsx", ["ver_relatorios"], tipo="download"),
        _a("Histórico da produção", "baixar", "/admin/producao/historico/exportar.xlsx", ["ver_producao"], tipo="download"),
        _a("Certificado do celular", "cadeado", "/cert", tipo="download"),
    ]),
    ("Relatórios", "relatorios", [
        _a("Kits finalizados", "relatorios", "/reports", ["ver_relatorios"]),
        _a("Kits por operador", "usuarios", "/reports/operadores", ["ver_relatorios"], ["/reports/operadores"]),
        _a("Verificações", "check-circulo", "/reports/validacoes", ["ver_relatorios"], ["/reports/validacoes"]),
        _a("Sobressalentes enviados", "caixa", "/reports/sobressalentes", ["ver_relatorios"], ["/reports/sobressalentes"]),
        _a("Mudanças de patrimônio", "historico", "/reports/patrimonio", ["ver_relatorios"], ["/reports/patrimonio"]),
        _a("Pré-check de kits", "clipboard", "/reports/pre-check", ["ver_relatorios"], ["/reports/pre-check"]),
        _a("Histórico da produção", "historico", "/admin/producao/historico", ["ver_producao"], ["/admin/producao/historico"]),
        _a("Auditoria", "cadeado", "/admin/auditoria", [], ["/admin/auditoria"], admin=True),
    ]),
    ("Sistema", "funcionalidades", [
        _a("Manual do sistema", "funcionalidades", "/funcionalidades", ["ver_funcionalidades"], ["/funcionalidades"]),
        _a("Rede e acesso", "rede", "/rede", ["ver_rede"], ["/rede"]),
        _a("Backup", "backup", "/admin/backup", ["ver_backup"], ["/admin/backup"]),
        _a("Padrão visual", "paleta", "/admin/padrao-visual", [], ["/admin/padrao-visual"], admin=True),
    ]),
]

# Aba que a tela abre quando o link não diz qual (pra acender o atalho certo).
_ABA_PADRAO = {"/admin/items": "catalogo", "/admin/templates": "kit"}

_RELATORIOS_PROPRIOS = ("operadores", "validacoes", "sobressalentes", "patrimonio", "pre-check")


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


def montar(caminho: str, query: str, user: dict | None, pode) -> list[dict]:
    """[{nome, icone, chave, ativo, itens:[{rotulo, icone, href, ativo, tipo}]}] —
    só os grupos com algum atalho que este usuário pode abrir."""
    if not user:
        return []
    aba = _aba_atual(caminho, (parse_qs(query or "").get("tab") or [""])[0])
    grupos = []
    for nome, icone_grupo, itens in _GRUPOS:
        visiveis = []
        for it in itens:
            if it["admin"] and not user.get("admin"):
                continue
            if any(not pode(p) for p in it["perms"]):
                continue
            visiveis.append({"rotulo": it["rotulo"], "icone": it["icone"], "href": it["href"],
                             "tipo": it["tipo"], "ativo": _ativo(it, caminho, aba)})
        if visiveis:
            grupos.append({"nome": nome, "icone": icone_grupo,
                           "chave": nome.lower().replace(" ", "-"), "itens": visiveis})
    # A mesma tela com atalhos por aba (Itens & Estoque e as abas dela): a aba
    # com atalho próprio acende sozinha; o atalho geral, só nas outras abas.
    abas_com_atalho = {(urlsplit(i["href"]).path, (parse_qs(urlsplit(i["href"]).query).get("tab") or [""])[0])
                       for g in grupos for i in g["itens"] if i["ativo"]}
    for g in grupos:
        for i in g["itens"]:
            d = urlsplit(i["href"])
            if i["ativo"] and not d.query and (d.path, aba) in abas_com_atalho and aba:
                i["ativo"] = False
        g["ativo"] = any(i["ativo"] for i in g["itens"])
    return grupos
