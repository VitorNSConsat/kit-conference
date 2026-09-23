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

import app.usuarios as usuarios_mod

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
