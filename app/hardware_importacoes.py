"""Conferência da importação de ocorrências de Hardware: o que a planilha fez,
linha a linha. Mesmo papel que app/importacoes.py cumpre pra veículos, mas
com tabela própria (hardware_importacao/hardware_importacao_item) porque
aquelas são veículo-específicas (colunas cliente/garagem/modelo, FK pra
veiculos) e os campos que a importação de hardware mexe são outros
(cliente/produto/status).

Este módulo não valida nada. Ele GRAVA o que hardware.importar_excel() já
decidiu, para que a conferência possa ser reaberta depois do upload, e não só
no instante da resposta do POST.

Situação de cada linha: mesmo vocabulário da importação de veículos (novo /
igual / alterado / erro), ver hardware.importar_excel() pro que cada uma
significa aqui.
"""
from database import db, now_brt

SITUACOES = (
    ("novo",     "Novo",         ""),
    ("igual",    "Já existente", ""),
    ("alterado", "Alterado",     ""),
    ("erro",     "Erro",         ""),
)

TEXTO = {k: t for k, t, _ in SITUACOES}
SINAL = {k: s for k, _, s in SITUACOES}

CAMPOS = (("cliente", "Cliente"), ("produto", "Produto"), ("status", "Status"))


def registrar(resultado: dict, arquivo: str, user_id: int | None) -> int:
    """Guarda uma importação já executada e devolve o id dela.

    `resultado` é exatamente o que hardware.importar_excel() devolveu -- os
    contadores não são recalculados aqui, senão a tela poderia mostrar um
    número diferente do que a importação de fato fez."""
    itens = resultado.get("itens") or []
    erros = sum(1 for i in itens if i["situacao"] == "erro")
    avisos = sum(1 for i in itens if i.get("erro") and i["situacao"] != "erro")
    with db() as conn:
        imp_id = conn.execute(
            "INSERT INTO hardware_importacao (arquivo, criada_em, criada_por, total, "
            "novos, iguais, alterados, erros, avisos) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (arquivo or "", now_brt(), user_id, len(itens),
             sum(1 for i in itens if i["situacao"] == "novo"),
             sum(1 for i in itens if i["situacao"] == "igual"),
             sum(1 for i in itens if i["situacao"] == "alterado"),
             erros, avisos)
        ).lastrowid
        conn.executemany(
            "INSERT INTO hardware_importacao_item (importacao_id, linha, rotulo, situacao, "
            "ocorrencia_id, cliente_antes, cliente_depois, produto_antes, produto_depois, "
            "status_antes, status_depois, erro) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(imp_id, i["linha"], i["rotulo"], i["situacao"], i["ocorrencia_id"],
              i["cliente_antes"], i["cliente_depois"],
              i["produto_antes"], i["produto_depois"],
              i["status_antes"], i["status_depois"], i.get("erro") or "")
             for i in itens]
        )
    return imp_id


def _enfeitar(r: dict) -> dict:
    r["situacao_texto"] = TEXTO.get(r["situacao"], r["situacao"])
    r["sinal"] = SINAL.get(r["situacao"], "")
    mudancas = []
    for campo, rotulo in CAMPOS:
        antes = (r.get(campo + "_antes") or "").strip()
        depois = (r.get(campo + "_depois") or "").strip()
        if r["situacao"] == "novo":
            if depois:
                mudancas.append({"campo": rotulo, "antes": "", "depois": depois})
        elif antes != depois:
            mudancas.append({"campo": rotulo, "antes": antes, "depois": depois})
    r["mudancas"] = mudancas
    r["resumo"] = ", ".join(m["campo"] for m in mudancas)
    return r


def uma(importacao_id: int) -> dict | None:
    with db() as conn:
        row = conn.execute(
            "SELECT i.*, u.nome AS autor FROM hardware_importacao i "
            "LEFT JOIN users u ON u.id = i.criada_por WHERE i.id = ?",
            (importacao_id,)
        ).fetchone()
    return dict(row) if row else None


def itens(importacao_id: int, situacao: str = "", busca: str = "",
          so_avisos: bool = False) -> list[dict]:
    sql = ["SELECT * FROM hardware_importacao_item WHERE importacao_id = ?"]
    args: list = [importacao_id]
    if situacao in TEXTO:
        sql.append("AND situacao = ?")
        args.append(situacao)
    if so_avisos:
        sql.append("AND TRIM(COALESCE(erro,'')) <> '' AND situacao <> 'erro'")
    busca = (busca or "").strip()
    if busca:
        sql.append("AND (LOWER(rotulo) LIKE ? OR LOWER(COALESCE(cliente_depois,'')) LIKE ? "
                   "OR LOWER(COALESCE(cliente_antes,'')) LIKE ? "
                   "OR LOWER(COALESCE(produto_depois,'')) LIKE ?)")
        args += ["%" + busca.lower() + "%"] * 4
    sql.append("ORDER BY linha")
    with db() as conn:
        rows = conn.execute(" ".join(sql), args).fetchall()
    return [_enfeitar(dict(r)) for r in rows]


def contagens(importacao_id: int) -> dict:
    with db() as conn:
        rows = conn.execute(
            "SELECT situacao, COUNT(*) AS n FROM hardware_importacao_item "
            "WHERE importacao_id = ? GROUP BY situacao", (importacao_id,)
        ).fetchall()
        avisos = conn.execute(
            "SELECT COUNT(*) FROM hardware_importacao_item WHERE importacao_id = ? "
            "AND TRIM(COALESCE(erro,'')) <> '' AND situacao <> 'erro'",
            (importacao_id,)
        ).fetchone()[0]
    c = {k: 0 for k in TEXTO}
    for r in rows:
        c[r["situacao"]] = r["n"]
    c["avisos"] = avisos
    c["total"] = sum(c[k] for k in TEXTO)
    return c


def listar(limite: int = 40) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT i.*, u.nome AS autor FROM hardware_importacao i "
            "LEFT JOIN users u ON u.id = i.criada_por "
            "ORDER BY i.id DESC LIMIT ?", (limite,)
        ).fetchall()
    return [dict(r) for r in rows]
