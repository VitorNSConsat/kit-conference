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


# ── Fila de etiquetas "Em Andamento" (kit ainda sem kit_record) ────────────────
# Tabela separada de print_queue porque kit_id lá é NOT NULL e referencia
# kit_record — uma sessão em andamento não tem isso ainda. As duas aparecem
# juntas em listar_aguardando_tudo().

def adicionar_pausa(sessao_id: int, html_label: str, solicitado_por: int) -> int:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO print_queue_pausa (sessao_id, html_label, solicitado_por, solicitado_em) "
            "VALUES (?, ?, ?, ?)",
            (sessao_id, html_label, solicitado_por, now_brt())
        )
        return cur.lastrowid


def buscar_pausa(pq_id: int) -> dict | None:
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM print_queue_pausa WHERE id = ?", (pq_id,)
        ).fetchone()
    return dict(row) if row else None


def marcar_impresso_pausa(pq_id: int):
    with db() as conn:
        conn.execute(
            "UPDATE print_queue_pausa SET status = 'impresso', impresso_em = ? WHERE id = ?",
            (now_brt(), pq_id)
        )


def cancelar_pausa(pq_id: int):
    with db() as conn:
        conn.execute(
            "UPDATE print_queue_pausa SET status = 'cancelado' WHERE id = ?", (pq_id,)
        )


def listar_aguardando_tudo() -> list:
    """Etiquetas de kit finalizado + etiquetas "Em Andamento", juntas numa
    lista só (ordenada por quando foram pedidas), pro operador não ter que
    checar duas telas diferentes pra saber o que falta imprimir."""
    kits = listar_aguardando()
    for item in kits:
        item["tipo"] = "kit"
    with db() as conn:
        rows = conn.execute(
            "SELECT pqp.*, s.veiculo, s.garagem, s.sequencia, s.kit_template_id, "
            "kt.nome AS kit_nome, kt.cliente, u.nome AS solicitado_por_nome "
            "FROM print_queue_pausa pqp "
            "JOIN scan_session s ON s.id = pqp.sessao_id "
            "JOIN kit_template kt ON kt.id = s.kit_template_id "
            "JOIN users u ON u.id = pqp.solicitado_por "
            "WHERE pqp.status = 'aguardando' ORDER BY pqp.solicitado_em"
        ).fetchall()
    pausas = [dict(r) for r in rows]
    for item in pausas:
        item["tipo"] = "pausa"
    return sorted(kits + pausas, key=lambda i: i["solicitado_em"])
