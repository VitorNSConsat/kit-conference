"""Menu lateral: os grupos e os atalhos de cada um.

Além das telas principais, o menu leva direto às telas que antes só se
achavam por dentro de outras (importações, modelos de planilha, relatórios,
remessas…). Cada atalho só aparece pra quem pode abrir o destino — a regra é a
MESMA do porteiro das rotas (permissão da tela) mais a permissão de ação
quando a tela pede uma (ex.: ajustar estoque exige estoque_editar).

`ativo` diz se o atalho é a tela aberta agora: casa pelo caminho e, quando o
atalho aponta pra uma aba (`?tab=…`), pela aba também — senão "Itens & Estoque"
e "Sobressalentes" acenderiam juntos.
"""
from urllib.parse import urlsplit, parse_qs

# (rótulo, ícone, href, permissões exigidas, prefixos que contam como "esta tela",
#  caminhos que NÃO contam (pertencem a outro atalho), só admin, é download)
_GRUPOS = [
    ("Operação", "bipagem", [
        ("Bipagem", "bipagem", "/", ["ver_bipagem"], ["/", "/session"], [], False, False),
        ("Impressão", "impressao", "/print-queue", ["ver_impressao"], ["/print-queue"], [], False, False),
        ("Produção", "producao", "/admin/producao", ["ver_producao"], ["/admin/producao", "/producao"],
         ["/admin/producao/remessas", "/admin/producao/historico"], False, False),
        ("Remessas", "caixa", "/admin/producao/remessas", ["ver_producao"], ["/admin/producao/remessas"], [], False, False),
        ("RMAs", "alerta", "/admin/hardware", ["ver_hardware"], ["/admin/hardware"],
         ["/admin/hardware/importar", "/admin/hardware/importacoes", "/admin/hardware/produtos"], False, False),
        ("Prateleira", "prateleira", "/admin/prateleira", ["ver_prateleira"], ["/admin/prateleira", "/prateleira"], [], False, False),
    ]),
    ("Cadastros", "kits", [
        ("Veículos e Clientes", "veiculos", "/admin/veiculos", ["ver_veiculos"],
         ["/admin/veiculos", "/admin/clientes", "/admin/garagens"],
         ["/admin/veiculos/import", "/admin/veiculos/importacoes"], False, False),
        ("Itens & Estoque", "itens", "/admin/items", ["ver_itens"], ["/admin/items", "/admin/tipos", "/admin/estoque"],
         [], False, False),
        ("Sobressalentes", "caixa", "/admin/items?tab=sobressalentes", ["ver_itens"], ["/admin/items"], [], False, False),
        ("Criar Kit/Pedido", "kits", "/admin/templates", ["ver_kits"], ["/admin/templates"], [], False, False),
        ("Produtos de RMA", "clipboard", "/admin/hardware/produtos", ["ver_hardware"], ["/admin/hardware/produtos"], [], False, False),
    ]),
    ("Importações", "importar", [
        ("Importar veículos", "importar", "/admin/veiculos/import", ["ver_veiculos"], ["/admin/veiculos/import"], [], False, False),
        ("Importações de veículos", "relogio", "/admin/veiculos/importacoes", ["ver_veiculos"], ["/admin/veiculos/importacoes"], [], False, False),
        ("Importar RMAs", "importar", "/admin/hardware/importar", ["ver_hardware"], ["/admin/hardware/importar"], [], False, False),
        ("Importações de RMAs", "relogio", "/admin/hardware/importacoes", ["ver_hardware"], ["/admin/hardware/importacoes"], [], False, False),
        ("Ajustar estoque por planilha", "importar", "/admin/items?tab=catalogo#importar-ajuste",
         ["ver_itens", "estoque_editar"], [], [], False, False),
        ("Importar BOM de itens", "importar", "/admin/items?tab=novo#importar-bom", ["ver_itens"], [], [], False, False),
        ("Criar kit pela BOM", "importar", "/admin/templates?tab=kit#bom", ["ver_kits"], [], [], False, False),
        ("Importar planilha de pedido", "importar", "/admin/templates?tab=pedido#importar-pedido",
         ["ver_kits", "pedidos_criar_editar"], [], [], False, False),
    ]),
    ("Modelos de planilha", "arquivo", [
        ("Modelo de veículos", "baixar", "/admin/veiculos/modelo.xlsx", ["ver_veiculos"], [], [], False, True),
        ("Modelo de RMAs", "baixar", "/admin/hardware/modelo.xlsx", ["ver_hardware"], [], [], False, True),
        ("Planilha de ajuste de estoque", "baixar", "/admin/estoque/ajuste-modelo.xlsx",
         ["ver_itens", "estoque_editar"], [], [], False, True),
    ]),
    ("Relatórios", "relatorios", [
        ("Kits finalizados", "relatorios", "/reports", ["ver_relatorios"], [], [], False, False),
        ("Kits por operador", "usuarios", "/reports/operadores", ["ver_relatorios"], ["/reports/operadores"], [], False, False),
        ("Verificações", "check-circulo", "/reports/validacoes", ["ver_relatorios"], ["/reports/validacoes"], [], False, False),
        ("Sobressalentes enviados", "caixa", "/reports/sobressalentes", ["ver_relatorios"], ["/reports/sobressalentes"], [], False, False),
        ("Mudanças de patrimônio", "historico", "/reports/patrimonio", ["ver_relatorios"], ["/reports/patrimonio"], [], False, False),
        ("Histórico da produção", "historico", "/admin/producao/historico", ["ver_producao"], ["/admin/producao/historico"], [], False, False),
        ("Auditoria", "cadeado", "/admin/auditoria", [], ["/admin/auditoria"], [], True, False),
    ]),
    ("Gestão", "funcionalidades", [
        ("Rede", "rede", "/rede", ["ver_rede"], ["/rede"], [], False, False),
        ("Funcionalidades", "funcionalidades", "/funcionalidades", ["ver_funcionalidades"], ["/funcionalidades"], [], False, False),
        ("Backup", "backup", "/admin/backup", ["ver_backup"], ["/admin/backup"], [], False, False),
        ("Padrão Visual", "paleta", "/admin/padrao-visual", [], ["/admin/padrao-visual"], [], True, False),
    ]),
]


def _casa(caminho: str, prefixo: str) -> bool:
    if prefixo == "/":
        return caminho == "/" or caminho.startswith("/session")
    return caminho == prefixo or caminho.startswith(prefixo + "/")


def montar(caminho: str, query: str, user: dict | None, pode) -> list[dict]:
    """[{nome, icone, chave, ativo, itens:[{rotulo, icone, href, ativo, download}]}] —
    só os grupos com algum atalho que este usuário pode abrir."""
    if not user:
        return []
    aba = (parse_qs(query or "").get("tab") or [""])[0]
    grupos = []
    for nome, icone_grupo, itens in _GRUPOS:
        visiveis = []
        for rotulo, icone, href, perms, prefixos, excluir, so_admin, download in itens:
            if so_admin and not user.get("admin"):
                continue
            if any(not pode(p) for p in perms):
                continue
            destino = urlsplit(href)
            aba_destino = (parse_qs(destino.query).get("tab") or [""])[0]
            ativo = False
            # Atalho pra um trecho de outra tela (#âncora) nunca "acende": a tela
            # aberta é a dona do trecho (Itens & Estoque, Criar Kit/Pedido).
            if not download and not destino.fragment:
                if prefixos:
                    ativo = (any(_casa(caminho, pre) for pre in prefixos)
                             and not any(_casa(caminho, ex) for ex in excluir))
                else:
                    ativo = caminho == destino.path and (not aba_destino or aba == aba_destino)
                # Duas entradas da mesma tela separadas pela aba: Sobressalentes
                # acende só na aba dela; Itens & Estoque, em todas as outras.
                if ativo and aba_destino and prefixos:
                    ativo = aba == aba_destino
                if ativo and href == "/admin/items" and aba == "sobressalentes":
                    ativo = False
            visiveis.append({"rotulo": rotulo, "icone": icone, "href": href,
                             "ativo": ativo, "download": download})
        if visiveis:
            grupos.append({"nome": nome, "icone": icone_grupo,
                           "chave": nome.lower().replace(" ", "-"),
                           "ativo": any(i["ativo"] for i in visiveis), "itens": visiveis})
    # Relatórios: "Kits finalizados" (/reports) acende só na própria página.
    for g in grupos:
        for i in g["itens"]:
            if i["href"] == "/reports":
                i["ativo"] = caminho == "/reports" or (caminho.startswith("/reports/")
                                                      and caminho.split("/")[2] not in
                                                      ("operadores", "validacoes", "sobressalentes", "patrimonio"))
        g["ativo"] = any(i["ativo"] for i in g["itens"])
    return grupos
