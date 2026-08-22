"""Permissões finas por usuário comum — em cima do modelo admin/comum que
já existe (admin continua sempre tendo acesso total, inclusive pra
configurar as permissões dos outros).

Guardado como lista de bloqueios (user_permissoes_negadas): a PRESENÇA de
uma linha nega aquela chave pro usuário. Ausência = permitido. Isso evita
ter que preencher nada pros usuários que já existem — todo mundo nasce
com tudo liberado, exatamente como era antes dessa tabela existir.
"""

from database import db

# Chave -> rótulo exibido na tela de gestão de usuários.
PERMISSOES = {
    "ver_rede": "Ver a tela Rede",
    "ver_relatorios": "Ver Relatórios",
    "estoque_editar": "Repor/corrigir quantidade em Estoque",
    "producao_nota_fiscal": "Editar Nota Fiscal na Produção",
    "producao_mover_estagio": "Mover kits na esteira (trânsito, cliente, voltar)",
    "patrimonio_corrigir": "Corrigir patrimônio (código e nº de série)",
    "pedidos_criar_editar": "Criar/editar Pedidos",
    "itens_apagar": "Apagar itens do catálogo",
    "bipagem_excluir_item": "Excluir bipagem de item específico (kit em aberto)",
}
# Toda chave nova nasce PERMITIDA pra quem já existe: tem_permissao() nega só
# o que está na lista de negadas do usuário. Então acrescentar uma permissão
# aqui não tira acesso de ninguém — só passa a ser possível restringir.


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
