"""Cria um novo usuário no banco de dados.

Execute este script no servidor para criar o primeiro usuário administrador:
    python criar_usuario.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv()

from database import init_db, db
from app.auth import hash_password

init_db()

print("=== Criar Usuário ===")
nome     = input("Nome completo: ").strip()
username = input("Login (sem espaços): ").strip().lower()
senha    = input("Senha: ").strip()

if not nome or not username or not senha:
    print("Erro: nenhum campo pode ficar em branco.")
    sys.exit(1)

with db() as conn:
    existente = conn.execute(
        "SELECT id FROM users WHERE username = ?", (username,)
    ).fetchone()
    if existente:
        print(f"Erro: o login '{username}' já existe.")
        sys.exit(1)

    conn.execute(
        "INSERT INTO users (nome, username, password_hash) VALUES (?, ?, ?)",
        (nome, username, hash_password(senha))
    )

print()
print("=" * 40)
print(f"Usuário '{username}' ({nome}) criado com sucesso!")
print("=" * 40)
