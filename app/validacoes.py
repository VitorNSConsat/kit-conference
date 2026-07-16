from database import db, now_brt


def registrar(kit_id: str, user_id: int, observacao: str) -> int:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO kit_validacoes (kit_id, validado_por, validado_em, observacao) "
            "VALUES (?, ?, ?, ?)",
            (kit_id, user_id, now_brt(), observacao or None)
        )
        return cur.lastrowid


def listar_por_kit(kit_id: str) -> list:
    with db() as conn:
        rows = conn.execute(
            "SELECT kv.*, u.nome AS user_nome "
            "FROM kit_validacoes kv "
            "JOIN users u ON u.id = kv.validado_por "
            "WHERE kv.kit_id = ? "
            "ORDER BY kv.validado_em DESC",
            (kit_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def listar_relatorio(data_ini: str = "", data_fim: str = "", user_id: str = "") -> list:
    query = """
        SELECT kv.id, kv.kit_id, kv.validado_em, kv.observacao,
               uv.nome AS validado_por_nome,
               kr.finalizado_em, kr.veiculo, kr.garagem,
               kt.nome AS kit_nome, kt.cliente,
               uo.nome AS operador_nome,
               (
                   SELECT GROUP_CONCAT(sub.r, ' | ')
                   FROM (
                       SELECT it.nome || ' x' || COUNT(*) AS r
                       FROM scan_session_items si
                       JOIN item_tipo it ON it.id = si.item_tipo_id
                       WHERE si.sessao_id = kr.sessao_id
                       GROUP BY si.item_tipo_id
                   ) sub
               ) AS itens_resumo
        FROM kit_validacoes kv
        JOIN kit_record kr ON kr.kit_id = kv.kit_id
        JOIN kit_template kt ON kt.id = kr.kit_template_id
        JOIN users uv ON uv.id = kv.validado_por
        JOIN users uo ON uo.id = kr.operador_id
        WHERE 1=1
    """
    params = []
    if data_ini:
        query += " AND DATE(kv.validado_em) >= ?"
        params.append(data_ini)
    if data_fim:
        query += " AND DATE(kv.validado_em) <= ?"
        params.append(data_fim)
    if user_id and str(user_id).isdigit():
        query += " AND kv.validado_por = ?"
        params.append(int(user_id))
    query += " ORDER BY kv.validado_em DESC LIMIT 500"
    with db() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]
