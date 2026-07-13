import json
import os
import re
import uuid
from datetime import datetime, timezone, timedelta

# Brasília Time (UTC-3) — garante horário correto independente do fuso do servidor
BRT = timezone(timedelta(hours=-3))
from urllib.parse import quote
from fastapi import FastAPI, Request, Form, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
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


def _detectar_ip_lan() -> str:
    """Detecta o IP da máquina na LAN local (não localhost)."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))   # rota padrão — funciona em qualquer rede LAN
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


@app.on_event("startup")
def startup():
    init_db()
    import app.zpl as _zpl
    _zpl.EMPRESA_NOME = os.getenv("EMPRESA_NOME", "Sua Empresa")

    ip = _detectar_ip_lan()
    _tem_ssl = os.path.exists("certs/cert.pem") and os.path.exists("certs/key.pem")

    app.state.url_http  = f"http://{ip}:8080"
    app.state.url_https = f"https://{ip}:8011" if _tem_ssl else None
    app.state.tem_ssl = _tem_ssl

    if _tem_ssl:
        # QR aponta para HTTPS:8011 — iOS funciona direto com cert instalado
        # Android: aceita aviso "Avançado → Continuar" na primeira vez, depois lembra
        _zpl.SERVIDOR_URL = f"https://{ip}:8011"
        app.state.servidor_url = f"https://{ip}:8011"
        print(f"[KIT] HTTPS (QR + Admin): {app.state.servidor_url}")
        print(f"[KIT] HTTP  (alternativo): {app.state.url_http}")
    else:
        _zpl.SERVIDOR_URL = f"http://{ip}:8080"
        app.state.servidor_url = f"http://{ip}:8080"
        print(f"[KIT] HTTP: {app.state.servidor_url}")


def _parse_itens_form(form) -> list[dict]:
    """Extrai itens do formulário de template sem depender de índices sequenciais."""
    indices = sorted(
        int(m.group(1))
        for k in form.keys()
        for m in [re.match(r'^item_tipo_id_(\d+)$', k)]
        if m
    )
    itens = []
    for i in indices:
        tipo_id = form.get(f"item_tipo_id_{i}", "").strip()
        if not tipo_id:
            continue
        itens.append({
            "item_tipo_id": int(tipo_id),
            "quantidade_exigida": max(1, int(form.get(f"qtd_{i}", 1) or 1)),
            "obrigatorio": bool(form.get(f"obrigatorio_{i}")),
            "componente_codigo": (form.get(f"componente_codigo_{i}", "") or "").strip() or None,
            "requer_serial": bool(form.get(f"requer_serial_{i}")),
        })
    return itens


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


# ── Rede ──────────────────────────────────────────────────────────────────────

@app.get("/rede", response_class=HTMLResponse)
@require_login
async def rede(request: Request):
    import app.zpl as _zpl
    url_http  = getattr(app.state, "url_http",  _zpl.SERVIDOR_URL)
    url_https = getattr(app.state, "url_https", None)
    tem_ssl   = getattr(app.state, "tem_ssl",   False)

    def _make_qr_svg(url: str) -> str:
        try:
            import segno, io as _io, re
            qr = segno.make(url, error="l")
            buf = _io.BytesIO()
            qr.save(buf, kind="svg", scale=5, border=2, xmldecl=False, nl=False)
            svg = buf.getvalue().decode("utf-8")
            svg = re.sub(r'\s(width|height)="[^"]*"', '', svg, count=2)
            svg = svg.replace("<svg ", '<svg style="display:block;width:100%;max-width:200px;height:auto;margin:0 auto;" ', 1)
            return svg
        except Exception:
            return ""

    qr_ios     = _make_qr_svg(url_https) if url_https else _make_qr_svg(url_http)
    qr_android = _make_qr_svg(url_http)

    return render(request, "rede.html", {
        "url_http":    url_http,
        "url_https":   url_https,
        "servidor_url": url_https or url_http,
        "qr_ios":      qr_ios,
        "qr_android":  qr_android,
        "tem_ssl":     tem_ssl,
    })


# ── Certificado SSL (para iOS instalar) ──────────────────────────────────────

@app.get("/cert")
async def baixar_cert():
    """Download do certificado SSL para instalar no iOS/Android."""
    from fastapi.responses import Response as _Resp
    cert_path = "certs/cert.pem"
    if not os.path.exists(cert_path):
        return PlainTextResponse("Certificado não encontrado. Execute: python gerar_cert.py", status_code=404)
    with open(cert_path, "rb") as f:
        cert_bytes = f.read()
    return _Resp(
        content=cert_bytes,
        media_type="application/x-x509-ca-cert",
        headers={"Content-Disposition": 'attachment; filename="KitConference.crt"'},
    )


# ── Ping público (sem login) ─────────────────────────────────────────────────

@app.get("/ping")
async def ping():
    import app.zpl as _zpl
    return {"status": "ok", "servidor": _zpl.SERVIDOR_URL}


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


# ── Admin: Tipos de Item ──────────────────────────────────────────────────────

@app.post("/admin/tipos")
@require_login
async def admin_tipos_post(request: Request, nome: str = Form(...)):
    try:
        items_mod.criar_tipo(nome.strip())
    except Exception as e:
        itens = items_mod.listar_itens()
        tipos = items_mod.listar_tipos()
        return render(request, "admin_items.html",
                      {"itens": itens, "tipos": tipos,
                       "erro": f"Erro ao criar tipo: {e}"})
    return RedirectResponse("/admin/items?ok=tipo", status_code=302)


@app.post("/admin/tipos/importar")
@require_login
async def admin_tipos_importar(request: Request, arquivo: UploadFile = File(...)):
    conteudo = await arquivo.read()
    try:
        resultado = items_mod.importar_tipos_xlsx(conteudo)
        params = f"importado={resultado['criados']}&ignorado={resultado['ignorados']}"
    except Exception as e:
        params = f"erro_import={quote(str(e))}"
    return RedirectResponse(f"/admin/items?{params}", status_code=302)


@app.post("/admin/tipos/{tipo_id}/delete")
@require_login
async def admin_tipo_delete(request: Request, tipo_id: int):
    try:
        items_mod.deletar_tipo(tipo_id)
    except Exception:
        pass
    return RedirectResponse("/admin/items", status_code=302)


# ── Admin: Itens (Patrimônios) ────────────────────────────────────────────────

@app.get("/admin/items", response_class=HTMLResponse)
@require_login
async def admin_items(request: Request):
    itens = items_mod.listar_itens()
    tipos = items_mod.listar_tipos()
    return render(request, "admin_items.html", {"itens": itens, "tipos": tipos})


@app.post("/admin/items")
@require_login
async def admin_items_post(request: Request,
                           codigo_barra: str = Form(...),
                           item_tipo_id: int = Form(...)):
    user = get_current_user(request)
    try:
        items_mod.criar_item(codigo_barra.strip(), item_tipo_id, user["id"])
        return RedirectResponse("/admin/items?ok=1", status_code=302)
    except Exception as e:
        itens = items_mod.listar_itens()
        tipos = items_mod.listar_tipos()
        return render(request, "admin_items.html",
                      {"itens": itens, "tipos": tipos,
                       "erro": f"Erro ao salvar: {e}"})


@app.post("/admin/items/clear")
@require_login
async def admin_items_clear(request: Request):
    items_mod.apagar_todos_itens()
    return RedirectResponse("/admin/items", status_code=302)


@app.post("/admin/items/{item_id}/delete")
@require_login
async def admin_items_delete(request: Request, item_id: int):
    items_mod.deletar_item(item_id)
    return RedirectResponse("/admin/items", status_code=302)


# ── Admin: Templates ──────────────────────────────────────────────────────────

@app.get("/admin/templates", response_class=HTMLResponse)
@require_login
async def admin_templates(request: Request):
    todos = templates_mod.listar_todos()
    tipos_ativos = items_mod.listar_tipos(apenas_ativos=True)
    return render(request, "admin_templates.html",
                  {"templates": todos, "tipos_catalogo": tipos_ativos})


@app.post("/admin/templates")
@require_login
async def admin_templates_post(request: Request):
    user = get_current_user(request)
    form = await request.form()
    nome = form.get("nome", "").strip()
    cliente = form.get("cliente", "").strip()
    itens = _parse_itens_form(form)
    if not nome or not cliente or not itens:
        todos = templates_mod.listar_todos()
        tipos_ativos = items_mod.listar_tipos(apenas_ativos=True)
        return render(request, "admin_templates.html",
                      {"templates": todos, "tipos_catalogo": tipos_ativos,
                       "erro": "Preencha nome, cliente e ao menos 1 item."})
    templates_mod.criar_template(nome, cliente, user["id"], itens)
    return RedirectResponse("/admin/templates?ok=1", status_code=302)


@app.get("/admin/templates/{template_id}/edit", response_class=HTMLResponse)
@require_login
async def admin_template_edit_page(request: Request, template_id: int):
    template = templates_mod.buscar_template(template_id)
    if not template:
        return RedirectResponse("/admin/templates", status_code=302)
    itens = templates_mod.get_itens_template(template_id)
    tipos_ativos = items_mod.listar_tipos(apenas_ativos=True)
    return render(request, "admin_template_edit.html", {
        "template": template,
        "itens": itens,
        "tipos_catalogo": tipos_ativos,
    })


@app.post("/admin/templates/{template_id}/edit")
@require_login
async def admin_template_edit_post(request: Request, template_id: int):
    form = await request.form()
    nome = form.get("nome", "").strip()
    cliente = form.get("cliente", "").strip()
    itens = _parse_itens_form(form)
    if not nome or not cliente or not itens:
        template = templates_mod.buscar_template(template_id)
        itens_atuais = templates_mod.get_itens_template(template_id)
        tipos_ativos = items_mod.listar_tipos(apenas_ativos=True)
        return render(request, "admin_template_edit.html", {
            "template": template, "itens": itens_atuais,
            "tipos_catalogo": tipos_ativos,
            "erro": "Preencha nome, cliente e ao menos 1 item.",
        })
    templates_mod.atualizar_template(template_id, nome, cliente, itens)
    return RedirectResponse("/admin/templates?ok=editado", status_code=302)


@app.post("/admin/templates/{template_id}/delete")
@require_login
async def admin_template_delete(request: Request, template_id: int):
    try:
        templates_mod.deletar_template(template_id)
        return RedirectResponse("/admin/templates?ok=excluido", status_code=302)
    except ValueError as e:
        todos = templates_mod.listar_todos()
        tipos_ativos = items_mod.listar_tipos(apenas_ativos=True)
        return render(request, "admin_templates.html",
                      {"templates": todos, "tipos_catalogo": tipos_ativos,
                       "erro": str(e)})


@app.post("/admin/templates/{template_id}/nova-versao")
@require_login
async def admin_template_nova_versao(request: Request, template_id: int):
    user = get_current_user(request)
    templates_mod.nova_versao(template_id, user["id"])
    return RedirectResponse("/admin/templates?ok=versao", status_code=302)


@app.post("/admin/templates/{template_id}/toggle")
@require_login
async def admin_template_toggle(request: Request, template_id: int):
    templates_mod.toggle_ativo(template_id)
    return RedirectResponse("/admin/templates", status_code=302)


# ── Sessions ──────────────────────────────────────────────────────────────────

@app.get("/session/{sessao_id}", response_class=HTMLResponse)
@require_login
async def session_page(request: Request, sessao_id: int):
    session = sessions_mod.get_session(sessao_id)
    if not session:
        return RedirectResponse("/", status_code=302)
    if session["status"] != "em_andamento":
        return RedirectResponse("/", status_code=302)
    itens = templates_mod.get_itens_template(session["kit_template_id"])
    contagem = sessions_mod.get_contagem(sessao_id)
    return render(request, "session.html", {
        "session": session,
        "itens": itens,
        "contagem": contagem,
    })


@app.post("/session/{sessao_id}/cancel")
@require_login
async def session_cancel(request: Request, sessao_id: int):
    sessions_mod.cancel_session(sessao_id)
    return RedirectResponse("/", status_code=302)


@app.websocket("/ws/session/{sessao_id}")
async def ws_session(websocket: WebSocket, sessao_id: int):
    session_data = websocket.scope.get("session", {})
    user_id = session_data.get("user_id")
    await websocket.accept()
    if not user_id:
        await websocket.close(code=1008)
        return
    try:
        while True:
            data = await websocket.receive_text()
            data = data.strip()
            if not data:
                continue
            try:
                msg = json.loads(data)
                if msg.get("acao") == "identificar":
                    result = sessions_mod.register_scan(
                        sessao_id, msg["codigo"],
                        item_tipo_id=int(msg["item_tipo_id"])
                    )
                elif msg.get("acao") == "confirmar_componente":
                    result = sessions_mod.confirmar_componente(
                        sessao_id, msg["codigo_barra"], msg.get("quantidades", {})
                    )
                elif msg.get("acao") == "cancelar_serial":
                    result = sessions_mod.cancelar_serial(sessao_id)
                else:
                    result = {"resultado": "rejeitado", "mensagem": "Mensagem inválida."}
            except (json.JSONDecodeError, KeyError, ValueError):
                # Plain barcode scan — serial tem prioridade sobre bipagem normal
                pendente = sessions_mod.get_pendente_serial(sessao_id)
                if pendente:
                    result = sessions_mod.registrar_serial(sessao_id, data)
                else:
                    result = sessions_mod.checar_componente(sessao_id, data)
                    if result is None:
                        result = sessions_mod.register_scan(sessao_id, data)
            await websocket.send_json(result)
    except WebSocketDisconnect:
        pass

# ── Finalização ───────────────────────────────────────────────────────────────

@app.post("/session/{sessao_id}/finalize")
@require_login
async def session_finalize(request: Request, sessao_id: int,
                           veiculo: str = Form(""), garagem: str = Form("")):
    user = get_current_user(request)

    session_check = sessions_mod.get_session(sessao_id)
    if not session_check or session_check["status"] != "em_andamento":
        return RedirectResponse("/", status_code=302)

    validation = sessions_mod.validate_kit_complete(sessao_id)
    if validation["status"] != "completo":
        faltam = "; ".join(
            f"{i['descricao']} (faltam {i['faltam']})"
            for i in validation["itens_faltantes"]
        )
        return RedirectResponse(
            f"/session/{sessao_id}?erro={quote(faltam)}", status_code=302
        )

    session = sessions_mod.get_session(sessao_id)
    contagem = sessions_mod.get_contagem(sessao_id)
    itens_template = templates_mod.get_itens_template(session["kit_template_id"])

    itens_label = []
    for it in itens_template:
        qtd = contagem.get(it["item_tipo_id"], 0)
        if qtd > 0:
            itens_label.append({"descricao": it["descricao"], "quantidade": qtd})

    kit_id = str(uuid.uuid4())
    ts = datetime.now(tz=BRT)

    zpl = zpl_mod.generate_zpl(
        kit_id=kit_id,
        kit_nome=session["kit_nome"],
        cliente=session["cliente"],
        operador=session["operador_nome"],
        timestamp=ts,
        itens=itens_label,
        veiculo=veiculo.strip(),
        garagem=garagem.strip(),
    )

    html_label = zpl_mod.generate_html_label(
        kit_id=kit_id,
        kit_nome=session["kit_nome"],
        cliente=session["cliente"],
        operador=session["operador_nome"],
        timestamp=ts,
        itens=itens_label,
        veiculo=veiculo.strip(),
        garagem=garagem.strip(),
    )

    with db() as conn:
        ts_str = ts.strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "INSERT INTO kit_record (kit_id, sessao_id, kit_template_id, "
            "kit_template_versao, operador_id, veiculo, garagem, finalizado_em) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (kit_id, sessao_id, session["kit_template_id"],
             session["kit_template_versao"], user["id"],
             veiculo.strip(), garagem.strip(), ts_str)
        )
        conn.execute(
            "UPDATE scan_session SET status = 'finalizado', "
            "finalizado_em = ? WHERE id = ?",
            (ts_str, sessao_id)
        )
        conn.execute(
            "INSERT INTO print_queue (kit_id, zpl, html_label, solicitado_por) "
            "VALUES (?, ?, ?, ?)",
            (kit_id, zpl, html_label, user["id"])
        )

    return RedirectResponse(
        f"/session/{sessao_id}/complete?kit_id={kit_id}", status_code=302
    )


@app.get("/session/{sessao_id}/complete", response_class=HTMLResponse)
@require_login
async def session_complete(request: Request, sessao_id: int, kit_id: str):
    with db() as conn:
        pq_row = conn.execute(
            "SELECT * FROM print_queue WHERE kit_id = ? ORDER BY id DESC LIMIT 1",
            (kit_id,)
        ).fetchone()
    return render(request, "complete.html", {
        "kit_id": kit_id,
        "pq_id": dict(pq_row)["id"] if pq_row else None,
    })


# ── Fila de Impressão ─────────────────────────────────────────────────────────

@app.get("/print-queue", response_class=HTMLResponse)
@require_login
async def print_queue_page(request: Request):
    fila = pq_mod.listar_aguardando()
    return render(request, "print_queue.html", {"fila": fila})


@app.get("/print-queue/{pq_id}/zpl")
@require_login
async def print_queue_zpl(request: Request, pq_id: int):
    """Retorna o ZPL como download de arquivo .zpl para envio à Zebra."""
    from fastapi.responses import Response
    item = pq_mod.buscar(pq_id)
    if not item:
        return PlainTextResponse("Não encontrado", status_code=404)
    nome = f"etiqueta_{pq_id}.zpl"
    return Response(
        content=item["zpl"].encode("ascii", "replace"),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{nome}"'},
    )


@app.get("/print-queue/{pq_id}/etiqueta")
@require_login
async def print_queue_html_label(request: Request, pq_id: int):
    item = pq_mod.buscar(pq_id)
    if not item or not item.get("html_label"):
        return PlainTextResponse("Etiqueta HTML não disponível.", status_code=404)
    return HTMLResponse(item["html_label"])


@app.get("/print-queue/{pq_id}/preview")
@require_login
async def print_queue_preview(request: Request, pq_id: int):
    """Renderiza o ZPL como imagem PNG via Labelary (validação sem imprimir)."""
    import urllib.request as _urlreq
    from fastapi.responses import Response as _Resp
    item = pq_mod.buscar(pq_id)
    if not item:
        return PlainTextResponse("Não encontrado", status_code=404)
    zpl_bytes = item["zpl"].encode("ascii", "replace")
    # Labelary: 8 dpmm (203 DPI), 100x150mm = 3.94x5.91"
    url = "http://api.labelary.com/v1/printers/8dpmm/labels/3.94x5.91/0/"
    req = _urlreq.Request(url, data=zpl_bytes, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("Accept", "image/png")
    try:
        with _urlreq.urlopen(req, timeout=10) as resp:
            png = resp.read()
        return _Resp(content=png, media_type="image/png")
    except Exception as exc:
        return HTMLResponse(
            f'<body style="font-family:sans-serif;padding:20px;">'
            f'<h3>Erro ao renderizar via Labelary</h3><pre>{exc}</pre>'
            f'<p>Verifique se há conexão com a internet.</p></body>',
            status_code=502,
        )


@app.post("/print-queue/{pq_id}/impresso")
@require_login
async def print_queue_impresso(request: Request, pq_id: int):
    pq_mod.marcar_impresso(pq_id)
    return RedirectResponse("/print-queue", status_code=302)


@app.post("/print-queue/{pq_id}/cancelar")
@require_login
async def print_queue_cancelar(request: Request, pq_id: int):
    pq_mod.cancelar(pq_id)
    return RedirectResponse("/print-queue", status_code=302)


# ── Kit Detail (público — escaneado pelo QR code) ─────────────────────────────

@app.get("/kit/{kit_id}", response_class=HTMLResponse)
async def kit_detail(request: Request, kit_id: str):
    with db() as conn:
        kit = conn.execute(
            "SELECT kr.*, kt.nome AS kit_nome, kt.cliente, kt.versao, "
            "u.nome AS operador_nome "
            "FROM kit_record kr "
            "JOIN kit_template kt ON kt.id = kr.kit_template_id "
            "JOIN users u ON u.id = kr.operador_id "
            "WHERE kr.kit_id = ?",
            (kit_id,)
        ).fetchone()
        if not kit:
            return HTMLResponse("<h2>Kit não encontrado.</h2>", status_code=404)
        kit = dict(kit)

        itens = conn.execute(
            "SELECT it.nome AS tipo_nome, COUNT(*) AS quantidade, "
            "GROUP_CONCAT(si.codigo_barra, ', ') AS barcodes "
            "FROM scan_session_items si "
            "JOIN item_tipo it ON it.id = si.item_tipo_id "
            "WHERE si.sessao_id = ? "
            "GROUP BY si.item_tipo_id ORDER BY it.nome",
            (kit["sessao_id"],)
        ).fetchall()

    return render(request, "kit_detail.html", {
        "kit": kit,
        "itens": [dict(i) for i in itens],
    })


# ── Relatórios ────────────────────────────────────────────────────────────────

@app.get("/reports", response_class=HTMLResponse)
@require_login
async def reports(request: Request,
                  data_ini: str = "",
                  data_fim: str = "",
                  operador_id: str = ""):
    query = """
        SELECT kr.kit_id, kr.finalizado_em, kr.status,
               kr.veiculo, kr.garagem,
               kt.nome AS kit_nome, kt.cliente, kt.versao,
               u.nome AS operador_nome,
               pq.id AS pq_id
        FROM kit_record kr
        JOIN kit_template kt ON kt.id = kr.kit_template_id
        JOIN users u ON u.id = kr.operador_id
        LEFT JOIN print_queue pq ON pq.kit_id = kr.kit_id
        WHERE 1=1
    """
    params = []
    if data_ini:
        query += " AND DATE(kr.finalizado_em) >= ?"
        params.append(data_ini)
    if data_fim:
        query += " AND DATE(kr.finalizado_em) <= ?"
        params.append(data_fim)
    if operador_id:
        query += " AND kr.operador_id = ?"
        params.append(int(operador_id))
    query += " ORDER BY kr.finalizado_em DESC LIMIT 200"

    with db() as conn:
        rows = conn.execute(query, params).fetchall()
        usuarios = conn.execute("SELECT id, nome FROM users ORDER BY nome").fetchall()

    return render(request, "reports.html", {
        "kits": [dict(r) for r in rows],
        "usuarios": [dict(u) for u in usuarios],
        "data_ini": data_ini,
        "data_fim": data_fim,
        "operador_id": operador_id,
        "ok": request.query_params.get("ok", ""),
    })


@app.post("/reports/reprint/{kit_id}")
@require_login
async def reprint_kit(request: Request, kit_id: str):
    """Recria a entrada na fila de impressão para um kit já finalizado."""
    user = get_current_user(request)
    with db() as conn:
        pq_row = conn.execute(
            "SELECT * FROM print_queue WHERE kit_id = ? ORDER BY id DESC LIMIT 1",
            (kit_id,)
        ).fetchone()
    if not pq_row:
        return RedirectResponse("/reports?erro=Etiqueta+nao+encontrada", status_code=302)
    pq = dict(pq_row)
    with db() as conn:
        conn.execute(
            "INSERT INTO print_queue (kit_id, zpl, html_label, solicitado_por) VALUES (?,?,?,?)",
            (kit_id, pq["zpl"], pq.get("html_label"), user["id"])
        )
    return RedirectResponse("/print-queue?ok=reimpresso", status_code=302)


@app.get("/reports/{kit_id}/excel")
@require_login
async def report_excel(request: Request, kit_id: str):
    from fastapi.responses import Response as _Resp
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    from io import BytesIO

    with db() as conn:
        kit = conn.execute(
            "SELECT kr.*, kt.nome AS kit_nome, kt.cliente, kt.versao, "
            "u.nome AS operador_nome "
            "FROM kit_record kr "
            "JOIN kit_template kt ON kt.id = kr.kit_template_id "
            "JOIN users u ON u.id = kr.operador_id "
            "WHERE kr.kit_id = ?",
            (kit_id,)
        ).fetchone()
        if not kit:
            return PlainTextResponse("Kit não encontrado", status_code=404)
        kit = dict(kit)

        resumo = conn.execute(
            "SELECT it.nome AS tipo_nome, COUNT(*) AS quantidade "
            "FROM scan_session_items si "
            "JOIN item_tipo it ON it.id = si.item_tipo_id "
            "WHERE si.sessao_id = ? "
            "GROUP BY si.item_tipo_id ORDER BY it.nome",
            (kit["sessao_id"],)
        ).fetchall()
        resumo = [dict(r) for r in resumo]

        itens = conn.execute(
            "SELECT it.nome AS tipo_nome, si.codigo_barra, si.serial_number, si.bipado_em "
            "FROM scan_session_items si "
            "JOIN item_tipo it ON it.id = si.item_tipo_id "
            "WHERE si.sessao_id = ? "
            "ORDER BY it.nome, si.bipado_em",
            (kit["sessao_id"],)
        ).fetchall()
        itens = [dict(i) for i in itens]

    wb = openpyxl.Workbook()
    azul = "1A3A5C"
    branco = "FFFFFF"
    cinza = "F4F7FB"

    def hdr_cell(ws, row, col, value):
        c = ws.cell(row=row, column=col, value=value)
        c.font = Font(bold=True, color=branco)
        c.fill = PatternFill("solid", fgColor=azul)
        c.alignment = Alignment(horizontal="center", vertical="center")
        return c

    def meta_block(ws):
        meta = [
            ("Kit", kit["kit_nome"]),
            ("Cliente", kit["cliente"]),
            ("Versão", f"v{kit['versao']}"),
            ("Operador", kit["operador_nome"]),
            ("Veículo", kit.get("veiculo") or "—"),
            ("Garagem", kit.get("garagem") or "—"),
            ("Finalizado em", kit["finalizado_em"]),
        ]
        for r, (label, value) in enumerate(meta, 1):
            ws.cell(r, 1, label).font = Font(bold=True)
            ws.cell(r, 2, value)
        return len(meta) + 2  # blank row + next data row

    # ── Aba Resumo ──────────────────────────────────────────────────────────────
    ws1 = wb.active
    ws1.title = "Resumo"
    next_row = meta_block(ws1)
    for col, h in enumerate(["Tipo de Item", "Quantidade Bipada"], 1):
        hdr_cell(ws1, next_row, col, h)
    for i, r in enumerate(resumo):
        row = next_row + 1 + i
        ws1.cell(row, 1, r["tipo_nome"])
        ws1.cell(row, 2, r["quantidade"])
        if i % 2 == 0:
            for col in (1, 2):
                ws1.cell(row, col).fill = PatternFill("solid", fgColor=cinza)
    ws1.column_dimensions["A"].width = 32
    ws1.column_dimensions["B"].width = 20

    # ── Aba Detalhes ────────────────────────────────────────────────────────────
    ws2 = wb.create_sheet("Detalhes")
    next_row = meta_block(ws2)
    for col, h in enumerate(["Tipo de Item", "Código de Barras", "Serial Number", "Origem", "Bipado em"], 1):
        hdr_cell(ws2, next_row, col, h)
    for i, item in enumerate(itens):
        row = next_row + 1 + i
        codigo = item["codigo_barra"]
        if codigo.startswith("COMP:"):
            parts = codigo.split(":", 3)
            origem = "Saquinho"
            codigo_display = parts[1] if len(parts) >= 2 else codigo
        else:
            origem = "Bipagem direta"
            codigo_display = codigo
        ws2.cell(row, 1, item["tipo_nome"])
        ws2.cell(row, 2, codigo_display)
        ws2.cell(row, 3, item.get("serial_number") or "")
        ws2.cell(row, 4, origem)
        ws2.cell(row, 5, item.get("bipado_em", ""))
        if i % 2 == 0:
            for col in (1, 2, 3, 4, 5):
                ws2.cell(row, col).fill = PatternFill("solid", fgColor=cinza)
    ws2.column_dimensions["A"].width = 32
    ws2.column_dimensions["B"].width = 28
    ws2.column_dimensions["C"].width = 24
    ws2.column_dimensions["D"].width = 18
    ws2.column_dimensions["E"].width = 22

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    import re as _re
    safe = _re.sub(r'[^\w\-]', '_', kit["kit_nome"])
    data = (kit["finalizado_em"] or "")[:10]
    filename = f"kit_{safe}_{data}.xlsx"
    return _Resp(
        content=buf.read(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/reports/{kit_id}/delete")
@require_login
async def report_delete(request: Request, kit_id: str):
    sessions_mod.deletar_kit_record(kit_id)
    return RedirectResponse("/reports?ok=excluido", status_code=302)


# ── Reset do banco (apenas admin) ─────────────────────────────────────────────

@app.get("/admin/reset", response_class=HTMLResponse)
@require_login
async def reset_page(request: Request):
    return render(request, "admin_reset.html")


@app.post("/admin/reset")
@require_login
async def reset_confirm(request: Request, confirmacao: str = Form("")):
    if confirmacao != "CONFIRMAR":
        return render(request, "admin_reset.html", {"erro": "Digite CONFIRMAR para prosseguir."})
    with db() as conn:
        conn.execute("DELETE FROM print_queue")
        conn.execute("DELETE FROM scan_session_items")
        conn.execute("DELETE FROM scan_session")
        conn.execute("DELETE FROM kit_record")
        conn.execute("DELETE FROM item_master")
        conn.execute("DELETE FROM kit_template_items")
        conn.execute("DELETE FROM kit_template")
        conn.execute("DELETE FROM item_tipo")
        conn.execute("DELETE FROM users")
        # Reseta os autoincrement
        conn.execute("DELETE FROM sqlite_sequence WHERE name != 'sqlite_sequence'")
    # Limpa a sessão (o próprio usuário foi apagado)
    request.session.clear()
    return RedirectResponse("/login?ok=reset", status_code=302)


if __name__ == "__main__":
    import asyncio
    import uvicorn

    _tem_ssl = os.path.exists("certs/cert.pem") and os.path.exists("certs/key.pem")

    if _tem_ssl:
        # HTTPS:8011 (admin, iOS com cert — porta que iOS já "conhece" como segura)
        # HTTP:8080  (QR codes — porta nova, iOS nunca viu HTTPS aqui, funciona em qualquer device)
        async def _serve_dual():
            cfg_https = uvicorn.Config(
                "main:app", host="0.0.0.0", port=8011, reload=False,
                ssl_certfile="certs/cert.pem", ssl_keyfile="certs/key.pem",
            )
            cfg_http = uvicorn.Config(
                "main:app", host="0.0.0.0", port=8080, reload=False,
            )
            await asyncio.gather(
                uvicorn.Server(cfg_https).serve(),
                uvicorn.Server(cfg_http).serve(),
            )
        asyncio.run(_serve_dual())
    else:
        # Sem SSL: apenas HTTP:8080
        uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=False)
