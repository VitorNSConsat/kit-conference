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

import re

from database import db, now_brt
import app.auditoria as auditoria_mod

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


_CAMPOS_KIT = (
    "kr.kit_id, kr.finalizado_em, kr.transito_em, kr.cliente_instalando_em, "
    "kr.cliente_concluido_em, kr.veiculo, kr.garagem, kr.modelo, "
    "kr.nota_fiscal, kr.nota_fiscal_data, "
    "kt.nome AS kit_nome, kt.cliente, u.nome AS operador_nome"
)


def _listar_por_estagio(estagio: str) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            f"SELECT {_CAMPOS_KIT} "
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
            f"SELECT {_CAMPOS_KIT} "
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


def atualizar_nota_fiscal(kit_id: str, nota_fiscal: str, nota_fiscal_data: str,
                           motivo: str = "") -> bool:
    """Registro manual só pro controle interno — não faz parte da esteira,
    não trava nem depende de estágio.

    Se já havia nota fiscal salva e o valor está mudando, exige motivo —
    quem só está preenchendo pela primeira vez não precisa justificar nada.
    Sem motivo nesse caso, não grava e retorna False; o motivo em si não é
    guardado em kit_record, ele fica só no log de auditoria (que já
    registra os campos exatos submetidos no formulário)."""
    nota_fiscal = nota_fiscal.strip()
    nota_fiscal_data = nota_fiscal_data.strip()
    motivo = motivo.strip()
    with db() as conn:
        atual = conn.execute(
            "SELECT nota_fiscal, nota_fiscal_data FROM kit_record WHERE kit_id = ?",
            (kit_id,)
        ).fetchone()
        tinha_valor = bool(atual and (atual["nota_fiscal"] or "").strip())
        mudou = tinha_valor and (
            (atual["nota_fiscal"] or "").strip() != nota_fiscal
            or (atual["nota_fiscal_data"] or "").strip() != nota_fiscal_data
        )
        if mudou and not motivo:
            return False
        conn.execute(
            "UPDATE kit_record SET nota_fiscal = ?, nota_fiscal_data = ? WHERE kit_id = ?",
            (nota_fiscal, nota_fiscal_data or None, kit_id)
        )
        return True


_RE_KIT_ID_PATH = re.compile(
    r"^/admin/producao/([^/]+)/(cliente-instalando|cliente-concluido|voltar|nota-fiscal)$"
)
_RE_KIT_IDS_DETALHE = re.compile(r"kit_ids=([^|]+)")


def _kit_ids_da_linha(caminho: str, detalhe: str) -> list[str]:
    """Extrai o(s) kit_id envolvido(s) num registro de auditoria da
    esteira — a maioria das rotas leva o kit_id na própria URL, só o envio
    em lote pra 'em trânsito' leva vários dentro do corpo do formulário."""
    m = _RE_KIT_ID_PATH.match(caminho)
    if m:
        return [m.group(1)]
    if caminho == "/admin/producao/transito":
        return [v.strip() for v in _RE_KIT_IDS_DETALHE.findall(detalhe or "")]
    return []


def listar_historico(data_ini: str = "", data_fim: str = "", limite: int = 500) -> list[dict]:
    """Histórico de ações manuais da esteira (mudança de estágio + edição
    de nota fiscal), lido da auditoria geral (que já cobre toda rota de
    /admin/producao automaticamente) e enriquecido com veículo/garagem
    quando dá pra casar o kit_id da linha."""
    linhas = auditoria_mod.listar(
        data_ini=data_ini, data_fim=data_fim,
        caminho_prefixo="/admin/producao/", limite=limite
    )
    with db() as conn:
        kits = {
            r["kit_id"]: dict(r)
            for r in conn.execute(
                "SELECT kr.kit_id, kr.veiculo, kr.garagem, kt.nome AS kit_nome "
                "FROM kit_record kr JOIN kit_template kt ON kt.id = kr.kit_template_id"
            ).fetchall()
        }
    for linha in linhas:
        descricoes = []
        for kid in _kit_ids_da_linha(linha["caminho"], linha["detalhe"] or ""):
            info = kits.get(kid)
            if info and (info["veiculo"] or "").strip():
                descricoes.append(info["veiculo"])
            elif info:
                descricoes.append(info["kit_nome"])
            else:
                descricoes.append(kid)
        linha["kit_desc"] = ", ".join(descricoes) if descricoes else "—"
    return linhas


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
