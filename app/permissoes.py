"""Permissões finas por usuário comum — em cima do modelo admin/comum que
já existe (admin continua sempre tendo acesso total, inclusive pra
configurar as permissões dos outros).

Guardado como lista de bloqueios (user_permissoes_negadas): a PRESENÇA de
uma linha nega aquela chave pro usuário. Ausência = permitido. Isso evita
ter que preencher nada pros usuários que já existem — todo mundo nasce
com tudo liberado, exatamente como era antes dessa tabela existir.
"""

from database import db

# ── Telas ────────────────────────────────────────────────────────────────────
# (chave, rótulo, para onde o link do menu aponta, prefixos de rota da tela).
# Uma tela = várias rotas (lista, detalhe, exportação, formulários); os
# prefixos existem pra o porteiro cobrir a área inteira, inclusive rota nova
# que venha a ser criada dentro dela.
#
# A ORDEM é a do menu: é ela que decide pra onde vai quem não pode ver a
# tela inicial.
TELAS = (
    ("ver_bipagem",        "Bipagem",            "/",                ("/", "/session")),
    ("ver_impressao",      "Impressão",          "/print-queue",     ("/print-queue",)),
    ("ver_itens",          "Itens & Estoque",    "/admin/items",     ("/admin/items", "/admin/tipos",
                                                                        "/admin/estoque", "/estoque")),
    ("ver_kits",           "Criar Kit/Pedido",   "/admin/templates", ("/admin/templates",)),
    ("ver_veiculos",       "Veículos e Clientes", "/admin/veiculos", ("/admin/veiculos", "/admin/clientes",
                                                                        "/admin/garagens")),
    ("ver_prateleira",     "Prateleira",         "/admin/prateleira", ("/admin/prateleira", "/prateleira")),
    ("ver_producao",       "Produção",           "/admin/producao",  ("/admin/producao", "/producao")),
    ("ver_relatorios",     "Relatórios",         "/reports",         ("/reports",)),
    ("ver_rede",           "Rede",               "/rede",            ("/rede",)),
    ("ver_funcionalidades", "Funcionalidades",   "/funcionalidades", ("/funcionalidades",)),
    ("ver_backup",         "Backup",             "/admin/backup",    ("/admin/backup",)),
)

# Chave -> rótulo exibido na tela de gestão de usuários.
PERMISSOES_TELAS = {chave: rotulo for chave, rotulo, _destino, _prefixos in TELAS}

# Ações por assunto — grupos menores em vez de uma lista única, pra achar
# mais rápido na tela de usuários (e pra "Veículos" já nascer com a
# permissão de excluir, em vez de depender só de ser admin).
PERMISSOES_VEICULOS = {
    "veiculos_excluir": "Excluir veículos",
}
PERMISSOES_ESTOQUE = {
    "estoque_editar": "Repor/corrigir quantidade em Estoque",
    "itens_apagar": "Apagar itens do catálogo",
}
PERMISSOES_PRODUCAO = {
    "producao_nota_fiscal": "Editar Nota Fiscal na Produção",
    "producao_mover_estagio": "Mover kits na esteira (trânsito, cliente, voltar)",
    "remessas_gerenciar": "Abrir remessa e mudar a quantidade do envio",
}
PERMISSOES_PATRIMONIO = {
    "patrimonio_corrigir": "Corrigir patrimônio (código e nº de série)",
    "patrimonio_mover": "Mover patrimônio de um veículo para outro",
    "patrimonio_atribuir": "Atribuir/retirar patrimônio de um kit fechado",
}
PERMISSOES_PEDIDOS = {
    "pedidos_criar_editar": "Criar/editar Pedidos",
}
PERMISSOES_BIPAGEM = {
    "bipagem_excluir_item": "Excluir bipagem de item específico (kit em aberto)",
}
PERMISSOES_SISTEMA = {
    "backup_configurar": "Configurar a rotina de backup e fazer cópia na hora",
}

PERMISSOES_ACOES = {
    **PERMISSOES_VEICULOS, **PERMISSOES_ESTOQUE, **PERMISSOES_PRODUCAO,
    **PERMISSOES_PATRIMONIO, **PERMISSOES_PEDIDOS, **PERMISSOES_BIPAGEM,
    **PERMISSOES_SISTEMA,
}

PERMISSOES = {**PERMISSOES_TELAS, **PERMISSOES_ACOES}

# Como a tela de usuários agrupa os checkboxes — ver tela é uma pergunta,
# cada assunto de ação é outra, e ficavam todas embaralhadas numa lista só.
GRUPOS = (
    ("Telas que o usuário enxerga", PERMISSOES_TELAS),
    ("Veículos", PERMISSOES_VEICULOS),
    ("Estoque e itens", PERMISSOES_ESTOQUE),
    ("Produção e remessas", PERMISSOES_PRODUCAO),
    ("Patrimônio", PERMISSOES_PATRIMONIO),
    ("Pedidos", PERMISSOES_PEDIDOS),
    ("Bipagem", PERMISSOES_BIPAGEM),
    ("Sistema", PERMISSOES_SISTEMA),
)

# Toda chave nova nasce PERMITIDA pra quem já existe: tem_permissao() nega só
# o que está na lista de negadas do usuário. Então acrescentar uma permissão
# aqui não tira acesso de ninguém — só passa a ser possível restringir.


def permissao_da_rota(caminho: str) -> str | None:
    """Qual permissão de tela cobre este caminho (None = tela sem porteiro).

    Casa por segmento inteiro: '/reports' cobre '/reports/operadores', mas
    '/estoque' não pode cobrir '/estoquex' nem '/admin/estoque' virar dono
    de tudo que comece com essas letras."""
    for chave, _rotulo, _destino, prefixos in TELAS:
        for p in prefixos:
            if caminho == p or (p != "/" and caminho.startswith(p + "/")):
                return chave
    return None


def negadas_do_usuario(user_id: int) -> set[str]:
    with db() as conn:
        rows = conn.execute(
            "SELECT chave FROM user_permissoes_negadas WHERE user_id = ?", (user_id,)
        ).fetchall()
    return {r["chave"] for r in rows}


def tem_permissao(user: dict | None, chave: str) -> bool:
    """Admin sempre passa. Sem usuário (deslogado), nunca passa."""
    if not user:
        return False
    if user.get("admin"):
        return True
    return chave not in negadas_do_usuario(user["id"])


def definir_permissoes(user_id: int, permitidas: set[str]) -> None:
    """Recebe o conjunto de chaves que devem ficar PERMITIDAS (vindas dos
    checkboxes marcados na tela) e recalcula a lista de bloqueios inteira:
    tudo que existe em PERMISSOES e não está em `permitidas` vira negado."""
    negar = set(PERMISSOES) - permitidas
    with db() as conn:
        conn.execute("DELETE FROM user_permissoes_negadas WHERE user_id = ?", (user_id,))
        conn.executemany(
            "INSERT INTO user_permissoes_negadas (user_id, chave) VALUES (?, ?)",
            [(user_id, chave) for chave in negar]
        )
