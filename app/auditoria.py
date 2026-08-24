"""Trilha de auditoria.

Grava toda requisição que altera dados — de admin ou não. O registro é
feito por middleware, e não rota a rota, porque cobertura aqui é o
requisito: uma rota nova criada amanhã já nasce auditada, sem depender de
alguém lembrar de instrumentar.
"""

from database import db, now_brt
import app.datas as datas_mod

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
    # Antes de /toggle: a rota de vínculo não é toggle, mas convém ficar
    # explícita no log por ser mudança rastreável de kit ↔ veículo.
    if c.startswith("/kit-record/") and c.endswith("/veiculo"):
        return "VINCULO DE VEICULO"
    if "/toggle" in c:
        return "ALTERACAO DE STATUS"
    if "/admin/producao/" in c:
        if "/nota-fiscal" in c:
            return "PRODUCAO: NOTA FISCAL"
        if c.endswith("/transito"):
            return "PRODUCAO: EM TRANSITO"
        if "/cliente-instalando" in c:
            return "PRODUCAO: CHEGADA NO CLIENTE"
        if "/cliente-concluido" in c:
            return "PRODUCAO: INSTALACAO CONCLUIDA"
        if "/voltar" in c:
            return "PRODUCAO: VOLTAR ESTAGIO"
    if "/mover-garagem" in c:
        return "VEICULOS: MUDANCA DE GARAGEM"
    return "ALTERACAO"


def _filtros(data_ini: str, data_fim: str, user_id: str, acao: str,
             caminho_prefixo: str, busca: str = "") -> tuple[str, list]:
    """WHERE compartilhado pela contagem e pela listagem — se os dois
    divergirem, o total diz um número e a lista entrega outro.

    A busca por texto entra AQUI, no SQL, e não em Python depois: filtrando
    depois seria preciso carregar tudo antes de paginar, que é exatamente o
    que obrigava o teto de 2000 e escondia o resto."""
    where = "WHERE 1=1"
    sql_data, params = datas_mod.clausula("criado_em", data_ini, data_fim)
    where += sql_data
    if user_id and str(user_id).isdigit():
        where += " AND user_id = ?"
        params.append(int(user_id))
    if acao:
        where += " AND acao = ?"
        params.append(acao)
    if caminho_prefixo:
        where += " AND caminho LIKE ?"
        params.append(caminho_prefixo + "%")
    # Cada palavra tem que aparecer em algum dos campos (ordem não importa),
    # mesma regra da busca das outras telas.
    for palavra in _sem_acento_py(busca).split():
        where += (" AND sem_acento(COALESCE(detalhe,'') || ' ' || COALESCE(user_nome,'')"
                  " || ' ' || COALESCE(acao,'') || ' ' || COALESCE(caminho,'')"
                  " || ' ' || COALESCE(ip,'')) LIKE ?")
        params.append(f"%{palavra}%")
    return where, params


def _sem_acento_py(texto: str) -> str:
    import unicodedata
    t = str(texto or "").strip().lower()
    return "".join(c for c in unicodedata.normalize("NFD", t)
                   if unicodedata.category(c) != "Mn")


def contar(data_ini: str = "", data_fim: str = "", user_id: str = "",
           acao: str = "", caminho_prefixo: str = "", busca: str = "") -> int:
    """Quantos registros batem com o filtro — o total DE VERDADE, sem o
    teto da listagem. É ele que a tela mostra e que dimensiona a paginação."""
    where, params = _filtros(data_ini, data_fim, user_id, acao, caminho_prefixo, busca)
    with db() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM auditoria {where}", params).fetchone()[0]


def listar(data_ini: str = "", data_fim: str = "", user_id: str = "",
           acao: str = "", limite: int = 500, caminho_prefixo: str = "",
           offset: int = 0, busca: str = "") -> list[dict]:
    """`limite`/`offset` servem a paginação, não a um corte escondido: a
    tela pede uma página por vez e usa contar() pro total. Antes a tela
    pedia 2000 de uma vez e paginava esse pedaço — passando disso, os
    registros mais antigos do período simplesmente não existiam pra quem
    olhava, sem nenhum aviso."""
    where, params = _filtros(data_ini, data_fim, user_id, acao, caminho_prefixo, busca)
    query = f"SELECT * FROM auditoria {where} ORDER BY id DESC LIMIT ? OFFSET ?"
    params = params + [int(limite), int(offset)]
    with db() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def resumos_para_analise(data_ini: str = "", data_fim: str = "", user_id: str = "",
                         acao: str = "", busca: str = "") -> dict:
    """Cortes agregados do log, calculados NO BANCO — por dia, por usuário,
    por ação, por dia×usuário e por hora do dia.

    São GROUP BY sobre o mesmo WHERE da listagem, não contagem em Python
    sobre as linhas carregadas: assim os totais valem pro período inteiro
    mesmo quando ele tem centenas de milhares de registros, e a exportação
    não precisa trazer tudo pra memória só pra somar."""
    where, params = _filtros(data_ini, data_fim, user_id, acao, "", busca)

    def agrupar(expressao, rotulos, ordem):
        with db() as conn:
            rows = conn.execute(
                f"SELECT {expressao}, COUNT(*) AS total "
                f"FROM auditoria {where} GROUP BY {rotulos} ORDER BY {ordem}",
                params).fetchall()
        return [dict(r) for r in rows]

    return {
        "por_dia": agrupar(
            "SUBSTR(criado_em, 1, 10) AS dia", "dia", "dia DESC"),
        "por_usuario": agrupar(
            "COALESCE(user_nome, '(sem usuário)') AS usuario, user_id",
            "usuario, user_id", "total DESC"),
        "por_acao": agrupar("acao", "acao", "total DESC"),
        "por_dia_usuario": agrupar(
            "SUBSTR(criado_em, 1, 10) AS dia, COALESCE(user_nome, '(sem usuário)') AS usuario",
            "dia, usuario", "dia DESC, total DESC"),
        "por_hora": agrupar(
            "SUBSTR(criado_em, 12, 2) AS hora", "hora", "hora"),
        "por_status": agrupar(
            "COALESCE(status, 0) AS status", "status", "total DESC"),
    }


def acoes_distintas() -> list[str]:
    with db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT acao FROM auditoria ORDER BY acao"
        ).fetchall()
    return [r["acao"] for r in rows]
