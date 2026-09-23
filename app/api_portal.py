"""API administrativa para o Portal (painel de pessoas) falar com o Kit.

Só existe pra isso — quem usa o Kit no dia a dia continua pelas telas HTML de
sempre (/admin/usuarios), sem nenhuma mudança. Autenticação por chave de
servidor (PORTAL_SERVICE_KEY), nunca por sessão: quem chama aqui é o Portal,
não uma pessoa logada no navegador.

Chave errada ou ausente devolve 404, não 401/403 — não é pra dar pista de que
a rota existe pra quem não tem a chave.
"""
import hmac
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

import app.usuarios as usuarios_mod
import app.permissoes as permissoes_mod

router = APIRouter(prefix="/api/portal", tags=["portal"])


def verificar_chave(request: Request) -> None:
    chave = os.getenv("PORTAL_SERVICE_KEY", "")
    recebida = request.headers.get("X-Portal-Key", "")
    if not chave or not hmac.compare_digest(recebida, chave):
        raise HTTPException(404)
    request.state.auditoria_user_nome = "Portal"


def _usuario_out(u: dict) -> dict:
    return {"id": u["id"], "nome": u["nome"], "username": u["username"],
            "admin": bool(u["admin"]), "ativo": bool(u["ativo"]), "criado_em": u["criado_em"]}


@router.get("/usuarios", dependencies=[Depends(verificar_chave)])
def listar_usuarios():
    return [_usuario_out(u) for u in usuarios_mod.listar()]


class PermissoesIn(BaseModel):
    permitidas: list[str]


class UsuarioIn(BaseModel):
    nome: str
    username: str
    senha: str
    admin: bool = False


class UsuarioEdit(BaseModel):
    nome: str
    admin: bool
    ativo: bool


class SenhaIn(BaseModel):
    senha: str


@router.get("/permissoes/catalogo", dependencies=[Depends(verificar_chave)])
def catalogo_permissoes():
    return {"grupos": [[titulo, list(chaves.items())] for titulo, chaves in permissoes_mod.GRUPOS]}


@router.get("/usuarios/{uid}/permissoes", dependencies=[Depends(verificar_chave)])
def permissoes_do_usuario(uid: int):
    if not usuarios_mod.buscar(uid):
        raise HTTPException(404, "Usuário não encontrado")
    negadas = permissoes_mod.negadas_do_usuario(uid)
    return {"permitidas": [c for c in permissoes_mod.PERMISSOES if c not in negadas]}


@router.put("/usuarios/{uid}/permissoes", dependencies=[Depends(verificar_chave)])
def definir_permissoes_do_usuario(uid: int, body: PermissoesIn):
    if not usuarios_mod.buscar(uid):
        raise HTTPException(404, "Usuário não encontrado")
    permissoes_mod.definir_permissoes(uid, set(body.permitidas))
    return {"ok": True}


@router.post("/usuarios", dependencies=[Depends(verificar_chave)])
def criar_usuario(body: UsuarioIn):
    try:
        uid = usuarios_mod.criar(body.nome, body.username, body.senha, body.admin)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": uid}


@router.put("/usuarios/{uid}", dependencies=[Depends(verificar_chave)])
def editar_usuario(uid: int, body: UsuarioEdit):
    if not usuarios_mod.buscar(uid):
        raise HTTPException(404, "Usuário não encontrado")
    try:
        usuarios_mod.renomear(uid, body.nome)
        usuarios_mod.definir_admin(uid, body.admin)
        usuarios_mod.definir_ativo(uid, body.ativo)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@router.post("/usuarios/{uid}/senha", dependencies=[Depends(verificar_chave)])
def trocar_senha_usuario(uid: int, body: SenhaIn):
    if not usuarios_mod.buscar(uid):
        raise HTTPException(404, "Usuário não encontrado")
    try:
        usuarios_mod.trocar_senha(uid, body.senha)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}
