from database import db


def listar_itens() -> list:
    with db() as conn:
        rows = conn.execute(
            "SELECT i.*, u.nome AS criado_por_nome FROM item_master i "
            "LEFT JOIN users u ON u.id = i.criado_por ORDER BY i.descricao"
        ).fetchall()
    return [dict(r) for r in rows]


def buscar_item(codigo_barra: str) -> dict | None:
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM item_master WHERE codigo_barra = ? AND ativo = 1",
            (codigo_barra,)
        ).fetchone()
    return dict(row) if row else None


def criar_item(codigo_barra: str, descricao: str, unidade: str,
               categoria: str, controla_serial: bool, criado_por: int):
    with db() as conn:
        conn.execute(
            "INSERT INTO item_master (codigo_barra, descricao, unidade, categoria, "
            "controla_serial, criado_por) VALUES (?, ?, ?, ?, ?, ?)",
            (codigo_barra, descricao, unidade, categoria, int(controla_serial), criado_por)
        )


def toggle_ativo(item_id: int):
    with db() as conn:
        conn.execute(
            "UPDATE item_master SET ativo = NOT ativo WHERE id = ?", (item_id,)
        )
