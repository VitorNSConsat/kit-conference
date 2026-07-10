# create_user.py  (rodar uma vez para criar o primeiro usuário)
from database import init_db, db
from app.auth import hash_password

init_db()
nome = input("Nome completo: ")
username = input("Usuário: ")
password = input("Senha: ")

with db() as conn:
    conn.execute(
        "INSERT INTO users (nome, username, password_hash) VALUES (?, ?, ?)",
        (nome, username, hash_password(password))
    )
print("Usuário criado com sucesso.")
