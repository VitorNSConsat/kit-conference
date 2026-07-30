"""Cria um novo usuário no banco de dados.

Use este script para criar o PRIMEIRO administrador. Depois disso, a
gestão de usuários é feita pela tela /admin/usuarios, que já registra
tudo na auditoria:

    python criar_usuario.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv()

from database import init_db
import app.usuarios as usuarios_mod

init_db()

print("=== Criar Usuário ===")
nome     = input("Nome completo: ").strip()
username = input("Login (sem espaços): ").strip().lower()
senha    = input(f"Senha (mín. {usuarios_mod.SENHA_MINIMA} caracteres): ").strip()
resposta = input("É administrador? (só admin pode excluir dados) [s/N]: ").strip().lower()
admin    = resposta in ("s", "sim", "y", "yes")

try:
    usuarios_mod.criar(nome, username, senha, admin=admin)
except ValueError as e:
    print(f"Erro: {e}")
    sys.exit(1)

perfil = "ADMINISTRADOR" if admin else "comum (não pode excluir)"
print()
print("=" * 44)
print(f"Usuário '{username}' ({nome}) criado com sucesso!")
print(f"Perfil: {perfil}")
print("=" * 44)
