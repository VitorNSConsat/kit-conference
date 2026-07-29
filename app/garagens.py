import sqlite3

from database import db, now_brt


def listar() -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT id, nome, criado_em FROM garagens ORDER BY nome"
        ).fetchall()
    return [dict(r) for r in rows]


def deletar(garagem_id: int):
    with db() as conn:
        conn.execute("DELETE FROM garagens WHERE id = ?", (garagem_id,))


def criar(nome: str) -> int | None:
    nome = nome.strip().upper()
    if not nome:
        return None
    try:
        with db() as conn:
            cur = conn.execute(
                "INSERT INTO garagens (nome, criado_em) VALUES (?, ?)",
                (nome, now_brt())
            )
            return cur.lastrowid
    except sqlite3.IntegrityError:
        return None
