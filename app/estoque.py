import re
from database import db, now_brt

# Status de compra — independente do status de quantidade (abaixo/proximo/ok).
# Vazio ('') = sem pendencia, nao aparece nenhum aviso.
STATUS_COMPRA = {
    "pedido": "Pedido ao fornecedor",
    "andamento": "Em andamento",
    "recebido": "Recebido",
}


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


def buscar_por_tipo(item_tipo_id: int) -> dict | None:
    with db() as conn:
        row = conn.execute(
            "SELECT e.*, it.nome AS tipo_nome "
            "FROM estoque e "
            "JOIN item_tipo it ON it.id = e.item_tipo_id "
            "WHERE e.item_tipo_id = ?",
            (item_tipo_id,)
        ).fetchone()
    return dict(row) if row else None


def buscar_por_referencia(texto: str) -> dict | None:
    """Busca item de estoque pelo código de barras direto, ou pela URL do QR
    da etiqueta (formato .../estoque/<id>) — permite que o mesmo QR seja lido
    tanto durante a bipagem (desconta como um código normal) quanto fora dela
    (mostra a quantidade atual)."""
    texto = (texto or "").strip()
    if not texto:
        return None
    direto = buscar_por_codigo(texto)
    if direto:
        return direto
    m = re.search(r'/estoque/(\d+)/?$', texto)
    if m:
        return buscar_por_id(int(m.group(1)))
    return None


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
            "INSERT INTO estoque (item_tipo_id, codigo_barra, quantidade_atual, quantidade_minima, criado_em) "
            "VALUES (?, ?, ?, ?, ?)",
            (item_tipo_id, codigo_barra, quantidade_inicial, quantidade_minima, now_brt())
        )
        estoque_id = cur.lastrowid
        if quantidade_inicial > 0:
            conn.execute(
                "INSERT INTO estoque_movimentos "
                "(estoque_id, tipo, quantidade, criado_por, observacao, criado_em) "
                "VALUES (?, 'entrada', ?, ?, 'Estoque inicial', ?)",
                (estoque_id, quantidade_inicial, criado_por, now_brt())
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
            "(estoque_id, tipo, quantidade, criado_por, observacao, criado_em) "
            "VALUES (?, 'entrada', ?, ?, ?, ?)",
            (estoque_id, quantidade, criado_por, observacao or "Reposição", now_brt())
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
            "(estoque_id, tipo, quantidade, sessao_id, criado_por, observacao, criado_em) "
            "VALUES (?, 'saida', ?, ?, ?, 'Kit', ?)",
            (estoque_id, quantidade, sessao_id, criado_por, now_brt())
        )


def registrar_sobressalente(estoque_id: int, quantidade: int, cliente: str,
                            criado_por: int, observacao: str = "") -> None:
    """Baixa manual de peça sobressalente enviada numa instalação — fora do
    que o kit bipado já contabiliza. Desconta do estoque e grava o cliente,
    pra dar pra consultar depois em Relatórios quanto já foi enviado de
    sobressalente pra cada um. Bloqueia se não tiver saldo suficiente (ação
    deliberada de escritório, não bipagem em campo — vale travar e pedir
    pra corrigir o estoque antes, ao contrário do desconto automático do
    kit que prefere deixar negativo a travar o operador)."""
    cliente = cliente.strip()
    if not cliente:
        raise ValueError("Informe o cliente.")
    if quantidade <= 0:
        raise ValueError("Quantidade deve ser maior que zero.")
    with db() as conn:
        atual = conn.execute(
            "SELECT quantidade_atual FROM estoque WHERE id = ?", (estoque_id,)
        ).fetchone()
        if atual is None:
            raise ValueError("Item de estoque não encontrado.")
        if atual["quantidade_atual"] < quantidade:
            raise ValueError(
                f"Estoque insuficiente ({atual['quantidade_atual']} disponíveis, "
                f"{quantidade} necessários)."
            )
        conn.execute(
            "UPDATE estoque SET quantidade_atual = quantidade_atual - ? WHERE id = ?",
            (quantidade, estoque_id)
        )
        conn.execute(
            "INSERT INTO estoque_movimentos "
            "(estoque_id, tipo, quantidade, cliente, criado_por, observacao, criado_em) "
            "VALUES (?, 'sobressalente', ?, ?, ?, ?, ?)",
            (estoque_id, quantidade, cliente, criado_por, observacao.strip() or None, now_brt())
        )


def listar_sobressalentes(data_ini: str = "", data_fim: str = "", cliente: str = "") -> list[dict]:
    query = (
        "SELECT em.*, e.codigo_barra, it.nome AS tipo_nome, u.nome AS operador_nome "
        "FROM estoque_movimentos em "
        "JOIN estoque e ON e.id = em.estoque_id "
        "JOIN item_tipo it ON it.id = e.item_tipo_id "
        "LEFT JOIN users u ON u.id = em.criado_por "
        "WHERE em.tipo = 'sobressalente'"
    )
    params: list = []
    if data_ini:
        query += " AND DATE(em.criado_em) >= ?"
        params.append(data_ini)
    if data_fim:
        query += " AND DATE(em.criado_em) <= ?"
        params.append(data_fim)
    if cliente:
        query += " AND em.cliente = ?"
        params.append(cliente)
    query += " ORDER BY em.criado_em DESC"
    with db() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


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


def listar_historico_completo() -> list:
    """Todas as movimentações de todos os itens de estoque — usado na
    exportação em Excel (aba Histórico)."""
    with db() as conn:
        rows = conn.execute(
            "SELECT em.*, e.codigo_barra, it.nome AS tipo_nome, u.nome AS operador_nome "
            "FROM estoque_movimentos em "
            "JOIN estoque e ON e.id = em.estoque_id "
            "JOIN item_tipo it ON it.id = e.item_tipo_id "
            "LEFT JOIN users u ON u.id = em.criado_por "
            "ORDER BY it.nome, em.criado_em DESC"
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


def atualizar_codigo_barra(estoque_id: int, novo_codigo: str) -> None:
    novo_codigo = (novo_codigo or "").strip()
    if not novo_codigo:
        raise ValueError("Código de barras não pode ser vazio.")
    with db() as conn:
        conn.execute(
            "UPDATE estoque SET codigo_barra = ? WHERE id = ?",
            (novo_codigo, estoque_id)
        )


def corrigir_quantidade(estoque_id: int, nova_quantidade: int, criado_por: int) -> None:
    """Corrige a quantidade atual diretamente para o valor informado (ajuste
    absoluto) — para acertar contagens divergentes sem precisar calcular a
    diferença e usar repor/ajustar."""
    if nova_quantidade < 0:
        raise ValueError("Quantidade não pode ser negativa.")
    with db() as conn:
        atual = conn.execute(
            "SELECT quantidade_atual FROM estoque WHERE id = ?", (estoque_id,)
        ).fetchone()
        if atual is None:
            raise ValueError("Item de estoque não encontrado.")
        antigo = atual["quantidade_atual"]
        conn.execute(
            "UPDATE estoque SET quantidade_atual = ? WHERE id = ?",
            (nova_quantidade, estoque_id)
        )
        conn.execute(
            "INSERT INTO estoque_movimentos "
            "(estoque_id, tipo, quantidade, criado_por, observacao, criado_em) "
            "VALUES (?, 'correcao', ?, ?, ?, ?)",
            (estoque_id, nova_quantidade, criado_por,
             f"Quantidade corrigida: {antigo} → {nova_quantidade}", now_brt())
        )


def atualizar_minimo(estoque_id: int, novo_minimo: int, criado_por: int) -> None:
    with db() as conn:
        atual = conn.execute(
            "SELECT quantidade_minima FROM estoque WHERE id = ?", (estoque_id,)
        ).fetchone()
        if atual is None:
            return
        antigo = atual["quantidade_minima"]
        conn.execute(
            "UPDATE estoque SET quantidade_minima = ? WHERE id = ?",
            (novo_minimo, estoque_id)
        )
        conn.execute(
            "INSERT INTO estoque_movimentos "
            "(estoque_id, tipo, quantidade, criado_por, observacao, criado_em) "
            "VALUES (?, 'ajuste_minimo', ?, ?, ?, ?)",
            (estoque_id, novo_minimo, criado_por,
             f"Mínimo alterado: {antigo} → {novo_minimo}", now_brt())
        )


def atualizar_status_compra(estoque_id: int, novo_status: str, criado_por: int) -> None:
    """Marca se o item já foi pedido ao fornecedor / está a caminho / chegou.
    Independente do status de quantidade (abaixo/proximo/ok) — um item pode
    estar OK em quantidade e ainda ter uma reposição futura já encomendada."""
    novo_status = novo_status if novo_status in STATUS_COMPRA else ""
    with db() as conn:
        atual = conn.execute(
            "SELECT status_compra FROM estoque WHERE id = ?", (estoque_id,)
        ).fetchone()
        if atual is None:
            return
        antigo = atual["status_compra"] or ""
        if antigo == novo_status:
            return
        conn.execute(
            "UPDATE estoque SET status_compra = ? WHERE id = ?",
            (novo_status, estoque_id)
        )
        conn.execute(
            "INSERT INTO estoque_movimentos "
            "(estoque_id, tipo, quantidade, criado_por, observacao, criado_em) "
            "VALUES (?, 'status_compra', 0, ?, ?, ?)",
            (estoque_id, criado_por,
             f"Status de compra: {STATUS_COMPRA.get(antigo, 'Sem pendência')} → "
             f"{STATUS_COMPRA.get(novo_status, 'Sem pendência')}", now_brt())
        )


def ajustar_quantidade(estoque_id: int, tipo: str, quantidade: int,
                       motivo: str, criado_por: int) -> int:
    """Ajuste manual (entrada ou saída). Retorna a nova quantidade."""
    if tipo not in ("entrada", "saida"):
        raise ValueError("Tipo inválido.")
    with db() as conn:
        est = conn.execute("SELECT * FROM estoque WHERE id = ?", (estoque_id,)).fetchone()
        if not est:
            raise ValueError("Item não encontrado.")
        nova = est["quantidade_atual"] + quantidade if tipo == "entrada" \
               else est["quantidade_atual"] - quantidade
        if nova < 0:
            raise ValueError(
                f"Estoque insuficiente. Disponível: {est['quantidade_atual']}, "
                f"solicitado: {quantidade}."
            )
        conn.execute("UPDATE estoque SET quantidade_atual = ? WHERE id = ?", (nova, estoque_id))
        conn.execute(
            "INSERT INTO estoque_movimentos "
            "(estoque_id, tipo, quantidade, criado_por, observacao, criado_em) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (estoque_id, tipo, quantidade, criado_por, motivo, now_brt())
        )
    return nova


def deletar_estoque(estoque_id: int) -> None:
    with db() as conn:
        conn.execute("DELETE FROM estoque_movimentos WHERE estoque_id = ?", (estoque_id,))
        conn.execute("DELETE FROM estoque WHERE id = ?", (estoque_id,))


def reconciliar_saidas_saquinho(criado_por: int) -> list[dict]:
    """Corrige retroativamente o estoque de itens que vieram de saquinho
    (scan_session_items.codigo_barra 'COMP:...') e nunca tiveram a baixa
    aplicada — bug histórico onde confirmar_componente não descontava
    estoque. Cada linha nasce com estoque_debitado=0 até ser processada
    (pelo fluxo normal, a partir do fix, ou por esta função, pras
    antigas), então rodar de novo não desconta duas vezes. Sem checar
    saldo antes de descontar: mesma filosofia do resto do sistema — não
    trava, só sinaliza que a contagem física precisa ser conferida.
    Retorna um resumo por tipo de item corrigido."""
    with db() as conn:
        pendentes = conn.execute(
            "SELECT ssi.item_tipo_id, it.nome AS tipo_nome, e.id AS estoque_id, "
            "COUNT(*) AS qtd "
            "FROM scan_session_items ssi "
            "JOIN item_tipo it ON it.id = ssi.item_tipo_id "
            "JOIN estoque e ON e.item_tipo_id = ssi.item_tipo_id "
            "WHERE ssi.codigo_barra LIKE 'COMP:%' AND ssi.estoque_debitado = 0 "
            "GROUP BY ssi.item_tipo_id"
        ).fetchall()
        pendentes = [dict(r) for r in pendentes]

    resumo = []
    for p in pendentes:
        with db() as conn:
            conn.execute(
                "UPDATE estoque SET quantidade_atual = quantidade_atual - ? WHERE id = ?",
                (p["qtd"], p["estoque_id"])
            )
            conn.execute(
                "INSERT INTO estoque_movimentos "
                "(estoque_id, tipo, quantidade, criado_por, observacao, criado_em) "
                "VALUES (?, 'saida', ?, ?, ?, ?)",
                (p["estoque_id"], p["qtd"], criado_por,
                 "Correção retroativa: saquinho não debitava estoque antes da correção do bug",
                 now_brt())
            )
            conn.execute(
                "UPDATE scan_session_items SET estoque_debitado = 1 "
                "WHERE codigo_barra LIKE 'COMP:%' AND item_tipo_id = ? AND estoque_debitado = 0",
                (p["item_tipo_id"],)
            )
        resumo.append({"tipo_nome": p["tipo_nome"], "quantidade": p["qtd"]})
    return resumo
