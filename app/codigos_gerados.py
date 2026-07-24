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
    """Alterna reciclável e sincroniza com o TIPO do patrimônio que já usa este
    código de barras (se algum), marcando/desmarcando 'Reutilizável' no tipo —
    é essa flag que permite bipar o mesmo código em mais de um kit ativo."""
    with db() as conn:
        row = conn.execute(
            "SELECT texto, reciclavel FROM codigo_gerado WHERE id = ?", (codigo_id,)
        ).fetchone()
        if not row:
            return
        novo_valor = 1 - (row["reciclavel"] or 0)
        conn.execute(
            "UPDATE codigo_gerado SET reciclavel = ? WHERE id = ?",
            (novo_valor, codigo_id)
        )
        item = conn.execute(
            "SELECT item_tipo_id FROM item_master WHERE codigo_barra = ?", (row["texto"],)
        ).fetchone()
        if item:
            conn.execute(
                "UPDATE item_tipo SET reutilizavel = ? WHERE id = ?",
                (novo_valor, item["item_tipo_id"])
            )


def sincronizar_tipo_se_reciclavel(codigo_barra: str, item_tipo_id: int):
    """Chamado sempre que um patrimônio novo é criado para um código de barras.
    Se esse código já foi marcado reciclável em 'Gerar Códigos' (o operador
    marcou antes mesmo de bipar pela primeira vez), propaga para o tipo —
    sem isso, o patrimônio nasceria sem poder participar de mais de 1 kit."""
    with db() as conn:
        row = conn.execute(
            "SELECT 1 FROM codigo_gerado WHERE texto = ? AND reciclavel = 1",
            (codigo_barra,)
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE item_tipo SET reutilizavel = 1 WHERE id = ?", (item_tipo_id,)
            )
