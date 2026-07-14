from database import db, now_brt


def adicionar(kit_id: str, zpl: str, solicitado_por: int):
    with db() as conn:
        conn.execute(
            "INSERT INTO print_queue (kit_id, zpl, solicitado_por, solicitado_em) VALUES (?, ?, ?, ?)",
            (kit_id, zpl, solicitado_por, now_brt())
        )


def listar_aguardando() -> list:
    with db() as conn:
        rows = conn.execute(
            "SELECT pq.*, kr.kit_template_id, kt.nome AS kit_nome, kt.cliente, "
            "u.nome AS solicitado_por_nome "
            "FROM print_queue pq "
            "JOIN kit_record kr ON kr.kit_id = pq.kit_id "
            "JOIN kit_template kt ON kt.id = kr.kit_template_id "
            "JOIN users u ON u.id = pq.solicitado_por "
            "WHERE pq.status = 'aguardando' ORDER BY pq.solicitado_em"
        ).fetchall()
    return [dict(r) for r in rows]


def marcar_impresso(pq_id: int):
    with db() as conn:
        conn.execute(
            "UPDATE print_queue SET status = 'impresso', "
            "impresso_em = ? WHERE id = ?",
            (now_brt(), pq_id)
        )


def cancelar(pq_id: int):
    with db() as conn:
        conn.execute(
            "UPDATE print_queue SET status = 'cancelado' WHERE id = ?", (pq_id,)
        )


def buscar(pq_id: int) -> dict | None:
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM print_queue WHERE id = ?", (pq_id,)
        ).fetchone()
    return dict(row) if row else None
