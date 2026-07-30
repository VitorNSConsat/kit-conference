from database import db, now_brt

MAX_ITENS_POR_SLOT = 6


def get_layout() -> dict:
    with db() as conn:
        row = conn.execute(
            "SELECT linhas, colunas FROM prateleira_layout WHERE id = 1"
        ).fetchone()
    return dict(row)


def listar_colunas() -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT coluna, nome FROM prateleira_colunas ORDER BY coluna"
        ).fetchall()
    return [dict(r) for r in rows]


def atualizar_layout(linhas: int, colunas: int, nomes: list[str]):
    """Redimensiona a grade e renomeia as colunas. Bloqueia reduzir linhas/
    colunas abaixo do que algum bloco já ocupa, pra não perder posições
    referenciadas silenciosamente."""
    if linhas < 1 or colunas < 1:
        raise ValueError("Linhas e colunas devem ser pelo menos 1.")
    if len(nomes) != colunas:
        raise ValueError("Quantidade de nomes não bate com a quantidade de colunas.")
    with db() as conn:
        maior_linha = conn.execute(
            "SELECT COALESCE(MAX(linha_fim), 0) AS m FROM prateleira_blocos"
        ).fetchone()["m"]
        maior_coluna = conn.execute(
            "SELECT COALESCE(MAX(coluna_fim), 0) AS m FROM prateleira_blocos"
        ).fetchone()["m"]
        if linhas < maior_linha:
            raise ValueError(
                f"Não é possível reduzir para {linhas} linha(s): "
                f"há um bloco usando a linha {maior_linha}. Remova-o primeiro."
            )
        if colunas < maior_coluna:
            raise ValueError(
                f"Não é possível reduzir para {colunas} coluna(s): "
                f"há um bloco usando a coluna {maior_coluna}. Remova-o primeiro."
            )
        conn.execute(
            "UPDATE prateleira_layout SET linhas = ?, colunas = ? WHERE id = 1",
            (linhas, colunas)
        )
        conn.execute("DELETE FROM prateleira_colunas")
        for idx, nome in enumerate(nomes, 1):
            nome = (nome or "").strip() or f"Coluna {idx}"
            conn.execute(
                "INSERT INTO prateleira_colunas (coluna, nome) VALUES (?, ?)",
                (idx, nome)
            )


def _overlaps(a: dict, b: dict) -> bool:
    return not (
        a["coluna_fim"] < b["coluna_ini"] or b["coluna_fim"] < a["coluna_ini"] or
        a["linha_fim"] < b["linha_ini"] or b["linha_fim"] < a["linha_ini"]
    )


def _mesma_regiao(a: dict, b: dict) -> bool:
    return (a["linha_ini"] == b["linha_ini"] and a["linha_fim"] == b["linha_fim"] and
            a["coluna_ini"] == b["coluna_ini"] and a["coluna_fim"] == b["coluna_fim"])


def criar_bloco(linha_ini: int, linha_fim: int, coluna_ini: int, coluna_fim: int, estoque_id: int):
    """Cria um bloco (região) ocupado por um item. Duas regiões só podem
    coexistir se forem idênticas (aí empilham, até MAX_ITENS_POR_SLOT) —
    uma seleção que cruza parcialmente outro bloco é rejeitada, pra manter
    a grade sempre renderizável sem cards se sobrepondo visualmente."""
    if linha_ini > linha_fim or coluna_ini > coluna_fim:
        raise ValueError("Região inválida: início não pode ser depois do fim.")
    layout = get_layout()
    if linha_ini < 1 or coluna_ini < 1 or linha_fim > layout["linhas"] or coluna_fim > layout["colunas"]:
        raise ValueError("Região fora dos limites da grade.")

    nova = {"linha_ini": linha_ini, "linha_fim": linha_fim,
            "coluna_ini": coluna_ini, "coluna_fim": coluna_fim}
    with db() as conn:
        existentes = [dict(r) for r in conn.execute("SELECT * FROM prateleira_blocos").fetchall()]
        mesma_regiao = [b for b in existentes if _mesma_regiao(b, nova)]
        for b in existentes:
            if b in mesma_regiao:
                continue
            if _overlaps(b, nova):
                raise ValueError(
                    "Essa seleção cruza parcialmente um bloco já existente — "
                    "remova-o primeiro ou selecione exatamente a mesma região."
                )
        if any(b["estoque_id"] == estoque_id for b in mesma_regiao):
            raise ValueError("Este item já está nesse bloco.")
        if len(mesma_regiao) >= MAX_ITENS_POR_SLOT:
            raise ValueError(f"Esse bloco já tem o máximo de {MAX_ITENS_POR_SLOT} itens.")
        conn.execute(
            "INSERT INTO prateleira_blocos "
            "(linha_ini, linha_fim, coluna_ini, coluna_fim, estoque_id, criado_em) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (linha_ini, linha_fim, coluna_ini, coluna_fim, estoque_id, now_brt())
        )


def remover_bloco(bloco_id: int):
    with db() as conn:
        conn.execute("DELETE FROM prateleira_blocos WHERE id = ?", (bloco_id,))


def listar_blocos() -> list[dict]:
    """Retorna os blocos agrupados por região — cada região tem a lista de
    itens que a ocupam (1 até MAX_ITENS_POR_SLOT, quando é a mesma região
    repetida por mais de um item)."""
    with db() as conn:
        rows = conn.execute(
            "SELECT pb.id AS bloco_id, pb.linha_ini, pb.linha_fim, pb.coluna_ini, pb.coluna_fim, "
            "e.id AS estoque_id, e.codigo_barra, e.quantidade_atual, e.quantidade_minima, "
            "it.nome AS tipo_nome "
            "FROM prateleira_blocos pb "
            "JOIN estoque e ON e.id = pb.estoque_id "
            "JOIN item_tipo it ON it.id = e.item_tipo_id "
            "ORDER BY pb.linha_ini, pb.coluna_ini, it.nome"
        ).fetchall()
    grupos: dict[tuple, dict] = {}
    ordem = []
    for r in rows:
        r = dict(r)
        chave = (r["linha_ini"], r["linha_fim"], r["coluna_ini"], r["coluna_fim"])
        if chave not in grupos:
            grupos[chave] = {
                "linha_ini": r["linha_ini"], "linha_fim": r["linha_fim"],
                "coluna_ini": r["coluna_ini"], "coluna_fim": r["coluna_fim"],
                "itens": [],
            }
            ordem.append(chave)
        grupos[chave]["itens"].append(r)
    return [grupos[k] for k in ordem]


def celulas_vazias(blocos: list[dict], layout: dict) -> list[tuple[int, int]]:
    """Posições (linha, coluna) não cobertas por nenhum bloco — usadas pra
    desenhar os placeholders vazios da grade."""
    cobertas = set()
    for b in blocos:
        for l in range(b["linha_ini"], b["linha_fim"] + 1):
            for c in range(b["coluna_ini"], b["coluna_fim"] + 1):
                cobertas.add((l, c))
    return [
        (l, c)
        for l in range(1, layout["linhas"] + 1)
        for c in range(1, layout["colunas"] + 1)
        if (l, c) not in cobertas
    ]


def listar_livre() -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT pl.id AS livre_id, e.id AS estoque_id, e.codigo_barra, "
            "e.quantidade_atual, e.quantidade_minima, it.nome AS tipo_nome "
            "FROM prateleira_livre pl "
            "JOIN estoque e ON e.id = pl.estoque_id "
            "JOIN item_tipo it ON it.id = e.item_tipo_id "
            "ORDER BY it.nome"
        ).fetchall()
    return [dict(r) for r in rows]


def adicionar_livre(estoque_id: int):
    with db() as conn:
        existe = conn.execute(
            "SELECT 1 FROM prateleira_livre WHERE estoque_id = ?", (estoque_id,)
        ).fetchone()
        if existe:
            raise ValueError("Este item já está na área livre.")
        conn.execute(
            "INSERT INTO prateleira_livre (estoque_id, criado_em) VALUES (?, ?)",
            (estoque_id, now_brt())
        )


def remover_livre(livre_id: int):
    with db() as conn:
        conn.execute("DELETE FROM prateleira_livre WHERE id = ?", (livre_id,))


def contar_status(blocos: list[dict], livre: list[dict]) -> dict:
    """Conta quantos itens (na grade + na área livre) estão em cada estado —
    usado no resumo do painel da TV (esgotado > crítico > atenção > normal)."""
    contagem = {"esgotado": 0, "critico": 0, "baixo": 0, "ok": 0}
    todos_itens = [item for b in blocos for item in b["itens"]] + livre
    for item in todos_itens:
        if item["quantidade_atual"] <= 0:
            contagem["esgotado"] += 1
        elif item["quantidade_atual"] <= item["quantidade_minima"]:
            contagem["critico"] += 1
        elif item["quantidade_atual"] <= item["quantidade_minima"] * 2:
            contagem["baixo"] += 1
        else:
            contagem["ok"] += 1
    return contagem
