"""Dados do painel da tela inicial da bipagem: os números do dia e, para cada
sessão em andamento, o progresso, o que falta e as últimas bipagens.

O progresso usa a MESMA conta da tela da sessão (unidades dos itens
obrigatórios, cada item limitado ao que exige), via conferencia_manual.listar(),
que já monta todas as sessões em poucas consultas — com centenas de kits em
montagem, uma consulta por sessão travaria a tela inicial.
"""
from statistics import median

import app.conferencia_manual as conferencia_mod
import app.sessions as sessions_mod
from database import db, now_brt

ULTIMOS_EVENTOS = 5
FALTANDO_MAX = 6


def _codigo_visivel(codigo: str) -> str:
    """Linhas internas (conjunto, estoque) não têm código que o operador reconheça."""
    codigo = codigo or ""
    return "" if codigo.startswith(("COMP:", "ESTOQUE:")) else codigo


def indicadores(user_id: int | None) -> dict:
    hoje = now_brt()[:10]
    with db() as conn:
        finalizados = conn.execute(
            "SELECT COUNT(*) AS total, SUM(CASE WHEN operador_id = ? THEN 1 ELSE 0 END) AS meus "
            "FROM kit_record WHERE substr(finalizado_em, 1, 10) = ?", (user_id, hoje)).fetchone()
        # Por kit finalizado hoje: da PRIMEIRA à ÚLTIMA bipagem — o tempo de
        # bipar de fato. Fica de fora o tempo com a sessão aberta antes de
        # começar e a espera depois (kit salvo pronto, aguardando finalizar).
        trabalho = [r[0] for r in conn.execute(
            "SELECT (julianday(b.ultima) - julianday(b.primeira)) * 1440 FROM kit_record kr "
            "JOIN (SELECT sessao_id, MIN(bipado_em) AS primeira, MAX(bipado_em) AS ultima FROM scan_session_items "
            "      WHERE status IS NULL OR status = 'completo' GROUP BY sessao_id) b ON b.sessao_id = kr.sessao_id "
            "WHERE substr(kr.finalizado_em, 1, 10) = ?", (hoje,)).fetchall()
            if r[0] is not None and r[0] >= 0]
        fila = conn.execute(
            "SELECT (SELECT COUNT(*) FROM print_queue WHERE status = 'aguardando') "
            "+ (SELECT COUNT(*) FROM print_queue_pausa WHERE status = 'aguardando')").fetchone()[0]
    return {
        "finalizados_hoje": finalizados["total"] or 0,
        "meus_hoje": finalizados["meus"] or 0,
        # Mediana, não média: um kit bipado em dois dias viraria horas e
        # puxaria a média pra cima sem dizer nada do ritmo do dia.
        "minutos_tipicos": round(median(trabalho)) if trabalho else None,
        "na_fila": fila or 0,
    }


def sessoes() -> list[dict]:
    """Sessões em andamento, da mais recente pra mais antiga, com o que a tela mostra."""
    base = {s["id"]: s for s in sessions_mod.listar_sessoes_em_andamento()}
    if not base:
        return []
    detalhadas = {s["id"]: s for s in conferencia_mod.listar()}
    ids = list(base)
    marcas = ",".join("?" * len(ids))
    with db() as conn:
        remessas = {r[0]: r[1] for r in conn.execute("SELECT id, nome FROM remessa").fetchall()}
        eventos: dict = {}
        for r in conn.execute(f"""
            SELECT * FROM (
                SELECT si.sessao_id, si.bipado_em, si.codigo_barra, COALESCE(si.quantidade, 1) AS qtd,
                       COALESCE(it.nome, '?') AS item,
                       ROW_NUMBER() OVER (PARTITION BY si.sessao_id ORDER BY si.id DESC) AS rn
                FROM scan_session_items si LEFT JOIN item_tipo it ON it.id = si.item_tipo_id
                WHERE si.sessao_id IN ({marcas}) AND (si.status IS NULL OR si.status = 'completo')
            ) WHERE rn <= ?""", ids + [ULTIMOS_EVENTOS]).fetchall():
            eventos.setdefault(r["sessao_id"], []).append({
                "hora": (r["bipado_em"] or "")[11:16], "data": (r["bipado_em"] or "")[:10],
                "item": r["item"], "codigo": _codigo_visivel(r["codigo_barra"]), "qtd": r["qtd"]})
    saida = []
    for sid, s in base.items():
        d = detalhadas.get(sid, {})
        obrig = [i for i in d.get("itens", []) if i["obrigatorio"] and i["estado"] != "fora"]
        exigido = sum(i["exigido"] for i in obrig)
        feito = sum(min(i["bipado"], i["exigido"]) for i in obrig)
        faltando = [i for i in obrig if i["estado"] != "completo"]
        pct = round(feito * 100 / exigido) if exigido else 100
        ev = eventos.get(sid, [])
        saida.append({
            "id": sid,
            "veiculo": s.get("veiculo") or "",
            "garagem": s.get("garagem") or "",
            "kit": s.get("kit_nome") or "",
            "cliente": s.get("cliente") or "",
            "operador": s.get("operador_nome") or "",
            "operador_id": s.get("operador_id"),
            "iniciado_em": s.get("iniciado_em") or "",
            "remessa": remessas.get(s.get("remessa_id")) or "",
            "feito": round(feito, 2), "exigido": round(exigido, 2), "pct": min(pct, 100),
            "pronto": bool(exigido) and feito >= exigido,
            "itens_total": len(obrig), "itens_completos": len(obrig) - len(faltando),
            "faltando": [{"item": i["descricao"], "faltam": i["faltam"], "unidade": i["unidade"]}
                         for i in faltando[:FALTANDO_MAX]],
            "faltando_mais": max(0, len(faltando) - FALTANDO_MAX),
            "eventos": ev,
            "ultima": ev[0]["data"] + " " + ev[0]["hora"] if ev else "",
        })
    saida.sort(key=lambda x: x["iniciado_em"], reverse=True)
    return saida


def finalizados_hoje(user_id: int | None) -> list[dict]:
    """Kits finalizados hoje, do mais recente pro mais antigo — a lista que abre
    ao clicar no número "Finalizados hoje"."""
    hoje = now_brt()[:10]
    with db() as conn:
        linhas = conn.execute(
            "SELECT kr.kit_id, kr.finalizado_em, COALESCE(v.numero, kr.veiculo, '') AS veiculo, "
            "       COALESCE(kr.garagem, '') AS garagem, t.nome AS kit, t.cliente, "
            "       COALESCE(u.nome, '—') AS operador, kr.operador_id "
            "FROM kit_record kr JOIN kit_template t ON t.id = kr.kit_template_id "
            "LEFT JOIN veiculos v ON v.id = kr.veiculo_id LEFT JOIN users u ON u.id = kr.operador_id "
            "WHERE substr(kr.finalizado_em, 1, 10) = ? ORDER BY kr.finalizado_em DESC", (hoje,)).fetchall()
    saida = []
    for r in linhas:
        d = dict(r)
        d["hora"] = (d["finalizado_em"] or "")[11:16]
        d["meu"] = d["operador_id"] == user_id
        saida.append(d)
    return saida
