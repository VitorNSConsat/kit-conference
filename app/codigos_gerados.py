from database import db, now_brt


def listar() -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT cg.*, u.nome AS criado_por_nome "
            "FROM codigo_gerado cg "
            "LEFT JOIN users u ON u.id = cg.criado_por "
            "ORDER BY cg.criado_em DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def registrar(texto: str, criado_por: int) -> None:
    """Garante que o código gerado em 'Gerar Códigos' fique visível na lista —
    sem duplicar quando o mesmo texto for gerado de novo (ex: reimpressão)."""
    texto = texto.strip()
    if not texto:
        return
    with db() as conn:
        existe = conn.execute(
            "SELECT 1 FROM codigo_gerado WHERE texto = ?", (texto,)
        ).fetchone()
        if not existe:
            conn.execute(
                "INSERT INTO codigo_gerado (texto, criado_por, criado_em) VALUES (?, ?, ?)",
                (texto, criado_por, now_brt())
            )


def toggle_reciclavel(codigo_id: int):
    with db() as conn:
        conn.execute(
            "UPDATE codigo_gerado SET reciclavel = 1 - COALESCE(reciclavel, 0) WHERE id = ?",
            (codigo_id,)
        )
