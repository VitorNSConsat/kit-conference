from database import db


def listar_estoque() -> list:
    with db() as conn:
        rows = conn.execute(
            "SELECT e.*, it.nome AS tipo_nome "
            "FROM estoque e "
            "JOIN item_tipo it ON it.id = e.item_tipo_id "
            "ORDER BY it.nome"
        ).fetchall()
    return [dict(r) for r in rows]


def buscar_por_codigo(codigo_barra: str) -> dict | None:
    with db() as conn:
        row = conn.execute(
            "SELECT e.*, it.nome AS tipo_nome "
            "FROM estoque e "
            "JOIN item_tipo it ON it.id = e.item_tipo_id "
            "WHERE e.codigo_barra = ?",
            (codigo_barra,)
        ).fetchone()
    return dict(row) if row else None


def buscar_por_id(estoque_id: int) -> dict | None:
    with db() as conn:
        row = conn.execute(
            "SELECT e.*, it.nome AS tipo_nome "
            "FROM estoque e "
            "JOIN item_tipo it ON it.id = e.item_tipo_id "
            "WHERE e.id = ?",
            (estoque_id,)
        ).fetchone()
    return dict(row) if row else None


def criar_estoque(item_tipo_id: int, codigo_barra: str,
                  quantidade_inicial: int, quantidade_minima: int,
                  criado_por: int) -> int:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO estoque (item_tipo_id, codigo_barra, quantidade_atual, quantidade_minima) "
            "VALUES (?, ?, ?, ?)",
            (item_tipo_id, codigo_barra, quantidade_inicial, quantidade_minima)
        )
        estoque_id = cur.lastrowid
        if quantidade_inicial > 0:
            conn.execute(
                "INSERT INTO estoque_movimentos "
                "(estoque_id, tipo, quantidade, criado_por, observacao) "
                "VALUES (?, 'entrada', ?, ?, 'Estoque inicial')",
                (estoque_id, quantidade_inicial, criado_por)
            )
    return estoque_id


def repor_estoque(estoque_id: int, quantidade: int,
                  criado_por: int, observacao: str = "") -> None:
    with db() as conn:
        conn.execute(
            "UPDATE estoque SET quantidade_atual = quantidade_atual + ? WHERE id = ?",
            (quantidade, estoque_id)
        )
        conn.execute(
            "INSERT INTO estoque_movimentos "
            "(estoque_id, tipo, quantidade, criado_por, observacao) "
            "VALUES (?, 'entrada', ?, ?, ?)",
            (estoque_id, quantidade, criado_por, observacao or "Reposição")
        )


def registrar_saida(estoque_id: int, quantidade: int,
                    sessao_id: int, criado_por: int) -> None:
    with db() as conn:
        conn.execute(
            "UPDATE estoque SET quantidade_atual = quantidade_atual - ? WHERE id = ?",
            (quantidade, estoque_id)
        )
        conn.execute(
            "INSERT INTO estoque_movimentos "
            "(estoque_id, tipo, quantidade, sessao_id, criado_por, observacao) "
            "VALUES (?, 'saida', ?, ?, ?, 'Kit')",
            (estoque_id, quantidade, sessao_id, criado_por)
        )


def reverter_saidas_sessao(sessao_id: int) -> None:
    """Restaura estoque das saídas de uma sessão cancelada."""
    with db() as conn:
        saidas = conn.execute(
            "SELECT estoque_id, SUM(quantidade) AS total "
            "FROM estoque_movimentos "
            "WHERE sessao_id = ? AND tipo = 'saida' "
            "GROUP BY estoque_id",
            (sessao_id,)
        ).fetchall()
        for s in saidas:
            conn.execute(
                "UPDATE estoque SET quantidade_atual = quantidade_atual + ? WHERE id = ?",
                (s["total"], s["estoque_id"])
            )
        conn.execute(
            "UPDATE estoque_movimentos SET tipo = 'saida_cancelada' "
            "WHERE sessao_id = ? AND tipo = 'saida'",
            (sessao_id,)
        )


def listar_historico(estoque_id: int, limit: int = 100) -> list:
    with db() as conn:
        rows = conn.execute(
            "SELECT em.*, u.nome AS operador_nome "
            "FROM estoque_movimentos em "
            "LEFT JOIN users u ON u.id = em.criado_por "
            "WHERE em.estoque_id = ? "
            "ORDER BY em.criado_em DESC LIMIT ?",
            (estoque_id, limit)
        ).fetchall()
    return [dict(r) for r in rows]


def alertas_abaixo_minimo() -> list:
    """Retorna itens de estoque com quantidade abaixo ou igual ao mínimo."""
    with db() as conn:
        rows = conn.execute(
            "SELECT e.*, it.nome AS tipo_nome "
            "FROM estoque e "
            "JOIN item_tipo it ON it.id = e.item_tipo_id "
            "WHERE e.quantidade_atual <= e.quantidade_minima "
            "ORDER BY (e.quantidade_atual - e.quantidade_minima), it.nome"
        ).fetchall()
    return [dict(r) for r in rows]


def deletar_estoque(estoque_id: int) -> None:
    with db() as conn:
        conn.execute("DELETE FROM estoque_movimentos WHERE estoque_id = ?", (estoque_id,))
        conn.execute("DELETE FROM estoque WHERE id = ?", (estoque_id,))
