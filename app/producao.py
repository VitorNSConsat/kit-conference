"""Esteira de produção: acompanha o ciclo de vida de um Kit da bipagem até
a instalação confirmada no cliente.

    Em Produção (Consat)  →  Produzido  →  Em Trânsito  →  Cliente: Em
    Produção (instalando) →  Cliente: Produzido (concluído)

"Em Produção (Consat)" não mora em kit_record — vem de scan_session ainda
em andamento, então não existe kit_record até a bipagem ser finalizada.
Do ponto de finalização em diante, tudo mora em kit_record.status_producao.

Só Kits entram nessa esteira — Pedidos continuam com o fluxo próprio de
"Feito/Não feito" que já existia.
"""

from database import db, now_brt

ESTAGIOS = ["produzido", "transito", "cliente_instalando", "cliente_concluido"]


def listar_em_producao() -> list[dict]:
    """Sessões de bipagem de Kit ainda em andamento — a etapa "Em Produção"
    do lado Consat, que não tem kit_record ainda."""
    with db() as conn:
        rows = conn.execute(
            "SELECT s.id AS sessao_id, s.iniciado_em, t.nome AS kit_nome, t.cliente, "
            "u.nome AS operador_nome "
            "FROM scan_session s "
            "JOIN kit_template t ON t.id = s.kit_template_id "
            "JOIN users u ON u.id = s.operador_id "
            "WHERE s.status = 'em_andamento' AND t.tipo = 'kit' "
            "ORDER BY s.iniciado_em"
        ).fetchall()
    return [dict(r) for r in rows]


def _listar_por_estagio(estagio: str) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT kr.kit_id, kr.finalizado_em, kr.transito_em, kr.cliente_instalando_em, "
            "kr.cliente_concluido_em, kr.veiculo, kr.garagem, kr.modelo, "
            "kt.nome AS kit_nome, kt.cliente, u.nome AS operador_nome "
            "FROM kit_record kr "
            "JOIN kit_template kt ON kt.id = kr.kit_template_id "
            "JOIN users u ON u.id = kr.operador_id "
            "WHERE kr.status_producao = ? AND kt.tipo = 'kit' "
            "ORDER BY kr.finalizado_em",
            (estagio,)
        ).fetchall()
    return [dict(r) for r in rows]


def listar_produzido() -> list[dict]:
    """Produzidos que ainda não foram marcados como em trânsito — fila
    acumulada, não reseta sozinha: o que não for movido continua aparecendo
    nos dias seguintes até alguém decidir."""
    return _listar_por_estagio("produzido")


def listar_transito() -> list[dict]:
    return _listar_por_estagio("transito")


def listar_cliente_instalando() -> list[dict]:
    return _listar_por_estagio("cliente_instalando")


def listar_cliente_concluido(limite: int | None = None) -> list[dict]:
    with db() as conn:
        query = (
            "SELECT kr.kit_id, kr.finalizado_em, kr.transito_em, kr.cliente_instalando_em, "
            "kr.cliente_concluido_em, kt.nome AS kit_nome, kt.cliente, u.nome AS operador_nome "
            "FROM kit_record kr "
            "JOIN kit_template kt ON kt.id = kr.kit_template_id "
            "JOIN users u ON u.id = kr.operador_id "
            "WHERE kr.status_producao = 'cliente_concluido' AND kt.tipo = 'kit' "
            "ORDER BY kr.cliente_concluido_em DESC"
        )
        if limite:
            query += f" LIMIT {int(limite)}"
        rows = conn.execute(query).fetchall()
    return [dict(r) for r in rows]


def _buscar_estagio(conn, kit_id: str) -> str | None:
    row = conn.execute(
        "SELECT status_producao FROM kit_record WHERE kit_id = ?", (kit_id,)
    ).fetchone()
    return row["status_producao"] if row else None


def marcar_transito(kit_ids: list[str]) -> int:
    """Move em lote de 'produzido' para 'transito'. Ignora silenciosamente
    kit_id que não esteja em 'produzido' (evita corrida: alguém marcou duas
    vezes, ou o kit já foi movido por outra aba)."""
    if not kit_ids:
        return 0
    agora = now_brt()
    with db() as conn:
        placeholders = ",".join("?" * len(kit_ids))
        cur = conn.execute(
            f"UPDATE kit_record SET status_producao = 'transito', transito_em = ? "
            f"WHERE kit_id IN ({placeholders}) AND status_producao = 'produzido'",
            [agora, *kit_ids]
        )
        return cur.rowcount


def marcar_cliente_instalando(kit_id: str) -> bool:
    with db() as conn:
        cur = conn.execute(
            "UPDATE kit_record SET status_producao = 'cliente_instalando', "
            "cliente_instalando_em = ? WHERE kit_id = ? AND status_producao = 'transito'",
            (now_brt(), kit_id)
        )
        return cur.rowcount > 0


def marcar_cliente_concluido(kit_id: str) -> bool:
    with db() as conn:
        cur = conn.execute(
            "UPDATE kit_record SET status_producao = 'cliente_concluido', "
            "cliente_concluido_em = ? WHERE kit_id = ? AND status_producao = 'cliente_instalando'",
            (now_brt(), kit_id)
        )
        return cur.rowcount > 0


def voltar_estagio(kit_id: str) -> bool:
    """Desfaz um clique errado, voltando um estágio e limpando o timestamp
    daquele estágio (ele deixa de valer)."""
    with db() as conn:
        atual = _buscar_estagio(conn, kit_id)
        if atual is None or atual == "produzido":
            return False  # já está no início da esteira, nada a desfazer
        idx = ESTAGIOS.index(atual)
        anterior = ESTAGIOS[idx - 1]
        campo_ts = {
            "transito": "transito_em",
            "cliente_instalando": "cliente_instalando_em",
            "cliente_concluido": "cliente_concluido_em",
        }[atual]
        conn.execute(
            f"UPDATE kit_record SET status_producao = ?, {campo_ts} = NULL WHERE kit_id = ?",
            (anterior, kit_id)
        )
        return True


def resumo() -> dict:
    """Contagens pra faixa de resumo do painel — uma consulta só."""
    with db() as conn:
        em_producao = conn.execute(
            "SELECT COUNT(*) FROM scan_session s JOIN kit_template t ON t.id = s.kit_template_id "
            "WHERE s.status = 'em_andamento' AND t.tipo = 'kit'"
        ).fetchone()[0]
        linhas = conn.execute(
            "SELECT kr.status_producao, COUNT(*) AS n FROM kit_record kr "
            "JOIN kit_template kt ON kt.id = kr.kit_template_id "
            "WHERE kt.tipo = 'kit' GROUP BY kr.status_producao"
        ).fetchall()
    contagem = {r["status_producao"]: r["n"] for r in linhas}
    return {
        "em_producao": em_producao,
        "produzido": contagem.get("produzido", 0),
        "transito": contagem.get("transito", 0),
        "cliente_instalando": contagem.get("cliente_instalando", 0),
        "cliente_concluido": contagem.get("cliente_concluido", 0),
    }
