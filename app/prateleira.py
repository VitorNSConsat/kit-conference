from database import db, now_brt

LINHAS = 4
COLUNAS = 3
MAX_ITENS_POR_SLOT = 6


def listar_grade() -> dict:
    """Retorna {(linha, coluna): [itens...]} para as 12 posições — cada
    posição pode ter até MAX_ITENS_POR_SLOT itens."""
    grade = {(l, c): [] for l in range(1, LINHAS + 1) for c in range(1, COLUNAS + 1)}
    with db() as conn:
        rows = conn.execute(
            "SELECT pp.linha, pp.coluna, e.id AS estoque_id, e.codigo_barra, "
            "e.quantidade_atual, e.quantidade_minima, it.nome AS tipo_nome "
            "FROM prateleira_posicoes pp "
            "JOIN estoque e ON e.id = pp.estoque_id "
            "JOIN item_tipo it ON it.id = e.item_tipo_id "
            "ORDER BY it.nome"
        ).fetchall()
    for r in rows:
        grade[(r["linha"], r["coluna"])].append(dict(r))
    return grade


def atribuir(linha: int, coluna: int, estoque_id: int):
    """Coloca um item na posição — se o item já ocupava outra posição,
    move-o (um item só fica em 1 slot por vez). Rejeita se a posição de
    destino já tiver o máximo de itens (a menos que o item já esteja nela)."""
    with db() as conn:
        atual = conn.execute(
            "SELECT linha, coluna FROM prateleira_posicoes WHERE estoque_id = ?", (estoque_id,)
        ).fetchone()
        ja_esta_aqui = atual and atual["linha"] == linha and atual["coluna"] == coluna
        if not ja_esta_aqui:
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM prateleira_posicoes WHERE linha = ? AND coluna = ?",
                (linha, coluna)
            ).fetchone()["n"]
            if count >= MAX_ITENS_POR_SLOT:
                raise ValueError(f"Esta posição já tem o máximo de {MAX_ITENS_POR_SLOT} itens.")
        conn.execute("DELETE FROM prateleira_posicoes WHERE estoque_id = ?", (estoque_id,))
        conn.execute(
            "INSERT INTO prateleira_posicoes (linha, coluna, estoque_id, criado_em) VALUES (?, ?, ?, ?)",
            (linha, coluna, estoque_id, now_brt())
        )


def remover(estoque_id: int):
    """Remove um item específico da prateleira (de onde quer que esteja)."""
    with db() as conn:
        conn.execute("DELETE FROM prateleira_posicoes WHERE estoque_id = ?", (estoque_id,))


def contar_status(grade: dict) -> dict:
    """Conta quantos itens da grade estão em cada estado — usado no resumo
    do painel da TV (esgotado > crítico > atenção > normal, mesma ordem de
    severidade usada nos cards)."""
    contagem = {"esgotado": 0, "critico": 0, "baixo": 0, "ok": 0}
    for itens in grade.values():
        for item in itens:
            if item["quantidade_atual"] <= 0:
                contagem["esgotado"] += 1
            elif item["quantidade_atual"] <= item["quantidade_minima"]:
                contagem["critico"] += 1
            elif item["quantidade_atual"] <= item["quantidade_minima"] * 2:
                contagem["baixo"] += 1
            else:
                contagem["ok"] += 1
    return contagem
