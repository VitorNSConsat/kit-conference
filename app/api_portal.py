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
from pydantic import BaseModel, field_validator

import app.usuarios as usuarios_mod
import app.permissoes as permissoes_mod

router = APIRouter(prefix="/api/portal", tags=["portal"])


def verificar_chave(request: Request) -> None:
    chave = os.getenv("PORTAL_SERVICE_KEY", "")
    recebida = request.headers.get("X-Portal-Key", "")
    # compare_digest com `str` estoura TypeError se algum dos dois lados tiver
    # caractere fora do ASCII (documentado no próprio hmac) — e não devolve
    # False. Comparar bytes evita o 500 e mantém a garantia de 404 mesmo com
    # chave/cabeçalho acentuado.
    if not chave or not hmac.compare_digest(recebida.encode("utf-8"), chave.encode("utf-8")):
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

    @field_validator("nome")
    @classmethod
    def _nome_valido(cls, v: str) -> str:
        # Mesmas regras de usuarios_mod.renomear(), verificadas aqui — antes
        # de qualquer mutação — pra fechar o resíduo achado na revisão final:
        # sem isso, um nome inválido só falhava dentro de renomear(), que já
        # rodava por último (depois de admin/ativo já terem sido escritos),
        # deixando essa mudança aplicada mesmo com a edição inteira devolvendo
        # 400.
        v = (v or "").strip()
        if not v:
            raise ValueError("O nome não pode ficar vazio.")
        if len(v) > 80:
            raise ValueError("O nome ficou longo demais (máximo de 80 caracteres).")
        return v


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
def definir_permissoes_do_usuario(uid: int, body: PermissoesIn, request: Request):
    alvo = usuarios_mod.buscar(uid)
    if not alvo:
        raise HTTPException(404, "Usuário não encontrado")
    # Chave desconhecida/com erro de digitação não pode passar batido: como
    # definir_permissoes() funciona negando tudo que NÃO está na lista, uma
    # chave errada nega em silêncio a permissão de verdade que devia ter sido
    # mantida, e quem chamou (o Portal) recebe 200 sem saber que algo falhou.
    invalidas = sorted(set(body.permitidas) - set(permissoes_mod.PERMISSOES))
    if invalidas:
        raise HTTPException(
            400, f"Chave(s) de permissão desconhecida(s): {', '.join(invalidas)}")
    antes = permissoes_mod.negadas_do_usuario(uid)
    permissoes_mod.definir_permissoes(uid, set(body.permitidas))
    depois = permissoes_mod.negadas_do_usuario(uid)
    # Mesmo resumo legível da tela HTML (o que passou a negar / o que voltou
    # a liberar), em vez do JSON cru cortado em 200 caracteres pelo
    # _resumir_json do middleware de auditoria.
    passou_a_negar = sorted(permissoes_mod.PERMISSOES.get(c, c) for c in (depois - antes))
    voltou_a_permitir = sorted(permissoes_mod.PERMISSOES.get(c, c) for c in (antes - depois))
    partes = []
    if passou_a_negar:
        partes.append("negou " + ", ".join(passou_a_negar))
    if voltou_a_permitir:
        partes.append("liberou " + ", ".join(voltou_a_permitir))
    resumo = "; ".join(partes) if partes else "sem mudança"
    request.state.auditoria_detalhe = (
        f"Permissões de \"{alvo['nome']}\" ({alvo['username']}): {resumo}")
    return {"ok": True}


@router.post("/usuarios", dependencies=[Depends(verificar_chave)])
def criar_usuario(body: UsuarioIn):
    try:
        uid = usuarios_mod.criar(body.nome, body.username, body.senha, body.admin)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": uid}


@router.put("/usuarios/{uid}", dependencies=[Depends(verificar_chave)])
def editar_usuario(uid: int, body: UsuarioEdit, request: Request):
    atual = usuarios_mod.buscar(uid)
    if not atual:
        raise HTTPException(404, "Usuário não encontrado")
    try:
        # admin/ativo primeiro (só quando realmente mudam) e o nome por
        # último: assim, se a edição inteira for rejeitada (ex.: tentando
        # rebaixar/desativar o último admin ativo), a rejeição acontece
        # ANTES de qualquer escrita no banco — em vez do que acontecia
        # antes, em que o nome já tinha sido gravado (commit próprio) quando
        # a chamada seguinte estourava ValueError, deixando o rename valendo
        # mesmo com a edição toda devolvendo 400.
        if body.admin != bool(atual["admin"]):
            usuarios_mod.definir_admin(uid, body.admin)
        if body.ativo != bool(atual["ativo"]):
            usuarios_mod.definir_ativo(uid, body.ativo)
        usuarios_mod.renomear(uid, body.nome)
    except ValueError as e:
        raise HTTPException(400, str(e))
    partes = []
    if body.nome != atual["nome"]:
        partes.append(f"renomeado de \"{atual['nome']}\" para \"{body.nome}\"")
    if body.admin != bool(atual["admin"]):
        partes.append("promovido a administrador" if body.admin else "rebaixado a usuário comum")
    if body.ativo != bool(atual["ativo"]):
        partes.append("reativado" if body.ativo else "desativado")
    resumo = ", ".join(partes) if partes else "sem mudança"
    request.state.auditoria_detalhe = (
        f"Usuário \"{atual['nome']}\" ({atual['username']}): {resumo}")
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
