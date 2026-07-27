from database import db, now_brt

LINHAS = 4
COLUNAS = 3


def listar_grade() -> dict:
    """Retorna {(linha, coluna): dict-do-item-ou-None} para as 12 posições."""
    grade = {(l, c): None for l in range(1, LINHAS + 1) for c in range(1, COLUNAS + 1)}
    with db() as conn:
        rows = conn.execute(
            "SELECT pp.linha, pp.coluna, e.id AS estoque_id, e.codigo_barra, "
            "e.quantidade_atual, e.quantidade_minima, it.nome AS tipo_nome "
            "FROM prateleira_posicoes pp "
            "JOIN estoque e ON e.id = pp.estoque_id "
            "JOIN item_tipo it ON it.id = e.item_tipo_id"
        ).fetchall()
    for r in rows:
        grade[(r["linha"], r["coluna"])] = dict(r)
    return grade


def atribuir(linha: int, coluna: int, estoque_id: int):
    """Coloca um item na posição — se o item já ocupava outra posição, move-o
    (um item só fica em 1 slot); se a posição já tinha outro item, substitui."""
    with db() as conn:
        conn.execute("DELETE FROM prateleira_posicoes WHERE estoque_id = ?", (estoque_id,))
        conn.execute("DELETE FROM prateleira_posicoes WHERE linha = ? AND coluna = ?", (linha, coluna))
        conn.execute(
            "INSERT INTO prateleira_posicoes (linha, coluna, estoque_id, criado_em) VALUES (?, ?, ?, ?)",
            (linha, coluna, estoque_id, now_brt())
        )


def limpar(linha: int, coluna: int):
    with db() as conn:
        conn.execute("DELETE FROM prateleira_posicoes WHERE linha = ? AND coluna = ?", (linha, coluna))
