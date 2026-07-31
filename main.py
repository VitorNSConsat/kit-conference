import json
import os
import re
import uuid
from datetime import datetime, timezone, timedelta

# Brasília Time (UTC-3) — garante horário correto independente do fuso do servidor
BRT = timezone(timedelta(hours=-3))
from urllib.parse import quote
from fastapi import FastAPI, Request, Form, WebSocket, WebSocketDisconnect, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from dotenv import load_dotenv

from database import init_db, db, now_brt
from app.auth import (hash_password, verify_password, get_current_user,
                      require_login, require_admin, require_permission, is_admin)
import app.items as items_mod
import app.kit_templates as templates_mod
import app.sessions as sessions_mod
import app.zpl as zpl_mod
import app.print_queue as pq_mod
import app.estoque as estoque_mod
import app.validacoes as validacoes_mod
import app.veiculos as veiculos_mod
import app.clientes as clientes_mod
import app.garagens as garagens_mod
import app.codigos_gerados as codigos_gerados_mod
import app.prateleira as prateleira_mod
import app.pedidos as pedidos_mod
import app.consumo as consumo_mod
import app.auditoria as auditoria_mod
import app.usuarios as usuarios_mod
import app.producao as producao_mod
import app.permissoes as permissoes_mod

load_dotenv()

_MOBILE_UA = re.compile(r'(Mobile|Android|iPhone|iPad|iPod)', re.IGNORECASE)

# Rotas GET permitidas em dispositivos móveis (bipagem + estoque)
_MOBILE_OK_EXACT = {'/mobile', '/login', '/logout', '/ping', '/cert', '/estoque'}
_MOBILE_OK_PREFIX = ('/static/', '/session/', '/ws/', '/kit/', '/admin/estoque', '/estoque/', '/prateleira/', '/producao/')


class _MobileGateMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method != 'GET':
            return await call_next(request)
        if not _MOBILE_UA.search(request.headers.get('user-agent', '')):
            return await call_next(request)
        path = request.url.path
        if path in _MOBILE_OK_EXACT or any(path.startswith(p) for p in _MOBILE_OK_PREFIX):
            return await call_next(request)
        return RedirectResponse('/mobile', status_code=302)


class _AuditoriaMiddleware(BaseHTTPMiddleware):
    """Grava toda requisição que altera dados.

    Fica no middleware, e não em cada rota, porque cobertura é o requisito:
    rota criada amanhã já nasce auditada. Roda DEPOIS da resposta e nunca
    propaga erro — auditoria com defeito não pode derrubar a operação.
    """

    _IGNORAR = ("/static/", "/ping")

    async def dispatch(self, request: Request, call_next):
        caminho = request.url.path
        if any(caminho.startswith(p) for p in self._IGNORAR):
            return await call_next(request)

        if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
            # GET não altera dados e logar todos inundaria a tabela — mas
            # uma tentativa NEGADA é exatamente o sinal que interessa quando
            # alguém está sondando o que consegue acessar.
            resposta = await call_next(request)
            if resposta.status_code == 403:
                try:
                    user = get_current_user(request)
                    auditoria_mod.registrar(
                        user_id=user["id"] if user else None,
                        user_nome=user["nome"] if user else None,
                        acao="ACESSO NEGADO",
                        metodo=request.method, caminho=caminho,
                        detalhe="", ip=_ip_do_cliente(request),
                        status=403,
                    )
                except Exception:
                    pass
            return resposta

        # O corpo precisa ser lido aqui para virar detalhe do log, mas ler
        # consome o stream — então reinjetamos para a rota receber intacto.
        detalhe = ""
        try:
            corpo = await request.body()

            async def _receive():
                return {"type": "http.request", "body": corpo, "more_body": False}

            request._receive = _receive

            tipo = request.headers.get("content-type", "")
            if corpo and ("form-urlencoded" in tipo or "multipart/form-data" in tipo):
                detalhe = auditoria_mod._resumir_form(await request.form())
                request._receive = _receive   # form() reconsome; restaura
        except Exception:
            detalhe = "<corpo nao capturado>"

        resposta = await call_next(request)

        try:
            user = None
            try:
                user = get_current_user(request)
            except Exception:
                pass
            # No POST /login o usuário só existe depois da resposta; o nome
            # digitado já foi para o detalhe, então o log não fica anônimo.
            auditoria_mod.registrar(
                user_id=user["id"] if user else None,
                user_nome=user["nome"] if user else None,
                acao=auditoria_mod.classificar(caminho),
                metodo=request.method,
                caminho=caminho,
                detalhe=detalhe,
                ip=_ip_do_cliente(request),
                status=resposta.status_code,
            )
        except Exception as e:
            print(f"[AUDITORIA] falha ao gravar {request.method} {caminho}: {e}")

        return resposta


app = FastAPI(title="Conferência de Kits")

# COOKIE_SECURE=1 marca o cookie de sessão como "só por HTTPS". Fica
# desligado por padrão porque o acesso pela LAN é HTTP puro (porta 8080) —
# ligado ali, o navegador simplesmente não manda o cookie e ninguém
# consegue logar. Ligue quando o acesso passar a ser só pelo domínio
# HTTPS (ex: atrás do Cloudflare Tunnel).
_COOKIE_SECURE = os.getenv("COOKIE_SECURE", "").strip() in ("1", "true", "True")

# Planilha grande é lida inteira na memória; sem teto, um upload de 1 GB
# derruba o processo. 25 MB cobre com folga qualquer BOM/planilha real.
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "25")) * 1024 * 1024


async def _ler_upload(arquivo) -> bytes:
    """Lê um upload recusando arquivos acima do teto."""
    conteudo = await arquivo.read()
    if len(conteudo) > MAX_UPLOAD_BYTES:
        raise ValueError(
            f"Arquivo muito grande ({len(conteudo) // (1024*1024)} MB). "
            f"O limite é {MAX_UPLOAD_BYTES // (1024*1024)} MB."
        )
    return conteudo


_SECRET_KEY = os.getenv("SECRET_KEY", "").strip()
if not _SECRET_KEY:
    # Sem SECRET_KEY, qualquer um forja o cookie de sessão e entra como
    # quem quiser. Numa máquina exposta isso é crítico, então o processo
    # se recusa a subir; em uso local o aviso é gritante mas não trava.
    if _COOKIE_SECURE or os.getenv("SERVIDOR_URL", "").startswith("https://"):
        raise RuntimeError(
            "SECRET_KEY nao definido no .env. Como este servidor esta configurado "
            "para acesso externo, subir com a chave padrao permitiria a qualquer "
            "pessoa forjar uma sessao. Defina SECRET_KEY antes de iniciar."
        )
    _SECRET_KEY = "dev-secret"
    print("[KIT] AVISO: SECRET_KEY nao definido — usando chave de desenvolvimento. "
          "NAO exponha este servidor sem definir SECRET_KEY no .env.")

# Ordem importa: quem é adicionado por último fica por fora. A auditoria
# precisa enxergar a sessão, então entra ANTES do SessionMiddleware para
# ficar por dentro dele.
app.add_middleware(_AuditoriaMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=_SECRET_KEY,
    same_site="lax",
    https_only=_COOKIE_SECURE,
    max_age=12 * 60 * 60,   # 12h — uma jornada; antes eram 14 dias
)
app.add_middleware(_MobileGateMiddleware)
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
        url_local = f"https://{ip}:8011"
    else:
        url_local = f"http://{ip}:8080"

    # SERVIDOR_URL do .env manda: é o endereço que vai no QR da etiqueta.
    # Sem ele, cai no IP da LAN (funciona só dentro do galpão). Com um
    # domínio público (ex: atrás do Cloudflare Tunnel), a etiqueta impressa
    # abre de qualquer lugar — por isso o valor configurado nunca é
    # sobrescrito pela detecção automática.
    url_publica = (os.getenv("SERVIDOR_URL") or "").strip().rstrip("/")

    _zpl.SERVIDOR_URL = url_publica or url_local
    app.state.servidor_url = _zpl.SERVIDOR_URL

    if url_publica:
        print(f"[KIT] Endereco publico (QR das etiquetas): {url_publica}")
        print(f"[KIT] Acesso local: {url_local}")
    elif _tem_ssl:
        print(f"[KIT] HTTPS (QR + Admin): {url_local}")
        print(f"[KIT] HTTP  (alternativo): {app.state.url_http}")
    else:
        print(f"[KIT] HTTP: {url_local}")


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
    alertas_estoque = estoque_mod.alertas_abaixo_minimo() if user else []
    pode = (lambda chave: permissoes_mod.tem_permissao(user, chave)) if user else (lambda chave: False)
    return jinja.TemplateResponse(template, {"request": request, "user": user, "alertas_estoque": alertas_estoque, "pode": pode, **ctx})


# ── Auth ──────────────────────────────────────────────────────────────────────

# Freio de força bruta. Guardado em memória de propósito: o app roda em um
# processo só, e perder a contagem num restart é aceitável — o objetivo é
# tornar inviável varrer senhas, não ser um cofre distribuído.
_LOGIN_MAX_TENTATIVAS = 8
_LOGIN_JANELA_SEG = 15 * 60
_login_tentativas: dict[str, list[float]] = {}


def _ip_do_cliente(request: Request) -> str:
    """IP real de quem chamou.

    Atrás de um proxy (Cloudflare Tunnel), request.client.host é o IP do
    proxy — igual para todo mundo — o que faria o freio de login trancar
    todos os usuários de uma vez. Nesse caso o IP verdadeiro vem no
    cabeçalho CF-Connecting-IP.

    Só confiamos no cabeçalho quando TRUST_PROXY_IP=1, porque quem fala
    direto com o app (acesso pela LAN) pode forjar esse cabeçalho e
    escapar do limite trocando o valor a cada tentativa.
    """
    if os.getenv("TRUST_PROXY_IP", "").strip() in ("1", "true", "True"):
        cf = request.headers.get("cf-connecting-ip")
        if cf:
            return cf.strip()
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
    return request.client.host if request.client else "?"


def _login_chave(request: Request, username: str) -> str:
    return f"{_ip_do_cliente(request)}|{username.lower()}"


def _login_bloqueado(chave: str) -> int:
    """Segundos restantes de bloqueio, ou 0 se liberado."""
    import time
    agora = time.time()
    tentativas = [t for t in _login_tentativas.get(chave, []) if agora - t < _LOGIN_JANELA_SEG]
    _login_tentativas[chave] = tentativas
    if len(tentativas) < _LOGIN_MAX_TENTATIVAS:
        return 0
    return int(_LOGIN_JANELA_SEG - (agora - tentativas[0]))


def _login_falhou(chave: str) -> None:
    import time
    _login_tentativas.setdefault(chave, []).append(time.time())


def _login_ok(chave: str) -> None:
    _login_tentativas.pop(chave, None)


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if get_current_user(request):
        next_url = request.query_params.get("next", "/")
        return RedirectResponse(next_url if next_url.startswith("/") and not next_url.startswith("//") else "/", status_code=302)
    return render(request, "login.html", {"next": request.query_params.get("next", "")})


@app.post("/login")
async def login_post(request: Request):
    form = await request.form()
    username = str(form.get("username", "")).strip()
    password = str(form.get("password", "")).strip()
    next_url = str(form.get("next", "")).strip()
    if not (next_url.startswith("/") and not next_url.startswith("//")):
        next_url = "/"

    chave = _login_chave(request, username)
    espera = _login_bloqueado(chave)
    if espera:
        minutos = max(1, espera // 60)
        return render(request, "login.html", {
            "erro": f"Muitas tentativas. Tente novamente em {minutos} minuto(s).",
            "next": next_url,
        })

    with db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
    if row and not row["ativo"]:
        return render(request, "login.html", {
            "erro": "Este usuário está desativado. Procure um administrador.",
            "next": next_url,
        })

    if row and verify_password(password, row["password_hash"]):
        _login_ok(chave)
        # Descarta qualquer conteúdo de sessão anterior antes de autenticar,
        # para que um valor plantado na sessão pré-login não sobreviva à
        # troca de identidade (fixação de sessão).
        request.session.clear()
        request.session["user_id"] = row["id"]
        return RedirectResponse(next_url, status_code=302)

    _login_falhou(chave)
    return render(request, "login.html", {"erro": "Usuário ou senha incorretos.", "next": next_url})


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)


# ── Usuários (só admin) ───────────────────────────────────────────────────────

@app.get("/admin/usuarios", response_class=HTMLResponse)
@require_admin
async def admin_usuarios(request: Request):
    usuarios = usuarios_mod.listar()
    negadas_por_usuario = {u["id"]: permissoes_mod.negadas_do_usuario(u["id"]) for u in usuarios}
    return render(request, "admin_usuarios.html", {
        "usuarios": usuarios,
        "permissoes": permissoes_mod.PERMISSOES,
        "negadas_por_usuario": negadas_por_usuario,
    })


@app.post("/admin/usuarios")
@require_admin
async def admin_usuarios_criar(request: Request):
    form = await request.form()
    try:
        usuarios_mod.criar(
            nome=str(form.get("nome", "")),
            username=str(form.get("username", "")),
            senha=str(form.get("senha", "")),
            admin=bool(form.get("admin")),
        )
    except ValueError as e:
        return RedirectResponse("/admin/usuarios?erro=" + quote(str(e)), status_code=302)
    return RedirectResponse("/admin/usuarios?ok=criado", status_code=302)


@app.post("/admin/usuarios/{user_id}/admin")
@require_admin
async def admin_usuario_toggle_admin(request: Request, user_id: int):
    alvo = usuarios_mod.buscar(user_id)
    if not alvo:
        raise HTTPException(status_code=404)
    try:
        usuarios_mod.definir_admin(user_id, not alvo["admin"])
    except ValueError as e:
        return RedirectResponse("/admin/usuarios?erro=" + quote(str(e)), status_code=302)
    return RedirectResponse("/admin/usuarios?ok=perfil", status_code=302)


@app.post("/admin/usuarios/{user_id}/ativo")
@require_admin
async def admin_usuario_toggle_ativo(request: Request, user_id: int):
    alvo = usuarios_mod.buscar(user_id)
    if not alvo:
        raise HTTPException(status_code=404)
    try:
        usuarios_mod.definir_ativo(user_id, not alvo["ativo"])
    except ValueError as e:
        return RedirectResponse("/admin/usuarios?erro=" + quote(str(e)), status_code=302)
    return RedirectResponse("/admin/usuarios?ok=status", status_code=302)


@app.post("/admin/usuarios/{user_id}/permissoes")
@require_admin
async def admin_usuario_permissoes(request: Request, user_id: int):
    alvo = usuarios_mod.buscar(user_id)
    if not alvo:
        raise HTTPException(status_code=404)
    form = await request.form()
    permitidas = {chave for chave in permissoes_mod.PERMISSOES if form.get(chave)}
    permissoes_mod.definir_permissoes(user_id, permitidas)
    return RedirectResponse("/admin/usuarios?ok=permissoes", status_code=302)


@app.post("/admin/usuarios/{user_id}/senha")
@require_admin
async def admin_usuario_senha(request: Request, user_id: int):
    form = await request.form()
    try:
        usuarios_mod.trocar_senha(user_id, str(form.get("senha", "")))
    except ValueError as e:
        return RedirectResponse("/admin/usuarios?erro=" + quote(str(e)), status_code=302)
    return RedirectResponse("/admin/usuarios?ok=senha", status_code=302)


# ── Auditoria (só admin) ──────────────────────────────────────────────────────

@app.get("/admin/auditoria", response_class=HTMLResponse)
@require_admin
async def admin_auditoria(request: Request,
                          data_ini: str = "", data_fim: str = "",
                          user_id: str = "", acao: str = ""):
    return render(request, "admin_auditoria.html", {
        "registros": auditoria_mod.listar(data_ini, data_fim, user_id, acao),
        "usuarios": usuarios_mod.listar(),
        "acoes": auditoria_mod.acoes_distintas(),
        "data_ini": data_ini, "data_fim": data_fim,
        "filtro_user_id": user_id, "filtro_acao": acao,
    })


# ── Rede ──────────────────────────────────────────────────────────────────────

@app.get("/rede", response_class=HTMLResponse)
@require_permission("ver_rede")
async def rede(request: Request):
    import app.zpl as _zpl
    url_http  = getattr(app.state, "url_http",  _zpl.SERVIDOR_URL)
    url_https = getattr(app.state, "url_https", None)
    tem_ssl   = getattr(app.state, "tem_ssl",   False)

    def _make_qr_svg(url: str) -> str:
        try:
            import segno, io as _io, re
            qr = segno.make(url, error="q")
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
    sessoes_em_andamento = sessions_mod.listar_sessoes_em_andamento()
    return render(request, "index.html", {
        "templates_kit": [t for t in templates_ativos if t.get("tipo", "kit") == "kit"],
        "templates_pedido": [t for t in templates_ativos if t.get("tipo") == "pedido"],
        "sessoes_em_andamento": sessoes_em_andamento,
    })


@app.post("/session/start")
@require_login
async def session_start(request: Request, kit_template_id: int = Form(...)):
    user = get_current_user(request)
    sessao_id = sessions_mod.start_session(kit_template_id, user["id"])
    return RedirectResponse(f"/session/{sessao_id}/destino", status_code=302)


# ── Admin: Tipos de Item ──────────────────────────────────────────────────────


@app.post("/admin/tipos/importar")
@require_login
async def admin_tipos_importar(request: Request, arquivo: UploadFile = File(...)):
    try:
        conteudo = await _ler_upload(arquivo)
        resultado = items_mod.importar_tipos_xlsx(conteudo)
        params = f"importado={resultado['criados']}&ignorado={resultado['ignorados']}"
    except Exception as e:
        params = f"erro_import={quote(str(e))}"
    return RedirectResponse(f"/admin/items?{params}", status_code=302)


@app.post("/admin/tipos/importar-bom")
@require_login
async def admin_tipos_importar_bom(request: Request, arquivo: UploadFile = File(...)):
    user = get_current_user(request)
    try:
        conteudo = await _ler_upload(arquivo)
        resultado = items_mod.importar_bom_xlsx(conteudo, user["id"])
        if "erro" in resultado:
            params = f"erro_import={quote(resultado['erro'])}"
        else:
            t, i = resultado["tipos_criados"], resultado["itens_criados"]
            ign = resultado["ignorados"]
            params = f"importado_bom=1&tipos={t}&itens={i}&ignorado={ign}"
    except Exception as e:
        params = f"erro_import={quote(str(e))}"
    return RedirectResponse(f"/admin/items?{params}", status_code=302)


@app.post("/admin/tipos/{tipo_id}/toggle-reutilizavel")
@require_login
async def admin_tipo_toggle_reutilizavel(request: Request, tipo_id: int):
    items_mod.alternar_reutilizavel_tipo(tipo_id)
    return RedirectResponse("/admin/items", status_code=302)


@app.post("/admin/tipos/{tipo_id}/toggle-controle-externo")
@require_login
async def admin_tipo_toggle_controle_externo(request: Request, tipo_id: int):
    items_mod.alternar_controle_externo(tipo_id)
    return RedirectResponse("/admin/items", status_code=302)


@app.post("/admin/tipos/{tipo_id}/toggle-requer-serial")
@require_login
async def admin_tipo_toggle_requer_serial(request: Request, tipo_id: int):
    items_mod.alternar_requer_serial(tipo_id)
    return RedirectResponse("/admin/items", status_code=302)


@app.post("/admin/tipos/{tipo_id}/toggle-unidade")
@require_login
async def admin_tipo_toggle_unidade(request: Request, tipo_id: int):
    items_mod.alternar_unidade_tipo(tipo_id)
    return RedirectResponse("/admin/items", status_code=302)


@app.post("/admin/tipos/{tipo_id}/renomear")
@require_login
async def admin_tipo_renomear(request: Request, tipo_id: int):
    form = await request.form()
    novo_nome = (form.get("nome") or "").strip()
    if novo_nome:
        try:
            items_mod.renomear_tipo(tipo_id, novo_nome)
        except Exception:
            pass
    return RedirectResponse("/admin/items", status_code=302)


@app.post("/admin/tipos/{tipo_id}/delete")
@require_admin
async def admin_tipo_delete(request: Request, tipo_id: int):
    try:
        items_mod.deletar_tipo(tipo_id)
        return RedirectResponse("/admin/items", status_code=302)
    except Exception:
        deps = items_mod.buscar_dependencias_tipo(tipo_id)
        return render(request, "admin_items.html", {
            **_admin_items_context(),
            "tipo_com_erro": deps,
        })


@app.post("/admin/tipos/{tipo_id}/delete-force")
@require_admin
async def admin_tipo_delete_force(request: Request, tipo_id: int):
    items_mod.deletar_tipo_cascade(tipo_id)
    return RedirectResponse("/admin/items", status_code=302)


@app.post("/admin/tipos/{tipo_id}/set-codigo-fixo")
@require_login
async def admin_tipo_set_codigo_fixo(request: Request, tipo_id: int):
    form = await request.form()
    codigo = str(form.get("codigo_fixo", "")).strip()
    items_mod.definir_codigo_fixo(tipo_id, codigo or None)
    return RedirectResponse("/admin/items", status_code=302)


# ── Admin: Itens (Patrimônios) ────────────────────────────────────────────────

def _admin_items_context() -> dict:
    return {
        "itens": items_mod.listar_itens(),
        "tipos": items_mod.listar_tipos(),
        "estoque_por_tipo": {e["item_tipo_id"]: e for e in estoque_mod.listar_estoque()},
        "codigos_gerados": codigos_gerados_mod.listar(),
        "estoque_itens": estoque_mod.listar_estoque(),
        "status_compra_opcoes": estoque_mod.STATUS_COMPRA,
    }


@app.get("/admin/items", response_class=HTMLResponse)
@require_login
async def admin_items(request: Request):
    return render(request, "admin_items.html", _admin_items_context())


@app.get("/admin/gerar-codigo/etiqueta", response_class=HTMLResponse)
@require_login
async def admin_gerar_codigo_etiqueta(request: Request, texto: str = ""):
    """Gera uma etiqueta avulsa (QR + código de barras do mesmo texto livre),
    sem precisar de um item_tipo ou registro de estoque — para saquinhos e
    outros códigos de componente definidos na hora de montar um kit."""
    import app.zpl as _zpl
    texto = texto.strip()
    if not texto:
        raise HTTPException(status_code=400, detail="Texto é obrigatório.")
    user = get_current_user(request)
    codigos_gerados_mod.registrar(texto, user["id"])
    html = _zpl.generate_estoque_html_label(tipo_nome=texto, codigo_barra=texto, url_qr=texto)
    return HTMLResponse(content=html)


@app.post("/admin/codigos-gerados/{codigo_id}/toggle-reciclavel")
@require_login
async def admin_codigo_gerado_toggle_reciclavel(request: Request, codigo_id: int):
    codigos_gerados_mod.toggle_reciclavel(codigo_id)
    return RedirectResponse("/admin/items?tab=codigos", status_code=302)


@app.post("/admin/tipos/completo")
@require_login
async def admin_tipos_completo(request: Request):
    """Cria um tipo de item e, opcionalmente, já atribui estoque (quantidade
    + código de barras) num único passo — usado pela aba 'Novo Item'."""
    user = get_current_user(request)
    form = await request.form()
    nome = (form.get("nome") or "").strip()
    unidade = form.get("unidade") or "un"
    reutilizavel = bool(form.get("reutilizavel"))
    codigo_barra = (form.get("codigo_barra") or "").strip()

    if not nome:
        return RedirectResponse(
            "/admin/items?tab=novo&erro=" + quote("Nome do tipo é obrigatório."),
            status_code=302)

    try:
        quantidade = max(0, int(form.get("quantidade") or 0))
        quantidade_minima = max(0, int(form.get("quantidade_minima") or 5))
        tipo_id = items_mod.criar_tipo(nome, unidade)
        if reutilizavel:
            items_mod.alternar_reutilizavel_tipo(tipo_id)
        if codigo_barra:
            estoque_mod.criar_estoque(tipo_id, codigo_barra, quantidade,
                                       quantidade_minima, user["id"])
    except Exception as e:
        return RedirectResponse(
            "/admin/items?tab=novo&erro=" + quote(f"Erro ao cadastrar: {e}"),
            status_code=302)

    return RedirectResponse("/admin/items?ok=item_criado", status_code=302)


@app.post("/admin/estoque/{estoque_id}/codigo")
@require_login
async def admin_estoque_codigo(request: Request, estoque_id: int):
    form = await request.form()
    try:
        estoque_mod.atualizar_codigo_barra(estoque_id, form.get("codigo_barra", ""))
    except Exception as e:
        return RedirectResponse(
            "/admin/items?erro=" + quote(f"Erro ao atualizar código: {e}"),
            status_code=302)
    return RedirectResponse("/admin/items?ok=codigo_atualizado", status_code=302)


@app.post("/admin/items")
@require_login
async def admin_items_post(request: Request,
                           codigo_barra: str = Form(...),
                           item_tipo_id: int = Form(...)):
    user = get_current_user(request)
    try:
        codigo_barra = codigo_barra.strip()
        items_mod.criar_item(codigo_barra, item_tipo_id, user["id"])
        codigos_gerados_mod.sincronizar_tipo_se_reciclavel(codigo_barra, item_tipo_id)
        return RedirectResponse("/admin/items?ok=1", status_code=302)
    except Exception as e:
        return render(request, "admin_items.html",
                      {**_admin_items_context(), "erro": f"Erro ao salvar: {e}",
                       "tab_ativo": "patrimonios"})


@app.post("/admin/items/clear")
@require_admin
async def admin_items_clear(request: Request):
    items_mod.apagar_todos_itens()
    return RedirectResponse("/admin/items", status_code=302)


@app.post("/admin/items/{item_id}/delete")
@require_permission("itens_apagar")
async def admin_items_delete(request: Request, item_id: int):
    try:
        items_mod.deletar_item(item_id)
        return RedirectResponse("/admin/items", status_code=302)
    except Exception:
        return render(request, "admin_items.html", {
            **_admin_items_context(),
            "erro": "Não foi possível excluir o patrimônio.",
            "tab_ativo": "patrimonios",
        })


# ── Admin: Templates ──────────────────────────────────────────────────────────

def _admin_templates_context() -> dict:
    todos = templates_mod.listar_todos()
    return {
        "templates_kit": [t for t in todos if t.get("tipo", "kit") == "kit"],
        "templates_pedido": [t for t in todos if t.get("tipo") == "pedido"],
        "tipos_catalogo": items_mod.listar_tipos(apenas_ativos=True),
        "clientes": clientes_mod.listar(),
        "consumo_resumo": consumo_mod.resumo_todos_kits(),
    }


@app.get("/admin/templates", response_class=HTMLResponse)
@require_login
async def admin_templates(request: Request):
    return render(request, "admin_templates.html", {
        **_admin_templates_context(),
        "erro": request.query_params.get("erro"),
    })


@app.post("/admin/templates/import-bom")
@require_login
async def admin_templates_import_bom(request: Request,
                                      nome: str = Form(""),
                                      cliente: str = Form(""),
                                      tipo: str = Form("kit"),
                                      arquivo: UploadFile = File(...)):
    user = get_current_user(request)
    nome, cliente = nome.strip(), cliente.strip()
    tipo = tipo if tipo in ("kit", "pedido") else "kit"
    if not nome or not cliente:
        return render(request, "admin_templates.html", {
            **_admin_templates_context(),
            "erro": "Preencha nome e cliente antes de importar o BOM.",
            "tab_ativo": tipo,
        })
    try:
        conteudo = await _ler_upload(arquivo)
        template_id, stats = templates_mod.criar_template_do_bom(
            nome, cliente, user["id"], conteudo, tipo=tipo
        )
        q = f"ok=bom&itens={stats['itens_adicionados']}&tipos={stats['tipos_criados']}"
        return RedirectResponse(f"/admin/templates/{template_id}/edit?{q}", status_code=302)
    except ValueError as e:
        return render(request, "admin_templates.html", {
            **_admin_templates_context(),
            "erro": str(e),
            "tab_ativo": tipo,
        })


@app.post("/admin/templates/import-pedido")
@require_permission("pedidos_criar_editar")
async def admin_templates_import_pedido(request: Request,
                                         cliente: str = Form(""),
                                         numero_pedido: str = Form(""),
                                         arquivo: UploadFile = File(...)):
    """Cria um ou mais Pedidos a partir da planilha de unidades (ICCID,
    Número de Telefone, CDT, ID Hardware) — diferente do BOM do Kit: não
    cria itens do template, só guarda as unidades para consulta. Uma
    planilha pode conter vários pedidos ao mesmo tempo (agrupados por uma
    coluna 'Pedido'); cada um vira um Pedido separado. Os itens de cada
    pedido são adicionados manualmente depois, na tela de edição."""
    user = get_current_user(request)
    cliente = cliente.strip()
    if not cliente:
        return render(request, "admin_templates.html", {
            **_admin_templates_context(),
            "erro": "Selecione o cliente antes de importar a planilha do pedido.",
            "tab_ativo": "pedido",
        })
    try:
        conteudo = await _ler_upload(arquivo)
        template_id, stats = pedidos_mod.importar_planilha(
            cliente, numero_pedido, user["id"], conteudo
        )
        if stats["pedidos"] == 1:
            q = f"ok=pedido&unidades={stats['unidades']}&numero={quote(stats['numeros'][0])}"
            return RedirectResponse(f"/admin/templates/{template_id}/edit?{q}", status_code=302)
        q = (f"ok=pedidos&pedidos={stats['pedidos']}&unidades={stats['unidades']}"
             f"&ignoradas={stats['ignoradas']}&tab=pedido")
        return RedirectResponse(f"/admin/templates?{q}", status_code=302)
    except ValueError as e:
        return render(request, "admin_templates.html", {
            **_admin_templates_context(),
            "erro": str(e),
            "tab_ativo": "pedido",
        })


@app.post("/admin/templates")
@require_login
async def admin_templates_post(request: Request):
    user = get_current_user(request)
    form = await request.form()
    nome = form.get("nome", "").strip()
    cliente = form.get("cliente", "").strip()
    tipo = form.get("tipo", "kit").strip()
    tipo = tipo if tipo in ("kit", "pedido") else "kit"
    if tipo == "pedido" and not permissoes_mod.tem_permissao(user, "pedidos_criar_editar"):
        return render(request, "admin_templates.html", {
            **_admin_templates_context(),
            "erro": "Seu usuário não tem permissão pra criar Pedidos.",
            "tab_ativo": tipo,
        })
    itens = _parse_itens_form(form)
    if not nome or not cliente or not itens:
        return render(request, "admin_templates.html", {
            **_admin_templates_context(),
            "erro": "Preencha nome, cliente e ao menos 1 item.",
            "tab_ativo": tipo,
        })
    templates_mod.criar_template(nome, cliente, user["id"], itens, tipo=tipo)
    return RedirectResponse(f"/admin/templates?ok=1&tab={tipo}", status_code=302)


@app.get("/admin/templates/{template_id}/edit", response_class=HTMLResponse)
@require_login
async def admin_template_edit_page(request: Request, template_id: int):
    template = templates_mod.buscar_template(template_id)
    if not template:
        return RedirectResponse("/admin/templates", status_code=302)
    user = get_current_user(request)
    if template.get("tipo") == "pedido" and not permissoes_mod.tem_permissao(user, "pedidos_criar_editar"):
        return RedirectResponse(
            "/admin/templates?erro=" + quote("Seu usuário não tem permissão pra editar Pedidos.")
            + "&tab=pedido", status_code=302)
    itens = templates_mod.get_itens_template(template_id)
    tipos_ativos = items_mod.listar_tipos(apenas_ativos=True)
    clientes = clientes_mod.listar()
    sessoes_em_andamento = sessions_mod.listar_sessoes_em_andamento(template_id=template_id)
    unidades = pedidos_mod.listar_unidades(template_id) if template.get("tipo") == "pedido" else []
    consumo = consumo_mod.analise_template(template_id)
    return render(request, "admin_template_edit.html", {
        "template": template,
        "itens": itens,
        "consumo": consumo,
        "tipos_catalogo": tipos_ativos,
        "clientes": clientes,
        "sessoes_em_andamento": sessoes_em_andamento,
        "unidades": unidades,
    })


@app.post("/admin/templates/{template_id}/edit")
@require_login
async def admin_template_edit_post(request: Request, template_id: int):
    template_atual = templates_mod.buscar_template(template_id)
    if template_atual and template_atual.get("tipo") == "pedido":
        user = get_current_user(request)
        if not permissoes_mod.tem_permissao(user, "pedidos_criar_editar"):
            return RedirectResponse(
                "/admin/templates?erro=" + quote("Seu usuário não tem permissão pra editar Pedidos.")
                + "&tab=pedido", status_code=302)
    form = await request.form()
    nome = form.get("nome", "").strip()
    cliente = form.get("cliente", "").strip()
    itens = _parse_itens_form(form)
    if not nome or not cliente or not itens:
        template = templates_mod.buscar_template(template_id)
        itens_atuais = templates_mod.get_itens_template(template_id)
        tipos_ativos = items_mod.listar_tipos(apenas_ativos=True)
        clientes = clientes_mod.listar()
        return render(request, "admin_template_edit.html", {
            "template": template, "itens": itens_atuais,
            "tipos_catalogo": tipos_ativos,
            "clientes": clientes,
            "erro": "Preencha nome, cliente e ao menos 1 item.",
        })
    templates_mod.atualizar_template(template_id, nome, cliente, itens)
    template = templates_mod.buscar_template(template_id)
    tipo = template.get("tipo", "kit") if template else "kit"
    return RedirectResponse(f"/admin/templates?ok=editado&tab={tipo}", status_code=302)


@app.get("/admin/templates/{template_id}/unidades/exportar.xlsx")
@require_login
async def admin_template_unidades_exportar(request: Request, template_id: int):
    from fastapi.responses import Response as _Resp
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    from io import BytesIO

    template = templates_mod.buscar_template(template_id)
    if not template:
        raise HTTPException(status_code=404)
    unidades = pedidos_mod.listar_unidades(template_id)
    itens_template = templates_mod.get_itens_template(template_id)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Unidades"
    azul, branco, cinza = "1A3A5C", "FFFFFF", "F4F7FB"

    for col, h in enumerate(["ICCID", "Número de Telefone", "CDT", "ID Hardware"], 1):
        c = ws.cell(1, col, h)
        c.font = Font(bold=True, color=branco)
        c.fill = PatternFill("solid", fgColor=azul)
        c.alignment = Alignment(horizontal="center", vertical="center")

    for i, u in enumerate(unidades):
        row = i + 2
        ws.cell(row, 1, u.get("iccid") or "")
        ws.cell(row, 2, u.get("telefone") or "")
        ws.cell(row, 3, u.get("cdt") or "")
        ws.cell(row, 4, u.get("id_hardware") or "")
        if i % 2 == 0:
            for col in range(1, 5):
                ws.cell(row, col).fill = PatternFill("solid", fgColor=cinza)
    for col, w in zip("ABCD", (24, 22, 18, 22)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"

    ws2 = wb.create_sheet("Itens do Pedido")
    for col, h in enumerate(["Item", "Quantidade Exigida", "Obrigatório", "Unidade"], 1):
        c = ws2.cell(1, col, h)
        c.font = Font(bold=True, color=branco)
        c.fill = PatternFill("solid", fgColor=azul)
        c.alignment = Alignment(horizontal="center", vertical="center")
    for i, item in enumerate(itens_template):
        row = i + 2
        ws2.cell(row, 1, item["descricao"])
        ws2.cell(row, 2, item["quantidade_exigida"])
        ws2.cell(row, 3, "Sim" if item["obrigatorio"] else "Não")
        ws2.cell(row, 4, item.get("unidade") or "un")
        if i % 2 == 0:
            for col in range(1, 5):
                ws2.cell(row, col).fill = PatternFill("solid", fgColor=cinza)
    for col, w in zip("ABCD", (32, 20, 14, 12)):
        ws2.column_dimensions[col].width = w
    ws2.freeze_panes = "A2"

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    import re as _re
    safe = _re.sub(r'[^\w\-]', '_', template["nome"])
    return _Resp(
        content=buf.read(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{safe}_unidades.xlsx"'},
    )


@app.post("/admin/templates/{template_id}/delete")
@require_admin
async def admin_template_delete(request: Request, template_id: int):
    template = templates_mod.buscar_template(template_id)
    tipo = template.get("tipo", "kit") if template else "kit"
    try:
        templates_mod.deletar_template(template_id)
        return RedirectResponse(f"/admin/templates?ok=excluido&tab={tipo}", status_code=302)
    except ValueError as e:
        return render(request, "admin_templates.html", {
            **_admin_templates_context(),
            "erro": str(e),
            "tab_ativo": tipo,
        })


@app.post("/admin/templates/{template_id}/nova-versao")
@require_login
async def admin_template_nova_versao(request: Request, template_id: int):
    user = get_current_user(request)
    template = templates_mod.buscar_template(template_id)
    tipo = template.get("tipo", "kit") if template else "kit"
    templates_mod.nova_versao(template_id, user["id"])
    return RedirectResponse(f"/admin/templates?ok=versao&tab={tipo}", status_code=302)


@app.post("/admin/templates/{template_id}/toggle")
@require_login
async def admin_template_toggle(request: Request, template_id: int):
    template = templates_mod.buscar_template(template_id)
    tipo = template.get("tipo", "kit") if template else "kit"
    templates_mod.toggle_ativo(template_id)
    return RedirectResponse(f"/admin/templates?tab={tipo}", status_code=302)


@app.post("/admin/templates/{template_id}/toggle-concluido")
@require_login
async def admin_template_toggle_concluido(request: Request, template_id: int):
    template = templates_mod.buscar_template(template_id)
    tipo = template.get("tipo", "kit") if template else "kit"
    templates_mod.toggle_concluido(template_id)
    return RedirectResponse(f"/admin/templates?tab={tipo}", status_code=302)


# ── Sessions ──────────────────────────────────────────────────────────────────

@app.get("/session/{sessao_id}", response_class=HTMLResponse)
@require_login
async def session_page(request: Request, sessao_id: int):
    session = sessions_mod.get_session(sessao_id)
    if not session:
        return RedirectResponse("/", status_code=302)
    if session["status"] != "em_andamento":
        return RedirectResponse("/", status_code=302)
    if not session.get("garagem"):
        # Destino ainda não escolhido — obrigatório antes de bipar (pode
        # acontecer com link direto/voltar do navegador, ou sessão antiga
        # de antes dessa mudança existir).
        return RedirectResponse(f"/session/{sessao_id}/destino", status_code=302)
    itens = templates_mod.get_itens_template(session["kit_template_id"])
    contagem = sessions_mod.get_contagem(sessao_id)
    return render(request, "session.html", {
        "session": session,
        "itens": itens,
        "contagem": contagem,
    })


@app.get("/session/{sessao_id}/destino", response_class=HTMLResponse)
@require_login
async def session_destino_page(request: Request, sessao_id: int, erro: str = ""):
    session = sessions_mod.get_session(sessao_id)
    if not session:
        return RedirectResponse("/", status_code=302)
    if session["status"] != "em_andamento":
        return RedirectResponse("/", status_code=302)
    if session.get("garagem"):
        # Destino já escolhido — não pergunta de novo, vai direto pra bipagem.
        return RedirectResponse(f"/session/{sessao_id}", status_code=302)
    veiculos_lista = veiculos_mod.listar(cliente=session.get("cliente", ""))
    garagens_lista = garagens_mod.listar()
    return render(request, "session_destino.html", {
        "session": session,
        "veiculos_lista": veiculos_lista,
        "garagens_lista": garagens_lista,
        "erro": erro,
    })


@app.post("/session/{sessao_id}/destino")
@require_login
async def session_destino_post(request: Request, sessao_id: int):
    session = sessions_mod.get_session(sessao_id)
    if not session or session["status"] != "em_andamento":
        return RedirectResponse("/", status_code=302)

    form = await request.form()
    veiculo_id_str = str(form.get("veiculo_id", "")).strip()
    veiculo_id = int(veiculo_id_str) if veiculo_id_str.isdigit() else None
    garagem = str(form.get("garagem", "")).strip()
    modelo = str(form.get("modelo", "")).strip()

    veiculo_texto = ""
    if veiculo_id:
        v = veiculos_mod.buscar(veiculo_id)
        if v:
            veiculo_texto = v["numero"]

    if not veiculo_id or not garagem:
        return RedirectResponse(
            f"/session/{sessao_id}/destino?erro=" +
            quote("Selecione o veículo e a garagem antes de continuar."),
            status_code=302)

    sessions_mod.definir_destino(sessao_id, veiculo_id, veiculo_texto, garagem, modelo)
    veiculos_mod.atualizar_garagem(veiculo_id, garagem.upper())
    return RedirectResponse(f"/session/{sessao_id}", status_code=302)


@app.post("/session/{sessao_id}/cancel")
@require_login
async def session_cancel(request: Request, sessao_id: int):
    sessions_mod.cancel_session(sessao_id)
    return RedirectResponse("/", status_code=302)


@app.post("/admin/sessoes/{sessao_id}/cancelar")
@require_login
async def admin_sessao_cancelar(request: Request, sessao_id: int):
    """Admin cancela uma sessão em andamento para liberar template para edição/exclusão."""
    session = sessions_mod.get_session(sessao_id)
    template_id = session["kit_template_id"] if session else None
    sessions_mod.cancel_session(sessao_id)
    if template_id:
        return RedirectResponse(f"/admin/templates/{template_id}/edit?cancelou=1", status_code=302)
    return RedirectResponse("/admin/templates", status_code=302)


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
                if not isinstance(msg, dict):
                    raise ValueError("not a JSON object")
                if msg.get("acao") == "identificar":
                    result = sessions_mod.register_scan(
                        sessao_id, msg["codigo"],
                        item_tipo_id=int(msg["item_tipo_id"])
                    )
                elif msg.get("acao") == "confirmar_quantidade":
                    result = sessions_mod.confirmar_quantidade(
                        sessao_id, msg["codigo_barra"], float(msg.get("quantidade", 1))
                    )
                elif msg.get("acao") == "confirmar_substituicao":
                    result = sessions_mod.confirmar_substituicao(
                        sessao_id, msg["codigo_barra"], msg.get("motivo", "")
                    )
                elif msg.get("acao") == "confirmar_componente":
                    result = sessions_mod.confirmar_componente(
                        sessao_id, msg["codigo_barra"], msg.get("quantidades", {})
                    )
                elif msg.get("acao") == "cancelar_serial":
                    result = sessions_mod.cancelar_serial(sessao_id)
                elif msg.get("acao") == "cancelar_patrimonio_fixo":
                    result = sessions_mod.cancelar_patrimonio_fixo(sessao_id)
                elif msg.get("acao") == "desfazer_ultimo":
                    result = sessions_mod.desfazer_ultimo_item(sessao_id)
                else:
                    result = {"resultado": "rejeitado", "mensagem": "Mensagem inválida."}
            except (json.JSONDecodeError, KeyError, ValueError):
                # Plain barcode scan — priority: serial > patrimônio fixo > componente > normal
                pendente_serial = sessions_mod.get_pendente_serial(sessao_id)
                if pendente_serial:
                    result = sessions_mod.registrar_serial(sessao_id, data)
                else:
                    pendente_fixo = sessions_mod.get_pendente_patrimonio_fixo(sessao_id)
                    if pendente_fixo:
                        result = sessions_mod.registrar_patrimonio_de_fixo(sessao_id, data)
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
async def session_finalize(request: Request, sessao_id: int):
    user = get_current_user(request)

    session_check = sessions_mod.get_session(sessao_id)
    if not session_check or session_check["status"] != "em_andamento":
        return RedirectResponse("/", status_code=302)
    if not session_check.get("garagem"):
        # Destino não foi definido (não deveria acontecer — a tela de bipagem
        # só é alcançada depois do /destino — mas não finaliza sem isso).
        return RedirectResponse(f"/session/{sessao_id}/destino", status_code=302)

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

    veiculo = session.get("veiculo") or ""
    garagem = session.get("garagem") or ""
    modelo = session.get("modelo") or ""
    veiculo_id = session.get("veiculo_id")

    zpl = zpl_mod.generate_zpl(
        kit_id=kit_id,
        kit_nome=session["kit_nome"],
        cliente=session["cliente"],
        operador=session["operador_nome"],
        timestamp=ts,
        itens=itens_label,
        veiculo=veiculo,
        garagem=garagem,
        modelo=modelo,
    )

    html_label = zpl_mod.generate_html_label(
        kit_id=kit_id,
        kit_nome=session["kit_nome"],
        cliente=session["cliente"],
        operador=session["operador_nome"],
        timestamp=ts,
        itens=itens_label,
        veiculo=veiculo,
        garagem=garagem,
        modelo=modelo,
    )

    with db() as conn:
        ts_str = ts.strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "INSERT INTO kit_record (kit_id, sessao_id, kit_template_id, "
            "kit_template_versao, operador_id, veiculo, garagem, modelo, finalizado_em, veiculo_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (kit_id, sessao_id, session["kit_template_id"],
             session["kit_template_versao"], user["id"],
             veiculo, garagem, modelo, ts_str, veiculo_id)
        )
        conn.execute(
            "UPDATE scan_session SET status = 'finalizado', "
            "finalizado_em = ? WHERE id = ?",
            (ts_str, sessao_id)
        )
        conn.execute(
            "INSERT INTO print_queue (kit_id, zpl, html_label, solicitado_por, solicitado_em) "
            "VALUES (?, ?, ?, ?, ?)",
            (kit_id, zpl, html_label, user["id"], now_brt())
        )

    if session.get("kit_tipo") == "pedido":
        templates_mod.marcar_concluido(session["kit_template_id"])

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


# ── Mobile Hub (público) ──────────────────────────────────────────────────────

@app.get("/mobile", response_class=HTMLResponse)
async def mobile_hub(request: Request):
    user = get_current_user(request)
    sessoes_ativas = []
    templates_list = []
    if user:
        with db() as conn:
            sessoes_ativas = conn.execute(
                "SELECT ss.id, kt.nome AS kit_nome, kt.cliente, ss.iniciado_em "
                "FROM scan_session ss "
                "JOIN kit_template kt ON kt.id = ss.kit_template_id "
                "WHERE ss.operador_id = ? AND ss.status = 'em_andamento' "
                "ORDER BY ss.iniciado_em DESC",
                (user["id"],)
            ).fetchall()
            templates_list = conn.execute(
                "SELECT id, nome, cliente FROM kit_template WHERE ativo = 1 ORDER BY nome"
            ).fetchall()

    return render(request, "mobile_hub.html", {
        "user": user,
        "sessoes_ativas": [dict(s) for s in sessoes_ativas],
        "templates_list": [dict(t) for t in templates_list],
    })


# ── Kit Detail (público — escaneado pelo QR code) ─────────────────────────────

def _resolver_kit_id(texto: str) -> str | None:
    """Resolve um texto lido (URL do QR da etiqueta, kit_id completo, ou o
    ID curto de 8 caracteres do código de barras) para o kit_id completo
    correspondente, ou None se não encontrar."""
    texto = (texto or "").strip()
    if not texto:
        return None
    m = re.search(r'/kit/([0-9a-fA-F-]{36})/?$', texto)
    if m:
        return m.group(1)
    with db() as conn:
        if len(texto) == 36:
            row = conn.execute(
                "SELECT kit_id FROM kit_record WHERE kit_id = ?", (texto,)
            ).fetchone()
            if row:
                return row["kit_id"]
        rows = conn.execute(
            "SELECT kit_id FROM kit_record WHERE kit_id LIKE ?",
            (texto.lower() + '%',)
        ).fetchall()
        if len(rows) == 1:
            return rows[0]["kit_id"]
    return None


@app.get("/kit/buscar")
async def kit_buscar(request: Request, codigo: str = ""):
    """Resolve um código de barras ou o texto de um QR de kit para a
    página de verificação correspondente — usado pelo scanner do /mobile."""
    kit_id = _resolver_kit_id(codigo)
    if not kit_id:
        return RedirectResponse("/mobile?erro=kit_nao_encontrado", status_code=302)
    return RedirectResponse(f"/kit/{kit_id}", status_code=302)


@app.get("/kit/{kit_id}", response_class=HTMLResponse)
async def kit_detail(request: Request, kit_id: str):
    with db() as conn:
        kit = conn.execute(
            "SELECT kr.*, kt.nome AS kit_nome, kt.cliente, kt.versao, kt.tipo AS kit_tipo, "
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

    validacoes = validacoes_mod.listar_por_kit(kit_id)
    ok = request.query_params.get("ok", "")
    unidades = pedidos_mod.listar_unidades(kit["kit_template_id"]) if kit.get("kit_tipo") == "pedido" else []

    return render(request, "kit_detail.html", {
        "kit": kit,
        "itens": [dict(i) for i in itens],
        "validacoes": validacoes,
        "ok": ok,
        "unidades": unidades,
    })


@app.post("/kit/{kit_id}/validar")
@require_login
async def kit_validar(request: Request, kit_id: str):
    user = get_current_user(request)
    form = await request.form()
    observacao = str(form.get("observacao", "")).strip()
    with db() as conn:
        exists = conn.execute(
            "SELECT kit_id FROM kit_record WHERE kit_id = ?", (kit_id,)
        ).fetchone()
    if not exists:
        return HTMLResponse("<h2>Kit não encontrado.</h2>", status_code=404)
    validacoes_mod.registrar(kit_id, user["id"], observacao)
    return RedirectResponse(f"/kit/{kit_id}?ok=validado", status_code=302)


# ── Relatórios ────────────────────────────────────────────────────────────────

@app.get("/reports", response_class=HTMLResponse)
@require_permission("ver_relatorios")
async def reports(request: Request,
                  data_ini: str = "",
                  data_fim: str = "",
                  operador_id: str = "",
                  tipo: str = ""):
    query = """
        SELECT kr.kit_id, kr.finalizado_em, kr.status,
               kr.veiculo, kr.garagem,
               kr.veiculo_id,
               COALESCE(v.numero, kr.veiculo) AS veiculo_exibido,
               v.id AS v_id,
               kt.nome AS kit_nome, kt.cliente, kt.versao, kt.tipo AS kit_tipo,
               u.nome AS operador_nome,
               pq.id AS pq_id,
               (SELECT COUNT(*) FROM kit_validacoes kv WHERE kv.kit_id = kr.kit_id) AS num_validacoes
        FROM kit_record kr
        JOIN kit_template kt ON kt.id = kr.kit_template_id
        JOIN users u ON u.id = kr.operador_id
        LEFT JOIN print_queue pq ON pq.kit_id = kr.kit_id
        LEFT JOIN veiculos v ON v.id = kr.veiculo_id
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
    if tipo in ("kit", "pedido"):
        query += " AND kt.tipo = ?"
        params.append(tipo)
    query += " ORDER BY kr.finalizado_em DESC LIMIT 200"

    with db() as conn:
        rows = conn.execute(query, params).fetchall()
        usuarios = conn.execute("SELECT id, nome FROM users ORDER BY nome").fetchall()

    veiculos_todos = veiculos_mod.listar()
    return render(request, "reports.html", {
        "kits": [dict(r) for r in rows],
        "usuarios": [dict(u) for u in usuarios],
        "data_ini": data_ini,
        "data_fim": data_fim,
        "operador_id": operador_id,
        "tipo": tipo,
        "ok": request.query_params.get("ok", ""),
        "veiculos_todos": veiculos_todos,
    })


@app.post("/kit-record/{kit_id}/veiculo")
@require_login
async def kit_record_vincular_veiculo(request: Request, kit_id: str):
    form = await request.form()
    veiculo_id_str = str(form.get("veiculo_id", "")).strip()
    veiculo_id = int(veiculo_id_str) if veiculo_id_str.isdigit() else None
    veiculo_texto = ""
    garagem_texto = ""
    if veiculo_id:
        v = veiculos_mod.buscar(veiculo_id)
        if v:
            veiculo_texto = v["numero"]
            garagem_texto = v["garagem"]
    with db() as conn:
        conn.execute(
            "UPDATE kit_record SET veiculo_id=?, veiculo=?, garagem=? WHERE kit_id=?",
            (veiculo_id, veiculo_texto, garagem_texto, kit_id)
        )
    return RedirectResponse("/reports?ok=veiculo", status_code=302)


@app.post("/reports/reprint/{kit_id}")
@require_permission("ver_relatorios")
async def reprint_kit(request: Request, kit_id: str):
    """Recria a entrada na fila de impressão para um kit já finalizado.
    Se a garagem enviada for diferente da gravada, atualiza o kit_record e
    regenera a etiqueta (ZPL + HTML) com o novo valor; caso contrário,
    reimprime a última etiqueta já gerada, sem recalcular nada."""
    user = get_current_user(request)
    form = await request.form()
    nova_garagem = str(form.get("garagem", "")).strip().upper()

    with db() as conn:
        kit_row = conn.execute(
            "SELECT kr.*, kt.nome AS kit_nome, kt.cliente, u.nome AS operador_nome "
            "FROM kit_record kr "
            "JOIN kit_template kt ON kt.id = kr.kit_template_id "
            "JOIN users u ON u.id = kr.operador_id "
            "WHERE kr.kit_id = ?",
            (kit_id,)
        ).fetchone()
    if not kit_row:
        return RedirectResponse("/reports?erro=Kit+nao+encontrado", status_code=302)
    kit = dict(kit_row)

    if nova_garagem != (kit.get("garagem") or ""):
        with db() as conn:
            conn.execute("UPDATE kit_record SET garagem = ? WHERE kit_id = ?", (nova_garagem, kit_id))
            itens_rows = conn.execute(
                "SELECT it.nome AS descricao, COUNT(*) AS quantidade "
                "FROM scan_session_items si "
                "JOIN item_tipo it ON it.id = si.item_tipo_id "
                "WHERE si.sessao_id = ? "
                "GROUP BY si.item_tipo_id ORDER BY it.nome",
                (kit["sessao_id"],)
            ).fetchall()
        itens_label = [dict(r) for r in itens_rows]
        ts = datetime.strptime(kit["finalizado_em"], "%Y-%m-%d %H:%M:%S")

        zpl = zpl_mod.generate_zpl(
            kit_id=kit_id, kit_nome=kit["kit_nome"], cliente=kit["cliente"],
            operador=kit["operador_nome"], timestamp=ts, itens=itens_label,
            veiculo=kit.get("veiculo") or "", garagem=nova_garagem,
        )
        html_label = zpl_mod.generate_html_label(
            kit_id=kit_id, kit_nome=kit["kit_nome"], cliente=kit["cliente"],
            operador=kit["operador_nome"], timestamp=ts, itens=itens_label,
            veiculo=kit.get("veiculo") or "", garagem=nova_garagem,
        )
    else:
        with db() as conn:
            pq_row = conn.execute(
                "SELECT * FROM print_queue WHERE kit_id = ? ORDER BY id DESC LIMIT 1",
                (kit_id,)
            ).fetchone()
        if not pq_row:
            return RedirectResponse("/reports?erro=Etiqueta+nao+encontrada", status_code=302)
        pq = dict(pq_row)
        zpl = pq["zpl"]
        html_label = pq.get("html_label")

    with db() as conn:
        conn.execute(
            "INSERT INTO print_queue (kit_id, zpl, html_label, solicitado_por, solicitado_em) VALUES (?,?,?,?,?)",
            (kit_id, zpl, html_label, user["id"], now_brt())
        )
    return RedirectResponse("/print-queue?ok=reimpresso", status_code=302)


@app.get("/reports/{kit_id}/excel")
@require_permission("ver_relatorios")
async def report_excel(request: Request, kit_id: str):
    from fastapi.responses import Response as _Resp
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    from io import BytesIO

    with db() as conn:
        kit = conn.execute(
            "SELECT kr.*, kt.nome AS kit_nome, kt.cliente, kt.versao, kt.tipo AS kit_tipo, "
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

    # ── Aba Unidades do Pedido (ICCID/Telefone/CDT/ID Hardware) ────────────────
    if kit.get("kit_tipo") == "pedido":
        unidades = pedidos_mod.listar_unidades(kit["kit_template_id"])
        ws3 = wb.create_sheet("Unidades do Pedido")
        for col, h in enumerate(["ICCID", "Número de Telefone", "CDT", "ID Hardware"], 1):
            hdr_cell(ws3, 1, col, h)
        for i, u in enumerate(unidades):
            row = i + 2
            ws3.cell(row, 1, u.get("iccid") or "")
            ws3.cell(row, 2, u.get("telefone") or "")
            ws3.cell(row, 3, u.get("cdt") or "")
            ws3.cell(row, 4, u.get("id_hardware") or "")
            if i % 2 == 0:
                for col in range(1, 5):
                    ws3.cell(row, col).fill = PatternFill("solid", fgColor=cinza)
        for col, w in zip("ABCD", (24, 22, 18, 22)):
            ws3.column_dimensions[col].width = w
        ws3.freeze_panes = "A2"

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


@app.get("/reports/exportar-todos.xlsx")
@require_permission("ver_relatorios")
async def reports_exportar_todos(request: Request,
                                  data_ini: str = "",
                                  data_fim: str = "",
                                  operador_id: str = "",
                                  tipo: str = ""):
    """Exporta todos os kits/pedidos finalizados que batem com os filtros
    atuais da tela de Relatórios (mesmos filtros — não é o kit único, é
    o lote inteiro), com uma aba de resumo e uma de itens detalhados."""
    from fastapi.responses import Response as _Resp
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    from io import BytesIO

    query = """
        SELECT kr.kit_id, kr.kit_template_id, kr.finalizado_em, kr.veiculo, kr.garagem,
               COALESCE(v.numero, kr.veiculo) AS veiculo_exibido,
               kt.nome AS kit_nome, kt.cliente, kt.versao, kt.tipo AS kit_tipo,
               u.nome AS operador_nome,
               (SELECT COUNT(*) FROM kit_validacoes kv WHERE kv.kit_id = kr.kit_id) AS num_validacoes
        FROM kit_record kr
        JOIN kit_template kt ON kt.id = kr.kit_template_id
        JOIN users u ON u.id = kr.operador_id
        LEFT JOIN veiculos v ON v.id = kr.veiculo_id
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
    if tipo in ("kit", "pedido"):
        query += " AND kt.tipo = ?"
        params.append(tipo)
    query += " ORDER BY kr.finalizado_em DESC LIMIT 200"

    with db() as conn:
        kits = [dict(r) for r in conn.execute(query, params).fetchall()]
        kit_ids = [k["kit_id"] for k in kits]
        itens_por_kit = {}
        if kit_ids:
            placeholders = ",".join("?" * len(kit_ids))
            rows_itens = conn.execute(
                "SELECT kr.kit_id, it.nome AS tipo_nome, si.codigo_barra, "
                "si.serial_number, si.bipado_em "
                "FROM scan_session_items si "
                "JOIN item_tipo it ON it.id = si.item_tipo_id "
                "JOIN kit_record kr ON kr.sessao_id = si.sessao_id "
                f"WHERE kr.kit_id IN ({placeholders}) "
                "ORDER BY kr.kit_id, it.nome, si.bipado_em",
                kit_ids
            ).fetchall()
            for r in rows_itens:
                itens_por_kit.setdefault(r["kit_id"], []).append(dict(r))

    wb = openpyxl.Workbook()
    azul, branco, cinza = "1A3A5C", "FFFFFF", "F4F7FB"

    def hdr_cell(ws, row, col, value):
        c = ws.cell(row=row, column=col, value=value)
        c.font = Font(bold=True, color=branco)
        c.fill = PatternFill("solid", fgColor=azul)
        c.alignment = Alignment(horizontal="center", vertical="center")
        return c

    # ── Aba Resumo ────────────────────────────────────────────────────────────
    ws1 = wb.active
    ws1.title = "Resumo"
    for col, h in enumerate(
        ["Tipo", "Kit", "Cliente", "Veículo", "Garagem", "Operador",
         "Finalizado em", "Verificações"], 1):
        hdr_cell(ws1, 1, col, h)
    for i, k in enumerate(kits):
        row = i + 2
        ws1.cell(row, 1, "Pedido" if k.get("kit_tipo") == "pedido" else "Kit")
        ws1.cell(row, 2, f"{k['kit_nome']} v{k['versao']}")
        ws1.cell(row, 3, k["cliente"])
        ws1.cell(row, 4, k.get("veiculo_exibido") or "")
        ws1.cell(row, 5, k.get("garagem") or "")
        ws1.cell(row, 6, k["operador_nome"])
        ws1.cell(row, 7, k.get("finalizado_em") or "")
        ws1.cell(row, 8, k.get("num_validacoes") or 0)
        if i % 2 == 0:
            for col in range(1, 9):
                ws1.cell(row, col).fill = PatternFill("solid", fgColor=cinza)
    for col, w in zip("ABCDEFGH", (10, 30, 22, 16, 16, 22, 20, 14)):
        ws1.column_dimensions[col].width = w
    ws1.freeze_panes = "A2"

    # ── Aba Detalhes ──────────────────────────────────────────────────────────
    ws2 = wb.create_sheet("Detalhes")
    for col, h in enumerate(
        ["Kit", "Veículo", "Tipo de Item", "Código de Barras", "Serial Number",
         "Origem", "Bipado em"], 1):
        hdr_cell(ws2, 1, col, h)
    row = 2
    for k in kits:
        veiculo_exibido = k.get("veiculo_exibido") or ""
        kit_label = f"{k['kit_nome']} v{k['versao']} ({k['kit_id'][:8].upper()})"
        for item in itens_por_kit.get(k["kit_id"], []):
            codigo = item["codigo_barra"]
            if codigo.startswith("COMP:"):
                parts = codigo.split(":", 3)
                origem = "Saquinho"
                codigo_display = parts[1] if len(parts) >= 2 else codigo
            else:
                origem = "Bipagem direta"
                codigo_display = codigo
            ws2.cell(row, 1, kit_label)
            ws2.cell(row, 2, veiculo_exibido)
            ws2.cell(row, 3, item["tipo_nome"])
            ws2.cell(row, 4, codigo_display)
            ws2.cell(row, 5, item.get("serial_number") or "")
            ws2.cell(row, 6, origem)
            ws2.cell(row, 7, item.get("bipado_em") or "")
            if row % 2 == 0:
                for col in range(1, 8):
                    ws2.cell(row, col).fill = PatternFill("solid", fgColor=cinza)
            row += 1
    for col, w in zip("ABCDEFG", (34, 16, 28, 24, 20, 16, 20)):
        ws2.column_dimensions[col].width = w
    ws2.freeze_panes = "A2"

    # ── Aba Unidades (ICCID/Telefone/CDT/ID Hardware dos Pedidos) ──────────────
    pedidos_no_lote = [k for k in kits if k.get("kit_tipo") == "pedido"]
    if pedidos_no_lote:
        ws3 = wb.create_sheet("Unidades")
        for col, h in enumerate(
            ["Pedido", "Veículo", "ICCID", "Número de Telefone", "CDT", "ID Hardware"], 1):
            hdr_cell(ws3, 1, col, h)
        row = 2
        for k in pedidos_no_lote:
            pedido_label = f"{k['kit_nome']} v{k['versao']} ({k['kit_id'][:8].upper()})"
            veiculo_exibido = k.get("veiculo_exibido") or ""
            for u in pedidos_mod.listar_unidades(k["kit_template_id"]):
                ws3.cell(row, 1, pedido_label)
                ws3.cell(row, 2, veiculo_exibido)
                ws3.cell(row, 3, u.get("iccid") or "")
                ws3.cell(row, 4, u.get("telefone") or "")
                ws3.cell(row, 5, u.get("cdt") or "")
                ws3.cell(row, 6, u.get("id_hardware") or "")
                if row % 2 == 0:
                    for col in range(1, 7):
                        ws3.cell(row, col).fill = PatternFill("solid", fgColor=cinza)
                row += 1
        for col, w in zip("ABCDEF", (34, 16, 24, 22, 18, 22)):
            ws3.column_dimensions[col].width = w
        ws3.freeze_panes = "A2"

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return _Resp(
        content=buf.read(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="relatorio_kits.xlsx"'},
    )


@app.post("/reports/{kit_id}/delete")
@require_admin
async def report_delete(request: Request, kit_id: str):
    sessions_mod.deletar_kit_record(kit_id)
    return RedirectResponse("/reports?ok=excluido", status_code=302)


@app.get("/reports/validacoes", response_class=HTMLResponse)
@require_permission("ver_relatorios")
async def reports_validacoes(request: Request,
                             data_ini: str = "",
                             data_fim: str = "",
                             validador_id: str = ""):
    rows = validacoes_mod.listar_relatorio(data_ini, data_fim, validador_id)
    with db() as conn:
        usuarios = conn.execute("SELECT id, nome FROM users ORDER BY nome").fetchall()
    return render(request, "reports_validacoes.html", {
        "rows": rows,
        "usuarios": [dict(u) for u in usuarios],
        "data_ini": data_ini,
        "data_fim": data_fim,
        "validador_id": validador_id,
    })


@app.get("/reports/validacoes/export")
@require_permission("ver_relatorios")
async def reports_validacoes_export(request: Request,
                                    data_ini: str = "",
                                    data_fim: str = "",
                                    validador_id: str = ""):
    """Uma linha por kit (não por verificação) — se o mesmo kit foi
    verificado mais de uma vez, cada verificação vira um bloco extra de
    colunas (Verificação 1, Verificação 2...) na mesma linha, em vez de
    duplicar veículo/cliente/itens numa linha nova por verificação."""
    from fastapi.responses import Response as _Resp
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    from io import BytesIO

    grupos = validacoes_mod.listar_relatorio_agrupado(data_ini, data_fim, validador_id)
    max_verificacoes = max((len(g["verificacoes"]) for g in grupos), default=0)

    azul = "1A3A5C"
    branco = "FFFFFF"
    cinza = "F4F7FB"

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Verificado"

    headers = [
        "Kit ID", "Template", "Cliente", "Veículo", "Garagem",
        "Operador Conferência", "Data Conferência", "Itens"
    ]
    widths = [14, 28, 22, 14, 14, 22, 20, 50]
    for n in range(1, max_verificacoes + 1):
        headers += [f"Verificação {n} - Por", f"Verificação {n} - Data", f"Verificação {n} - Observação"]
        widths += [22, 20, 30]

    for col, (h, w) in enumerate(zip(headers, widths), 1):
        c = ws.cell(row=1, column=col, value=h)
        c.font = Font(bold=True, color=branco)
        c.fill = PatternFill("solid", fgColor=azul)
        c.alignment = Alignment(horizontal="center", vertical="center")
        ws.column_dimensions[ws.cell(1, col).column_letter].width = w

    for i, g in enumerate(grupos, 2):
        ws.cell(i, 1, g["kit_id"][:8].upper())
        ws.cell(i, 2, g["kit_nome"])
        ws.cell(i, 3, g["cliente"])
        ws.cell(i, 4, g.get("veiculo") or "")
        ws.cell(i, 5, g.get("garagem") or "")
        ws.cell(i, 6, g["operador_nome"])
        ws.cell(i, 7, g.get("finalizado_em") or "")
        itens_texto = (g.get("itens_resumo") or "").replace(" | ", "\n")
        c_itens = ws.cell(i, 8, itens_texto)
        c_itens.alignment = Alignment(wrap_text=True, vertical="top")
        col = 9
        for v in g["verificacoes"]:
            ws.cell(i, col, v["validado_por_nome"])
            ws.cell(i, col + 1, v["validado_em"])
            ws.cell(i, col + 2, v.get("observacao") or "")
            col += 3
        if i % 2 == 0:
            for c in range(1, len(headers) + 1):
                ws.cell(i, c).fill = PatternFill("solid", fgColor=cinza)

    ws.freeze_panes = "A2"
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return _Resp(
        content=buf.read(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=verificacoes.xlsx"},
    )


# ── Prateleira ────────────────────────────────────────────────────────────────

def _prateleira_context() -> dict:
    layout = prateleira_mod.get_layout()
    blocos = prateleira_mod.listar_blocos()
    return {
        "layout": layout,
        "colunas_nomes": prateleira_mod.listar_colunas(),
        "blocos": blocos,
        "celulas_vazias": prateleira_mod.celulas_vazias(blocos, layout),
        "livre": prateleira_mod.listar_livre(),
        "estoque_itens": estoque_mod.listar_estoque(),
        "max_itens_por_slot": prateleira_mod.MAX_ITENS_POR_SLOT,
    }


@app.get("/admin/prateleira", response_class=HTMLResponse)
@require_login
async def admin_prateleira(request: Request):
    return render(request, "admin_prateleira.html", _prateleira_context())


@app.post("/admin/prateleira/layout")
@require_login
async def admin_prateleira_layout(request: Request):
    form = await request.form()
    try:
        linhas = int(form.get("linhas"))
        colunas = int(form.get("colunas"))
        nomes = [str(form.get(f"nome_coluna_{i}", "")).strip() for i in range(1, colunas + 1)]
        prateleira_mod.atualizar_layout(linhas, colunas, nomes)
    except (ValueError, TypeError) as e:
        return RedirectResponse("/admin/prateleira?erro=" + quote(str(e)), status_code=302)
    return RedirectResponse("/admin/prateleira?ok=layout", status_code=302)


@app.post("/admin/prateleira/blocos")
@require_login
async def admin_prateleira_criar_bloco(request: Request):
    form = await request.form()
    try:
        linha_ini = int(form.get("linha_ini"))
        linha_fim = int(form.get("linha_fim"))
        coluna_ini = int(form.get("coluna_ini"))
        coluna_fim = int(form.get("coluna_fim"))
        estoque_id = int(form.get("estoque_id"))
        prateleira_mod.criar_bloco(linha_ini, linha_fim, coluna_ini, coluna_fim, estoque_id)
    except (ValueError, TypeError) as e:
        return RedirectResponse("/admin/prateleira?erro=" + quote(str(e)), status_code=302)
    return RedirectResponse("/admin/prateleira?ok=bloco", status_code=302)


@app.post("/admin/prateleira/blocos/{bloco_id}/remover")
@require_admin
async def admin_prateleira_remover_bloco(request: Request, bloco_id: int):
    prateleira_mod.remover_bloco(bloco_id)
    return RedirectResponse("/admin/prateleira", status_code=302)


@app.post("/admin/prateleira/livre")
@require_login
async def admin_prateleira_adicionar_livre(request: Request):
    form = await request.form()
    try:
        prateleira_mod.adicionar_livre(int(form.get("estoque_id")))
    except (ValueError, TypeError) as e:
        return RedirectResponse("/admin/prateleira?erro=" + quote(str(e)), status_code=302)
    return RedirectResponse("/admin/prateleira?ok=livre", status_code=302)


@app.post("/admin/prateleira/livre/{livre_id}/remover")
@require_admin
async def admin_prateleira_remover_livre(request: Request, livre_id: int):
    prateleira_mod.remover_livre(livre_id)
    return RedirectResponse("/admin/prateleira", status_code=302)


@app.get("/prateleira/tv", response_class=HTMLResponse)
async def prateleira_tv(request: Request, minutos: int = 5):
    minutos = max(1, minutos)
    layout = prateleira_mod.get_layout()
    blocos = prateleira_mod.listar_blocos()
    livre = prateleira_mod.listar_livre()
    return render(request, "prateleira_tv.html", {
        "layout": layout,
        "colunas_nomes": prateleira_mod.listar_colunas(),
        "blocos": blocos,
        "celulas_vazias": prateleira_mod.celulas_vazias(blocos, layout),
        "livre": livre,
        "minutos": minutos,
        "contagem_status": prateleira_mod.contar_status(blocos, livre),
    })


# ── Produção (Consat → Trânsito → Cliente) ────────────────────────────────────

@app.get("/producao/tv", response_class=HTMLResponse)
async def producao_tv(request: Request, minutos: int = 5):
    minutos = max(1, minutos)
    return render(request, "producao_tv.html", {
        "em_producao": producao_mod.listar_em_producao(),
        "produzido": producao_mod.listar_produzido(),
        "transito": producao_mod.listar_transito(),
        "cliente_instalando": producao_mod.listar_cliente_instalando(),
        "cliente_concluido": producao_mod.listar_cliente_concluido(limite=12),
        "resumo": producao_mod.resumo(),
        "minutos": minutos,
    })


@app.get("/admin/producao", response_class=HTMLResponse)
@require_login
async def admin_producao(request: Request):
    return render(request, "admin_producao.html", {
        "em_producao": producao_mod.listar_em_producao(),
        "produzido": producao_mod.listar_produzido(),
        "transito": producao_mod.listar_transito(),
        "cliente_instalando": producao_mod.listar_cliente_instalando(),
        "cliente_concluido": producao_mod.listar_cliente_concluido(limite=30),
    })


@app.post("/admin/producao/transito")
@require_login
async def admin_producao_transito(request: Request):
    form = await request.form()
    kit_ids = form.getlist("kit_ids")
    n = producao_mod.marcar_transito(kit_ids)
    return RedirectResponse(f"/admin/producao?ok=transito&n={n}", status_code=302)


@app.post("/admin/producao/{kit_id}/cliente-instalando")
@require_login
async def admin_producao_cliente_instalando(request: Request, kit_id: str):
    producao_mod.marcar_cliente_instalando(kit_id)
    return RedirectResponse("/admin/producao?ok=instalando", status_code=302)


@app.post("/admin/producao/{kit_id}/cliente-concluido")
@require_login
async def admin_producao_cliente_concluido(request: Request, kit_id: str):
    producao_mod.marcar_cliente_concluido(kit_id)
    return RedirectResponse("/admin/producao?ok=concluido", status_code=302)


@app.post("/admin/producao/{kit_id}/voltar")
@require_login
async def admin_producao_voltar(request: Request, kit_id: str):
    producao_mod.voltar_estagio(kit_id)
    return RedirectResponse("/admin/producao?ok=voltou", status_code=302)


@app.post("/admin/producao/{kit_id}/nota-fiscal")
@require_permission("producao_nota_fiscal")
async def admin_producao_nota_fiscal(request: Request, kit_id: str):
    form = await request.form()
    ok = producao_mod.atualizar_nota_fiscal(
        kit_id,
        str(form.get("nota_fiscal", "")),
        str(form.get("nota_fiscal_data", "")),
        str(form.get("motivo", "")),
    )
    if not ok:
        return RedirectResponse("/admin/producao?erro=nf_motivo", status_code=302)
    return RedirectResponse("/admin/producao?ok=nota_fiscal", status_code=302)


@app.get("/admin/producao/historico", response_class=HTMLResponse)
@require_login
async def admin_producao_historico(request: Request,
                                    data_ini: str = "", data_fim: str = ""):
    return render(request, "admin_producao_historico.html", {
        "registros": producao_mod.listar_historico(data_ini, data_fim),
        "data_ini": data_ini, "data_fim": data_fim,
    })


@app.get("/admin/producao/historico/exportar.xlsx")
@require_login
async def admin_producao_historico_exportar(request: Request,
                                             data_ini: str = "", data_fim: str = ""):
    from fastapi.responses import Response as _Resp
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    from io import BytesIO

    registros = producao_mod.listar_historico(data_ini, data_fim, limite=5000)

    wb = openpyxl.Workbook()
    azul, branco = "1A3A5C", "FFFFFF"
    ws = wb.active
    ws.title = "Historico Producao"

    def hdr_cell(row, col, value):
        c = ws.cell(row=row, column=col, value=value)
        c.font = Font(bold=True, color=branco)
        c.fill = PatternFill("solid", fgColor=azul)
        c.alignment = Alignment(horizontal="center", vertical="center")
        return c

    for col, h in enumerate(
        ["Data/Hora", "Usuário", "Ação", "Kit", "Detalhe", "IP", "Status"], 1):
        hdr_cell(1, col, h)
    for i, r in enumerate(registros):
        row = i + 2
        ws.cell(row, 1, (r["criado_em"] or "")[:16])
        ws.cell(row, 2, r["user_nome"] or "—")
        ws.cell(row, 3, r["acao"])
        ws.cell(row, 4, r["kit_desc"])
        ws.cell(row, 5, r["detalhe"] or "")
        ws.cell(row, 6, r["ip"] or "")
        ws.cell(row, 7, r["status"])
    larguras = [17, 18, 26, 20, 60, 15, 8]
    for col, largura in enumerate(larguras, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(col)].width = largura

    buf = BytesIO()
    wb.save(buf)
    return _Resp(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="historico_producao.xlsx"'},
    )


# ── Estoque ───────────────────────────────────────────────────────────────────

@app.get("/admin/estoque/exportar.xlsx")
@require_login
async def admin_estoque_exportar(request: Request):
    from fastapi.responses import Response as _Resp
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    from io import BytesIO

    itens = estoque_mod.listar_estoque()
    historico = estoque_mod.listar_historico_completo()

    wb = openpyxl.Workbook()
    azul, branco, cinza = "1A3A5C", "FFFFFF", "F4F7FB"

    def hdr_cell(ws, row, col, value):
        c = ws.cell(row=row, column=col, value=value)
        c.font = Font(bold=True, color=branco)
        c.fill = PatternFill("solid", fgColor=azul)
        c.alignment = Alignment(horizontal="center", vertical="center")
        return c

    # ── Aba Estoque ──────────────────────────────────────────────────────────
    ws1 = wb.active
    ws1.title = "Estoque"
    for col, h in enumerate(
        ["Tipo de Item", "Código de Barras", "Quantidade Atual",
         "Quantidade Mínima", "Status", "Cadastrado em"], 1):
        hdr_cell(ws1, 1, col, h)
    for i, item in enumerate(itens):
        row = i + 2
        abaixo = item["quantidade_atual"] <= item["quantidade_minima"]
        proximo = item["quantidade_atual"] <= item["quantidade_minima"] * 2 and not abaixo
        status = "Abaixo do mínimo" if abaixo else ("Próximo do mínimo" if proximo else "OK")
        ws1.cell(row, 1, item["tipo_nome"])
        ws1.cell(row, 2, item["codigo_barra"])
        ws1.cell(row, 3, item["quantidade_atual"])
        ws1.cell(row, 4, item["quantidade_minima"])
        ws1.cell(row, 5, status)
        ws1.cell(row, 6, item.get("criado_em") or "")
        if i % 2 == 0:
            for col in range(1, 7):
                ws1.cell(row, col).fill = PatternFill("solid", fgColor=cinza)
    for col, w in zip("ABCDEF", (32, 26, 16, 16, 20, 20)):
        ws1.column_dimensions[col].width = w
    ws1.freeze_panes = "A2"

    # ── Aba Histórico ────────────────────────────────────────────────────────
    ws2 = wb.create_sheet("Histórico")
    for col, h in enumerate(
        ["Tipo de Item", "Código de Barras", "Movimento", "Quantidade",
         "Observação", "Operador", "Data"], 1):
        hdr_cell(ws2, 1, col, h)
    movimento_labels = {
        "entrada": "Entrada", "saida": "Saída", "saida_cancelada": "Saída cancelada",
        "correcao": "Correção", "ajuste_minimo": "Ajuste mínimo",
        "status_compra": "Status de compra",
    }
    for i, m in enumerate(historico):
        row = i + 2
        ws2.cell(row, 1, m["tipo_nome"])
        ws2.cell(row, 2, m["codigo_barra"])
        ws2.cell(row, 3, movimento_labels.get(m["tipo"], m["tipo"]))
        ws2.cell(row, 4, m["quantidade"])
        ws2.cell(row, 5, m.get("observacao") or "")
        ws2.cell(row, 6, m.get("operador_nome") or "—")
        ws2.cell(row, 7, m.get("criado_em") or "")
        if i % 2 == 0:
            for col in range(1, 8):
                ws2.cell(row, col).fill = PatternFill("solid", fgColor=cinza)
    for col, w in zip("ABCDEFG", (32, 26, 18, 14, 32, 20, 20)):
        ws2.column_dimensions[col].width = w
    ws2.freeze_panes = "A2"

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    filename = f"estoque_{now_brt()[:10]}.xlsx"
    return _Resp(
        content=buf.read(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/admin/estoque", response_class=HTMLResponse)
@require_login
async def admin_estoque(request: Request):
    import app.zpl as _zpl
    itens = estoque_mod.listar_estoque()
    alertas = estoque_mod.alertas_abaixo_minimo()
    url_http = getattr(app.state, "url_http", _zpl.SERVIDOR_URL)
    return render(request, "admin_estoque.html", {
        "itens": itens,
        "alertas": alertas,
        "url_http_base": url_http,
        "status_compra_opcoes": estoque_mod.STATUS_COMPRA,
    })


@app.post("/admin/estoque")
@require_login
async def admin_estoque_post(request: Request):
    # Chamado a partir do popup de configuração de um tipo em /admin/items
    # ("+ Adicionar ao estoque") — a criação de estoque para um tipo novo
    # acontece na aba "Novo Item", que usa /admin/tipos/completo.
    user = get_current_user(request)
    form = await request.form()
    try:
        item_tipo_id = int(form.get("item_tipo_id", 0) or 0)
        codigo_barra = form.get("codigo_barra", "").strip()
        quantidade = max(0, int(form.get("quantidade", 0) or 0))
        quantidade_minima = max(0, int(form.get("quantidade_minima", 0) or 0))
        if not item_tipo_id or not codigo_barra:
            raise ValueError("Tipo e código de barras são obrigatórios.")
        estoque_mod.criar_estoque(item_tipo_id, codigo_barra, quantidade,
                                   quantidade_minima, user["id"])
    except Exception as e:
        return RedirectResponse(
            "/admin/items?erro=" + quote(f"Erro ao adicionar ao estoque: {e}"),
            status_code=302)
    return RedirectResponse("/admin/items?ok=estoque_criado", status_code=302)


@app.post("/admin/estoque/{estoque_id}/repor")
@require_permission("estoque_editar")
async def admin_estoque_repor(request: Request, estoque_id: int):
    user = get_current_user(request)
    form = await request.form()
    quantidade = max(1, int(form.get("quantidade", 1) or 1))
    observacao = form.get("observacao", "").strip()
    estoque_mod.repor_estoque(estoque_id, quantidade, user["id"], observacao)
    return RedirectResponse("/admin/items?ok=reposto", status_code=302)


@app.post("/admin/estoque/{estoque_id}/corrigir")
@require_permission("estoque_editar")
async def admin_estoque_corrigir(request: Request, estoque_id: int):
    user = get_current_user(request)
    form = await request.form()
    try:
        nova_quantidade = max(0, int(form.get("quantidade_atual", 0) or 0))
        estoque_mod.corrigir_quantidade(estoque_id, nova_quantidade, user["id"])
    except Exception as e:
        return RedirectResponse(
            "/admin/items?erro=" + quote(f"Erro ao corrigir quantidade: {e}"),
            status_code=302)
    return RedirectResponse("/admin/items?ok=quantidade_corrigida", status_code=302)


@app.post("/admin/estoque/{estoque_id}/minimo")
@require_permission("estoque_editar")
async def admin_estoque_minimo(request: Request, estoque_id: int):
    user = get_current_user(request)
    form = await request.form()
    novo_minimo = max(0, int(form.get("quantidade_minima", 0) or 0))
    estoque_mod.atualizar_minimo(estoque_id, novo_minimo, user["id"])
    return RedirectResponse("/admin/items?ok=minimo", status_code=302)


@app.post("/admin/estoque/{estoque_id}/status-compra")
@require_permission("estoque_editar")
async def admin_estoque_status_compra(request: Request, estoque_id: int):
    user = get_current_user(request)
    form = await request.form()
    estoque_mod.atualizar_status_compra(estoque_id, str(form.get("status_compra", "")), user["id"])
    return RedirectResponse("/admin/items?ok=status_compra", status_code=302)


@app.get("/admin/estoque/{estoque_id}/historico", response_class=HTMLResponse)
@require_login
async def admin_estoque_historico(request: Request, estoque_id: int):
    est = estoque_mod.buscar_por_id(estoque_id)
    if not est:
        return RedirectResponse("/admin/estoque", status_code=302)
    historico = estoque_mod.listar_historico(estoque_id)
    return render(request, "admin_estoque_historico.html", {
        "est": est, "historico": historico,
    })


@app.post("/admin/estoque/{estoque_id}/delete")
@require_admin
async def admin_estoque_delete(request: Request, estoque_id: int):
    estoque_mod.deletar_estoque(estoque_id)
    return RedirectResponse("/admin/estoque?ok=excluido", status_code=302)


@app.get("/admin/estoque/{estoque_id}/etiqueta", response_class=HTMLResponse)
@require_login
async def admin_estoque_etiqueta(request: Request, estoque_id: int):
    import app.zpl as _zpl
    est = estoque_mod.buscar_por_id(estoque_id)
    if not est:
        raise HTTPException(status_code=404)
    base = getattr(app.state, "servidor_url", _zpl.SERVIDOR_URL)
    url_qr = f"{base}/estoque/{estoque_id}"
    html = _zpl.generate_estoque_html_label(
        tipo_nome=est["tipo_nome"],
        codigo_barra=est["codigo_barra"],
        url_qr=url_qr,
    )
    return HTMLResponse(content=html)


@app.get("/admin/estoque/{estoque_id}/qrcode.svg")
@require_login
async def admin_estoque_qrcode(request: Request, estoque_id: int):
    from fastapi.responses import Response as FResponse
    import app.zpl as _zpl
    est = estoque_mod.buscar_por_id(estoque_id)
    if not est:
        raise HTTPException(status_code=404)
    base = getattr(app.state, "servidor_url", _zpl.SERVIDOR_URL)
    url = f"{base}/estoque/{estoque_id}"
    import segno, io as _io
    qr = segno.make(url, error="q")
    buf = _io.BytesIO()
    qr.save(buf, kind="svg", scale=8, border=3, xmldecl=True, nl=False)
    return FResponse(content=buf.getvalue(), media_type="image/svg+xml")


# ── Veículos ──────────────────────────────────────────────────────────────────

@app.get("/admin/veiculos", response_class=HTMLResponse)
@require_login
async def admin_veiculos(request: Request, cliente: str = ""):
    veiculos = veiculos_mod.listar(cliente=cliente or None, ativo=True)
    veiculos_inativos = veiculos_mod.listar(cliente=cliente or None, ativo=False)
    clientes_filtro = [c["nome"] for c in clientes_mod.listar()]
    clientes_cadastrados = clientes_mod.listar()
    garagens_cadastradas = garagens_mod.listar()
    return render(request, "admin_veiculos.html", {
        "veiculos": veiculos,
        "veiculos_inativos": veiculos_inativos,
        "clientes": clientes_filtro,
        "clientes_cadastrados": clientes_cadastrados,
        "garagens_cadastradas": garagens_cadastradas,
        "filtro_cliente": cliente,
    })


@app.post("/admin/veiculos", response_class=HTMLResponse)
@require_login
async def admin_veiculos_post(request: Request):
    form = await request.form()
    numero = str(form.get("numero", "")).strip()
    cliente = str(form.get("cliente", "")).strip()
    garagem = str(form.get("garagem", "")).strip()
    if not numero or not cliente:
        veiculos = veiculos_mod.listar()
        clientes_filtro = [c["nome"] for c in clientes_mod.listar()]
        clientes_cadastrados = clientes_mod.listar()
        garagens_cadastradas = garagens_mod.listar()
        return render(request, "admin_veiculos.html", {
            "veiculos": veiculos, "clientes": clientes_filtro,
            "clientes_cadastrados": clientes_cadastrados,
            "garagens_cadastradas": garagens_cadastradas,
            "filtro_cliente": "", "erro": "Número e cliente são obrigatórios.",
        })
    veiculos_mod.criar(numero, cliente, garagem)
    return RedirectResponse("/admin/veiculos?ok=criado", status_code=302)


@app.get("/admin/veiculos/modelo.xlsx")
@require_login
async def admin_veiculos_modelo(request: Request):
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    from io import BytesIO
    from fastapi.responses import Response as _Resp
    azul, branco = "1A3A5C", "FFFFFF"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Veículos"
    for col, h in enumerate(["Número do Veículo", "Cliente"], 1):
        c = ws.cell(1, col, h)
        c.font = Font(bold=True, color=branco)
        c.fill = PatternFill("solid", fgColor=azul)
        ws.column_dimensions[ws.cell(1, col).column_letter].width = 28
    ws.cell(2, 1, "VH-001"); ws.cell(2, 2, "Exemplo Cliente")
    ws.cell(3, 1, "VH-002"); ws.cell(3, 2, "Outro Cliente")
    buf = BytesIO()
    wb.save(buf); buf.seek(0)
    return _Resp(content=buf.read(),
                 media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                 headers={"Content-Disposition": "attachment; filename=modelo_veiculos.xlsx"})


@app.get("/admin/veiculos/import", response_class=HTMLResponse)
@require_login
async def admin_veiculos_import_form(request: Request):
    return render(request, "admin_veiculos_import.html", {})


@app.post("/admin/veiculos/import", response_class=HTMLResponse)
@require_login
async def admin_veiculos_import_post(request: Request):
    form = await request.form()
    arquivo = form.get("arquivo")
    if not arquivo or not arquivo.filename:
        return render(request, "admin_veiculos_import.html",
                      {"erro": "Selecione um arquivo .xlsx."})
    try:
        file_bytes = await _ler_upload(arquivo)
    except ValueError as e:
        return render(request, "admin_veiculos_import.html", {"erro": str(e)})
    resultado = veiculos_mod.importar_excel(file_bytes)
    return render(request, "admin_veiculos_import.html", {"resultado": resultado})


@app.get("/admin/veiculos/{veiculo_id}", response_class=HTMLResponse)
@require_login
async def admin_veiculo_detalhe(request: Request, veiculo_id: int):
    v = veiculos_mod.buscar(veiculo_id)
    if not v:
        raise HTTPException(status_code=404)
    historico = veiculos_mod.historico_kits(veiculo_id)
    clientes_cadastrados = clientes_mod.listar()
    garagens_cadastradas = garagens_mod.listar()
    return render(request, "admin_veiculo_detalhe.html", {
        "v": v, "historico": historico, "clientes": clientes_cadastrados,
        "garagens": garagens_cadastradas,
    })


@app.post("/admin/veiculos/{veiculo_id}/editar")
@require_login
async def admin_veiculo_editar(request: Request, veiculo_id: int):
    form = await request.form()
    numero = str(form.get("numero", "")).strip()
    cliente = str(form.get("cliente", "")).strip()
    garagem = str(form.get("garagem", "")).strip()
    if not numero or not cliente:
        v = veiculos_mod.buscar(veiculo_id)
        clientes = clientes_mod.listar()
        garagens_cadastradas = garagens_mod.listar()
        return render(request, "admin_veiculo_detalhe.html", {
            "v": v, "historico": veiculos_mod.historico_kits(veiculo_id),
            "clientes": clientes, "garagens": garagens_cadastradas,
            "erro": "Número e cliente são obrigatórios.",
        })
    veiculos_mod.atualizar(veiculo_id, numero, cliente, garagem)
    return RedirectResponse(f"/admin/veiculos/{veiculo_id}?ok=atualizado", status_code=302)


@app.post("/admin/veiculos/{veiculo_id}/desativar")
@require_login
async def admin_veiculo_desativar(request: Request, veiculo_id: int):
    veiculos_mod.desativar(veiculo_id)
    return RedirectResponse("/admin/veiculos?ok=desativado", status_code=302)


@app.post("/admin/veiculos/{veiculo_id}/reativar")
@require_login
async def admin_veiculo_reativar(request: Request, veiculo_id: int):
    veiculos_mod.reativar(veiculo_id)
    return RedirectResponse(f"/admin/veiculos/{veiculo_id}?ok=reativado", status_code=302)


@app.post("/admin/veiculos/{veiculo_id}/delete")
@require_admin
async def admin_veiculo_delete(request: Request, veiculo_id: int):
    veiculos_mod.deletar(veiculo_id)
    return RedirectResponse("/admin/veiculos?ok=excluido", status_code=302)


@app.post("/admin/clientes")
@require_login
async def admin_clientes_post(request: Request):
    form = await request.form()
    nome = str(form.get("nome", "")).strip()
    if not nome:
        return RedirectResponse("/admin/veiculos?erro_cliente=vazio", status_code=302)
    resultado = clientes_mod.criar(nome)
    if resultado is None:
        return RedirectResponse("/admin/veiculos?erro_cliente=duplicado", status_code=302)
    return RedirectResponse("/admin/veiculos?ok=cliente", status_code=302)


@app.post("/admin/clientes/{cliente_id}/delete")
@require_admin
async def admin_cliente_delete(request: Request, cliente_id: int):
    clientes_mod.deletar(cliente_id)
    return RedirectResponse("/admin/veiculos?ok=cliente_excluido", status_code=302)


@app.post("/admin/garagens")
@require_login
async def admin_garagens_post(request: Request):
    form = await request.form()
    nome = str(form.get("nome", "")).strip()
    if not nome:
        return RedirectResponse("/admin/veiculos?erro_garagem=vazio", status_code=302)
    resultado = garagens_mod.criar(nome)
    if resultado is None:
        return RedirectResponse("/admin/veiculos?erro_garagem=duplicado", status_code=302)
    return RedirectResponse("/admin/veiculos?ok=garagem", status_code=302)


@app.post("/admin/garagens/{garagem_id}/delete")
@require_admin
async def admin_garagem_delete(request: Request, garagem_id: int):
    garagens_mod.deletar(garagem_id)
    return RedirectResponse("/admin/veiculos?ok=garagem_excluida", status_code=302)


# ── Estoque — página mobile (acesso via QR code) ──────────────────────────────

@app.get("/estoque", response_class=HTMLResponse)
@require_login
async def estoque_lista_mobile(request: Request):
    """Lista de estoque somente leitura, otimizada para celular — sem
    formulários de ajuste. Edição continua em /admin/estoque (computador)
    e em /estoque/{id} (via QR da etiqueta, no local)."""
    itens = estoque_mod.listar_estoque()
    return render(request, "estoque_lista_mobile.html", {"itens": itens})


@app.get("/estoque/buscar")
async def estoque_buscar(request: Request, codigo: str = ""):
    """Resolve um código de barras ou o texto de um QR de estoque para a
    página de consulta correspondente — usado pelo scanner do /mobile."""
    est = estoque_mod.buscar_por_referencia(codigo)
    if not est:
        return RedirectResponse("/mobile?erro=estoque_nao_encontrado", status_code=302)
    return RedirectResponse(f"/estoque/{est['id']}", status_code=302)


@app.get("/estoque/{estoque_id}", response_class=HTMLResponse)
async def estoque_mobile(request: Request, estoque_id: int):
    # Consulta de quantidade é pública (como a verificação de kit) —
    # só o ajuste de estoque exige login.
    est = estoque_mod.buscar_por_id(estoque_id)
    if not est:
        return RedirectResponse("/mobile?erro=estoque_nao_encontrado", status_code=302)
    historico = estoque_mod.listar_historico(estoque_id, limit=8)
    return render(request, "estoque_mobile.html", {
        "est": est,
        "historico": historico,
        "ok": request.query_params.get("ok"),
    })


@app.post("/estoque/{estoque_id}/ajustar")
@require_login
async def estoque_mobile_ajustar(request: Request, estoque_id: int):
    user = get_current_user(request)
    form = await request.form()
    tipo = (form.get("tipo") or "").strip()
    motivo = (form.get("motivo") or "").strip()
    try:
        quantidade = max(1, int(form.get("quantidade") or 1))
    except (ValueError, TypeError):
        quantidade = 1

    def _erro(msg):
        est = estoque_mod.buscar_por_id(estoque_id)
        historico = estoque_mod.listar_historico(estoque_id, limit=8)
        return render(request, "estoque_mobile.html", {
            "est": est, "historico": historico,
            "erro": msg, "tipo_sel": tipo, "qtd_sel": quantidade,
        })

    if tipo not in ("entrada", "saida"):
        return _erro("Selecione Adicionar ou Subtrair.")
    if not motivo:
        return _erro("Motivo é obrigatório.")

    try:
        estoque_mod.ajustar_quantidade(estoque_id, tipo, quantidade, motivo, user["id"])
    except ValueError as e:
        return _erro(str(e))

    return RedirectResponse(f"/estoque/{estoque_id}?ok=1", status_code=302)


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

    # SOMENTE_HTTPS=1 desliga a porta 8080 (HTTP puro) quando o certificado
    # existe, deixando só a 8011 (HTTPS) no ar. Fica atrás de uma flag —
    # desligado por padrão — porque sem 8080 quem ainda depende de HTTP na
    # LAN (ou não tem o certificado instalado/confiável no aparelho) perde
    # acesso.
    _somente_https = os.getenv("SOMENTE_HTTPS", "").strip() in ("1", "true", "True")

    if _tem_ssl and _somente_https:
        uvicorn.run(
            "main:app", host="0.0.0.0", port=8011, reload=False,
            ssl_certfile="certs/cert.pem", ssl_keyfile="certs/key.pem",
        )
    elif _tem_ssl:
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
        uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=False)
