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


# ── Admin: Itens ──────────────────────────────────────────────────────────────

@app.get("/admin/items", response_class=HTMLResponse)
@require_login
async def admin_items(request: Request):
    itens = items_mod.listar_itens()
    return render(request, "admin_items.html", {"itens": itens})


@app.post("/admin/items")
@require_login
async def admin_items_post(request: Request,
                           codigo_barra: str = Form(...),
                           descricao: str = Form(...),
                           unidade: str = Form("UN"),
                           categoria: str = Form(""),
                           controla_serial: str = Form("")):
    user = get_current_user(request)
    try:
        items_mod.criar_item(
            codigo_barra.strip(), descricao.strip(), unidade.strip(),
            categoria.strip(), bool(controla_serial), user["id"]
        )
        return RedirectResponse("/admin/items?ok=1", status_code=302)
    except Exception as e:
        itens = items_mod.listar_itens()
        return render(request, "admin_items.html",
                      {"itens": itens, "erro": f"Erro ao salvar: {e}"})


@app.post("/admin/items/{item_id}/toggle")
@require_login
async def admin_items_toggle(request: Request, item_id: int):
    items_mod.toggle_ativo(item_id)
    return RedirectResponse("/admin/items", status_code=302)


# ── Admin: Templates ──────────────────────────────────────────────────────────

@app.get("/admin/templates", response_class=HTMLResponse)
@require_login
async def admin_templates(request: Request):
    todos = templates_mod.listar_todos()
    itens_catalogo = items_mod.listar_itens()
    itens_ativos = [i for i in itens_catalogo if i["ativo"]]
    return render(request, "admin_templates.html",
                  {"templates": todos, "itens_catalogo": itens_ativos})


@app.post("/admin/templates")
@require_login
async def admin_templates_post(request: Request):
    user = get_current_user(request)
    form = await request.form()
    nome = form.get("nome", "").strip()
    cliente = form.get("cliente", "").strip()
    # itens vêm como: codigo_barra_0, qtd_0, obrigatorio_0, ...
    itens = []
    i = 0
    while f"codigo_barra_{i}" in form:
        cb = form.get(f"codigo_barra_{i}", "").strip()
        qtd = int(form.get(f"qtd_{i}", 1))
        obrig = bool(form.get(f"obrigatorio_{i}"))
        if cb:
            itens.append({"codigo_barra": cb, "quantidade_exigida": qtd, "obrigatorio": obrig})
        i += 1
    if not nome or not cliente or not itens:
        todos = templates_mod.listar_todos()
        itens_catalogo = [x for x in items_mod.listar_itens() if x["ativo"]]
        return render(request, "admin_templates.html",
                      {"templates": todos, "itens_catalogo": itens_catalogo,
                       "erro": "Preencha nome, cliente e ao menos 1 item."})
    templates_mod.criar_template(nome, cliente, user["id"], itens)
    return RedirectResponse("/admin/templates?ok=1", status_code=302)


@app.post("/admin/templates/{template_id}/nova-versao")
@require_login
async def admin_template_nova_versao(request: Request, template_id: int):
    user = get_current_user(request)
    novo_id = templates_mod.nova_versao(template_id, user["id"])
    return RedirectResponse(f"/admin/templates?ok=versao", status_code=302)


@app.post("/admin/templates/{template_id}/toggle")
@require_login
async def admin_template_toggle(request: Request, template_id: int):
    templates_mod.toggle_ativo(template_id)
    return RedirectResponse("/admin/templates", status_code=302)


# ── Placeholder para rotas adicionadas nas próximas tasks ────────────────────
# (Tasks 6-11 adicionam rotas aqui)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
