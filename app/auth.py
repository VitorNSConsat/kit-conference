from passlib.context import CryptContext
from functools import wraps
from fastapi import Request
from fastapi.responses import RedirectResponse, HTMLResponse
from database import db

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return pwd_context.verify(password, hashed)


def get_current_user(request: Request):
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row:
        return None
    user = dict(row)
    # Usuário desativado perde o acesso na hora, sem precisar esperar o
    # cookie expirar — a sessão já emitida deixa de valer.
    if not user.get("ativo", 1):
        return None
    return user


def is_admin(request: Request) -> bool:
    user = get_current_user(request)
    return bool(user and user.get("admin"))


def require_login(func):
    @wraps(func)
    async def wrapper(request: Request, *args, **kwargs):
        user = get_current_user(request)
        if not user:
            return RedirectResponse("/login", status_code=302)
        request.state.user = user
        return await func(request, *args, **kwargs)
    return wrapper


def require_admin(func):
    """Só administrador passa. Usado em tudo que exclui dados e na gestão
    de usuários. Esconder o botão na tela não basta — a rota é chamável
    direto, então a checagem tem que estar no servidor."""
    @wraps(func)
    async def wrapper(request: Request, *args, **kwargs):
        user = get_current_user(request)
        if not user:
            return RedirectResponse("/login", status_code=302)
        if not user.get("admin"):
            return HTMLResponse(
                "<h2 style='font-family:sans-serif;padding:32px'>Acesso negado</h2>"
                "<p style='font-family:sans-serif;padding:0 32px'>Esta ação é "
                "restrita a administradores.</p>"
                "<p style='padding:0 32px'><a href='/'>Voltar ao início</a></p>",
                status_code=403,
            )
        request.state.user = user
        return await func(request, *args, **kwargs)
    return wrapper
