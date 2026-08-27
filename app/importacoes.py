"""Conferência da importação de veículos: o que a planilha fez, linha a linha.

Até aqui a importação devolvia só um placar ("12 novos, 3 atualizados, 8 sem
alteração") e sumia da tela no primeiro F5. Quem subia uma planilha de 200
linhas não tinha como responder as duas perguntas que sempre aparecem depois:

  • "esse veículo mudou de garagem — foi a planilha de ontem?"
  • "importei o arquivo certo? o que exatamente ele mexeu?"

Este módulo não valida nada. Ele GRAVA o que a importação já decidiu — os
mesmos números, as mesmas regras, o mesmo resultado — para que a conferência
possa ser aberta depois, e não só no instante do upload.

Situação de cada linha:

  novo      o veículo não existia e foi cadastrado;
  igual     já existia e a planilha não mudou nada nele;
  alterado  já existia e algum campo mudou — o antes e o depois ficam gravados;
  erro      a linha não foi aproveitada (sem número, sem cliente, incompleta).

Uma linha pode ter entrado E ter um AVISO junto (o caso comum: o modelo
digitado não bate com nenhum kit, o veículo importa e o modelo fica como
estava). Aviso não é situação — é um bilhete preso à linha, senão um veículo
criado com sucesso seria contado como "erro" e a conta não fecharia.
"""
from database import db, now_brt

SITUACOES = (
    ("novo",     "Novo",         "🟢"),
    ("igual",    "Já existente", "🔵"),
    ("alterado", "Alterado",     "🟡"),
    ("erro",     "Erro",         "🔴"),
)

TEXTO = {k: t for k, t, _ in SITUACOES}
SINAL = {k: s for k, _, s in SITUACOES}

# Os três campos que a planilha mexe. Ficam num só lugar para a tela, o
# resumo e o diff nunca discordarem sobre o que é "campo alterado".
CAMPOS = (("cliente", "Cliente"), ("garagem", "Garagem"), ("modelo", "Modelo"))


def registrar(resultado: dict, arquivo: str, user_id: int | None) -> int:
    """Guarda uma importação já executada e devolve o id dela.

    `resultado` é exatamente o que `veiculos.importar_excel()` devolveu — os
    contadores não são recalculados aqui, senão a tela poderia mostrar um
    número diferente do que a importação de fato fez."""
    itens = resultado.get("itens") or []
    erros = sum(1 for i in itens if i["situacao"] == "erro")
    avisos = sum(1 for i in itens if i.get("erro") and i["situacao"] != "erro")
    with db() as conn:
        imp_id = conn.execute(
            "INSERT INTO importacao (arquivo, criada_em, criada_por, total, "
            "novos, iguais, alterados, erros, avisos) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (arquivo or "", now_brt(), user_id, len(itens),
             sum(1 for i in itens if i["situacao"] == "novo"),
             sum(1 for i in itens if i["situacao"] == "igual"),
             sum(1 for i in itens if i["situacao"] == "alterado"),
             erros, avisos)
        ).lastrowid
        conn.executemany(
            "INSERT INTO importacao_item (importacao_id, linha, numero, situacao, "
            "veiculo_id, cliente_antes, cliente_depois, garagem_antes, "
            "garagem_depois, modelo_antes, modelo_depois, erro) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(imp_id, i["linha"], i["numero"], i["situacao"], i["veiculo_id"],
              i["cliente_antes"], i["cliente_depois"],
              i["garagem_antes"], i["garagem_depois"],
              i["modelo_antes"], i["modelo_depois"], i.get("erro") or "")
             for i in itens]
        )
    return imp_id


def _enfeitar(r: dict) -> dict:
    """Acrescenta à linha o que a tela precisa: rótulo, sinal e a lista de
    campos que mudaram, já com o antes e o depois lado a lado."""
    r["situacao_texto"] = TEXTO.get(r["situacao"], r["situacao"])
    r["sinal"] = SINAL.get(r["situacao"], "")
    mudancas = []
    for campo, rotulo in CAMPOS:
        antes = (r.get(campo + "_antes") or "").strip()
        depois = (r.get(campo + "_depois") or "").strip()
        # Célula vazia preserva o cadastro — quando nada mudou, os dois lados
        # são iguais e o campo simplesmente não entra na lista.
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
            "SELECT i.*, u.nome AS autor FROM importacao i "
            "LEFT JOIN users u ON u.id = i.criada_por WHERE i.id = ?",
            (importacao_id,)
        ).fetchone()
    return dict(row) if row else None


def itens(importacao_id: int, situacao: str = "", busca: str = "",
          so_avisos: bool = False) -> list[dict]:
    """As linhas da importação, na ordem da planilha.

    O filtro é feito no banco e não na tela porque a busca por número precisa
    valer sobre a importação INTEIRA — filtrar só o que está visível esconderia
    justamente o veículo que a pessoa está procurando."""
    sql = ["SELECT * FROM importacao_item WHERE importacao_id = ?"]
    args: list = [importacao_id]
    if situacao in TEXTO:
        sql.append("AND situacao = ?")
        args.append(situacao)
    if so_avisos:
        # Aviso é a linha que ENTROU mas tem algo pra conferir. A de erro
        # também tem texto, mas ela tem cor própria — misturar as duas faria
        # o filtro devolver mais gente do que o número mostrado no resumo.
        sql.append("AND TRIM(COALESCE(erro,'')) <> '' AND situacao <> 'erro'")
    busca = (busca or "").strip()
    if busca:
        sql.append("AND (LOWER(numero) LIKE ? OR LOWER(COALESCE(cliente_depois,'')) LIKE ? "
                   "OR LOWER(COALESCE(cliente_antes,'')) LIKE ? "
                   "OR LOWER(COALESCE(garagem_depois,'')) LIKE ? "
                   "OR LOWER(COALESCE(modelo_depois,'')) LIKE ?)")
        args += ["%" + busca.lower() + "%"] * 5
    sql.append("ORDER BY linha")
    with db() as conn:
        rows = conn.execute(" ".join(sql), args).fetchall()
    return [_enfeitar(dict(r)) for r in rows]


def contagens(importacao_id: int) -> dict:
    """Os totais do cabeçalho. Vêm sempre da tabela de linhas, para o resumo
    não descolar do que a lista mostra quando algo for filtrado."""
    with db() as conn:
        rows = conn.execute(
            "SELECT situacao, COUNT(*) AS n FROM importacao_item "
            "WHERE importacao_id = ? GROUP BY situacao", (importacao_id,)
        ).fetchall()
        avisos = conn.execute(
            "SELECT COUNT(*) FROM importacao_item WHERE importacao_id = ? "
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
            "SELECT i.*, u.nome AS autor FROM importacao i "
            "LEFT JOIN users u ON u.id = i.criada_por "
            "ORDER BY i.id DESC LIMIT ?", (limite,)
        ).fetchall()
    return [dict(r) for r in rows]


def do_veiculo(veiculo_id: int, limite: int = 20) -> list[dict]:
    """Toda vez que uma planilha tocou neste veículo. É a resposta para
    "quem mudou a garagem dele?" sem precisar abrir importação por importação."""
    with db() as conn:
        rows = conn.execute(
            "SELECT ii.*, i.criada_em, i.arquivo, u.nome AS autor "
            "FROM importacao_item ii "
            "JOIN importacao i ON i.id = ii.importacao_id "
            "LEFT JOIN users u ON u.id = i.criada_por "
            "WHERE ii.veiculo_id = ? ORDER BY ii.id DESC LIMIT ?",
            (veiculo_id, limite)
        ).fetchall()
    return [_enfeitar(dict(r)) for r in rows]
