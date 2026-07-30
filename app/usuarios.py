"""Gestão de usuários e a regra de quem pode apagar.

Modelo deliberadamente simples: dois perfis. Usuário comum faz tudo,
menos excluir. Admin faz tudo, inclusive excluir e administrar usuários.
"""

import sqlite3

from database import db, now_brt
from app.auth import hash_password

SENHA_MINIMA = 8


def listar() -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT id, nome, username, admin, ativo, criado_em FROM users ORDER BY nome"
        ).fetchall()
    return [dict(r) for r in rows]


def buscar(user_id: int) -> dict | None:
    with db() as conn:
        row = conn.execute(
            "SELECT id, nome, username, admin, ativo, criado_em FROM users WHERE id = ?",
            (user_id,)
        ).fetchone()
    return dict(row) if row else None


def validar_senha(senha: str) -> str | None:
    """Devolve a mensagem de erro, ou None se a senha serve."""
    if len(senha) < SENHA_MINIMA:
        return f"A senha precisa ter pelo menos {SENHA_MINIMA} caracteres."
    if senha.isdigit():
        return "A senha não pode ser só números."
    if senha.lower() in ("12345678", "password", "senha123", "administrador", "consat123"):
        return "Essa senha é fácil demais de adivinhar."
    return None


def criar(nome: str, username: str, senha: str, admin: bool) -> int:
    nome = nome.strip()
    username = username.strip().lower()
    if not nome or not username:
        raise ValueError("Nome e login são obrigatórios.")
    if " " in username:
        raise ValueError("O login não pode conter espaços.")
    erro = validar_senha(senha)
    if erro:
        raise ValueError(erro)
    try:
        with db() as conn:
            cur = conn.execute(
                "INSERT INTO users (nome, username, password_hash, admin, ativo, criado_em) "
                "VALUES (?, ?, ?, ?, 1, ?)",
                (nome, username, hash_password(senha), 1 if admin else 0, now_brt())
            )
            return cur.lastrowid
    except sqlite3.IntegrityError:
        raise ValueError(f"Já existe um usuário com o login '{username}'.")


def _contar_admins_ativos(conn, excluindo: int | None = None) -> int:
    sql = "SELECT COUNT(*) FROM users WHERE admin = 1 AND ativo = 1"
    params: list = []
    if excluindo is not None:
        sql += " AND id != ?"
        params.append(excluindo)
    return conn.execute(sql, params).fetchone()[0]


def definir_admin(user_id: int, admin: bool) -> None:
    """Promove ou rebaixa. Nunca deixa o sistema sem nenhum admin ativo —
    caso contrário ninguém mais consegue excluir nada nem gerir usuários,
    e só daria pra destravar mexendo direto no banco."""
    with db() as conn:
        if not admin and _contar_admins_ativos(conn, excluindo=user_id) == 0:
            raise ValueError(
                "Este é o último administrador ativo. Promova outro usuário "
                "a administrador antes de rebaixar este."
            )
        conn.execute("UPDATE users SET admin = ? WHERE id = ?", (1 if admin else 0, user_id))


def definir_ativo(user_id: int, ativo: bool) -> None:
    with db() as conn:
        if not ativo and _contar_admins_ativos(conn, excluindo=user_id) == 0:
            raise ValueError(
                "Este é o último administrador ativo. Promova outro usuário "
                "a administrador antes de desativar este."
            )
        conn.execute("UPDATE users SET ativo = ? WHERE id = ?", (1 if ativo else 0, user_id))


def trocar_senha(user_id: int, senha: str) -> None:
    erro = validar_senha(senha)
    if erro:
        raise ValueError(erro)
    with db() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (hash_password(senha), user_id)
        )


def deletar(user_id: int) -> None:
    """Exclusão de usuário é bloqueada de propósito: o id é referenciado por
    kits, sessões, validações e movimentos de estoque — apagar quebraria o
    histórico. Desative em vez de excluir."""
    raise ValueError(
        "Usuários não podem ser excluídos, porque o histórico de kits e "
        "movimentações aponta para eles. Use 'Desativar'."
    )
