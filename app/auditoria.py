"""Trilha de auditoria.

Grava toda requisição que altera dados — de admin ou não. O registro é
feito por middleware, e não rota a rota, porque cobertura aqui é o
requisito: uma rota nova criada amanhã já nasce auditada, sem depender de
alguém lembrar de instrumentar.
"""

from database import db, now_brt

# Nunca guardar o valor destes campos no detalhe do log.
_CAMPOS_SENSIVEIS = {"password", "senha", "senha_atual", "nova_senha",
                     "confirmar_senha", "password_hash", "secret", "token"}

_LIMITE_DETALHE = 2000


def _resumir_form(form) -> str:
    """Serializa o formulário para o log, mascarando senhas e cortando
    conteúdo de arquivo (um .xlsx inteiro não vai para dentro do banco)."""
    partes = []
    for chave, valor in form.items():
        if chave.lower() in _CAMPOS_SENSIVEIS:
            partes.append(f"{chave}=***")
            continue
        # UploadFile e afins: guarda só o nome do arquivo
        nome_arquivo = getattr(valor, "filename", None)
        if nome_arquivo is not None:
            partes.append(f"{chave}=<arquivo:{nome_arquivo}>")
            continue
        texto = str(valor)
        if len(texto) > 200:
            texto = texto[:200] + "…"
        partes.append(f"{chave}={texto}")
    resumo = " | ".join(partes)
    return resumo[:_LIMITE_DETALHE]


def registrar(user_id: int | None, user_nome: str | None, acao: str,
              metodo: str, caminho: str, detalhe: str = "",
              ip: str = "", status: int | None = None) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO auditoria "
            "(user_id, user_nome, acao, metodo, caminho, detalhe, ip, status, criado_em) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, user_nome, acao, metodo, caminho,
             detalhe or None, ip or None, status, now_brt())
        )


def classificar(caminho: str) -> str:
    """Rótulo legível da ação, derivado da rota — o que aparece na tela."""
    c = caminho.lower()
    if "/delete" in c or "/excluir" in c or "/remover" in c:
        return "EXCLUSAO"
    if "/login" in c:
        return "LOGIN"
    if "/logout" in c:
        return "LOGOUT"
    if "/finalize" in c:
        return "FINALIZACAO DE KIT"
    if "/cancel" in c:
        return "CANCELAMENTO"
    if "/import" in c or "/importar" in c:
        return "IMPORTACAO"
    if "/usuarios" in c:
        return "GESTAO DE USUARIOS"
    if "/scan" in c or "/session" in c:
        return "BIPAGEM"
    if "/toggle" in c:
        return "ALTERACAO DE STATUS"
    return "ALTERACAO"


def listar(data_ini: str = "", data_fim: str = "", user_id: str = "",
           acao: str = "", limite: int = 500) -> list[dict]:
    query = "SELECT * FROM auditoria WHERE 1=1"
    params: list = []
    if data_ini:
        query += " AND DATE(criado_em) >= ?"
        params.append(data_ini)
    if data_fim:
        query += " AND DATE(criado_em) <= ?"
        params.append(data_fim)
    if user_id and str(user_id).isdigit():
        query += " AND user_id = ?"
        params.append(int(user_id))
    if acao:
        query += " AND acao = ?"
        params.append(acao)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(int(limite))
    with db() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def acoes_distintas() -> list[str]:
    with db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT acao FROM auditoria ORDER BY acao"
        ).fetchall()
    return [r["acao"] for r in rows]
