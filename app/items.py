from database import db


# ── Tipos de item ──────────────────────────────────────────────────────────────

def listar_tipos(apenas_ativos: bool = False) -> list:
    with db() as conn:
        if apenas_ativos:
            rows = conn.execute(
                "SELECT * FROM item_tipo WHERE ativo = 1 ORDER BY nome"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM item_tipo ORDER BY nome"
            ).fetchall()
    return [dict(r) for r in rows]


def listar_tipos_para_kit(template_id: int) -> list:
    """Retorna apenas os tipos presentes no template (para o modal de identificação)."""
    with db() as conn:
        rows = conn.execute(
            "SELECT it.id, it.nome FROM item_tipo it "
            "JOIN kit_template_items ki ON ki.item_tipo_id = it.id "
            "WHERE ki.kit_template_id = ? AND it.ativo = 1 ORDER BY it.nome",
            (template_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def criar_tipo(nome: str) -> int:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO item_tipo (nome) VALUES (?)", (nome.strip(),)
        )
        return cur.lastrowid


def deletar_tipo(tipo_id: int):
    with db() as conn:
        conn.execute("DELETE FROM item_tipo WHERE id = ?", (tipo_id,))


def toggle_tipo(tipo_id: int):
    with db() as conn:
        conn.execute(
            "UPDATE item_tipo SET ativo = 1 - ativo WHERE id = ?", (tipo_id,)
        )


# ── Patrimônios (item_master) ──────────────────────────────────────────────────

def listar_itens() -> list:
    with db() as conn:
        rows = conn.execute(
            "SELECT i.*, t.nome AS descricao, u.nome AS criado_por_nome "
            "FROM item_master i "
            "JOIN item_tipo t ON t.id = i.item_tipo_id "
            "LEFT JOIN users u ON u.id = i.criado_por "
            "ORDER BY t.nome, i.codigo_barra"
        ).fetchall()
    return [dict(r) for r in rows]


def buscar_item(codigo_barra: str) -> dict | None:
    with db() as conn:
        row = conn.execute(
            "SELECT i.*, t.nome AS descricao "
            "FROM item_master i "
            "JOIN item_tipo t ON t.id = i.item_tipo_id "
            "WHERE i.codigo_barra = ? AND i.ativo = 1",
            (codigo_barra,)
        ).fetchone()
    return dict(row) if row else None


def criar_item(codigo_barra: str, item_tipo_id: int, criado_por: int) -> int:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO item_master (codigo_barra, item_tipo_id, criado_por) "
            "VALUES (?, ?, ?)",
            (codigo_barra, item_tipo_id, criado_por)
        )
        return cur.lastrowid


def deletar_item(item_id: int):
    with db() as conn:
        conn.execute("DELETE FROM item_master WHERE id = ?", (item_id,))


def apagar_todos_itens():
    with db() as conn:
        conn.execute("DELETE FROM item_master")


def toggle_item(item_id: int):
    with db() as conn:
        conn.execute(
            "UPDATE item_master SET ativo = 1 - ativo WHERE id = ?", (item_id,)
        )
