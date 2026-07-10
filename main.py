import os
import uuid
from datetime import datetime
from fastapi import FastAPI, Request, Form, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from dotenv import load_dotenv

from database import init_db, db
from app.auth import hash_password, verify_password, get_current_user, require_login
import app.items as items_mod
import app.kit_templates as templates_mod
import app.sessions as sessions_mod
import app.zpl as zpl_mod
import app.print_queue as pq_mod

load_dotenv()

app = FastAPI(title="Conferência de Kits")
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SECRET_KEY", "dev-secret"))
app.mount("/static", StaticFiles(directory="static"), name="static")
jinja = Jinja2Templates(directory="templates")


@app.on_event("startup")
def startup():
    init_db()


def render(request: Request, template: str, ctx: dict = {}):
    user = get_current_user(request)
    return jinja.TemplateResponse(template, {"request": request, "user": user, **ctx})


# ── Auth ──────────────────────────────────────────────────────────────────────

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if get_current_user(request):
        return RedirectResponse("/", status_code=302)
    return render(request, "login.html")


@app.post("/login")
async def login_post(request: Request,
                     username: str = Form(...),
                     password: str = Form(...)):
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
    if row and verify_password(password, row["password_hash"]):
        request.session["user_id"] = row["id"]
        return RedirectResponse("/", status_code=302)
    return render(request, "login.html", {"erro": "Usuário ou senha incorretos."})


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)


# ── Home ──────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
@require_login
async def home(request: Request):
    templates_ativos = templates_mod.listar_templates_ativos()
    return render(request, "index.html", {"templates_ativos": templates_ativos})


@app.post("/session/start")
@require_login
async def session_start(request: Request, kit_template_id: int = Form(...)):
    user = get_current_user(request)
    sessao_id = sessions_mod.start_session(kit_template_id, user["id"])
    return RedirectResponse(f"/session/{sessao_id}", status_code=302)


# ── Placeholder para rotas adicionadas nas próximas tasks ────────────────────
# (Tasks 3-11 adicionam rotas aqui)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
