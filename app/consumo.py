"""Análise de consumo de itens por kit.

Responde, para cada modelo de Kit: quanto de cada item ele realmente gasta
(vs. o que o template planeja), quantos kits ainda dá pra montar com o
estoque atual, qual item trava primeiro e em quanto tempo isso acaba.

Por que o consumo real não é a contagem de bipagem: a bipagem trava
exatamente no `quantidade_exigida` do template (register_scan rejeita ao
atingir o máximo), então a média bipada seria sempre igual ao planejado.
Quem revela o gasto de verdade é `estoque_movimentos` — cada patrimônio
novo desconta do estoque, inclusive numa substituição no meio da bipagem
(equipamento com defeito trocado gasta 2 unidades para 1 kit).
"""

from datetime import datetime

from database import db

# Abaixo disso a média é ruído — mostra o número, mas marcado.
MIN_KITS_CONFIANCA = 5


def _parse_data(valor) -> datetime | None:
    if not valor:
        return None
    try:
        return datetime.strptime(str(valor)[:10], "%Y-%m-%d")
    except ValueError:
        return None


def carregar_base() -> dict:
    """Uma passada no banco com tudo que os cálculos precisam — evita
    N+1 quando a lista de kits pede o resumo de vários templates."""
    with db() as conn:
        kits = conn.execute(
            "SELECT kr.sessao_id, kr.finalizado_em, kt.nome, kt.cliente "
            "FROM kit_record kr "
            "JOIN kit_template kt ON kt.id = kr.kit_template_id "
            "WHERE kt.tipo = 'kit'"
        ).fetchall()

        saidas = conn.execute(
            "SELECT em.sessao_id, e.item_tipo_id, SUM(em.quantidade) AS total "
            "FROM estoque_movimentos em "
            "JOIN estoque e ON e.id = em.estoque_id "
            "WHERE em.tipo = 'saida' AND em.sessao_id IS NOT NULL "
            "GROUP BY em.sessao_id, e.item_tipo_id"
        ).fetchall()

        estoque = conn.execute(
            "SELECT item_tipo_id, quantidade_atual, quantidade_minima FROM estoque"
        ).fetchall()

    kits_por_familia: dict[tuple, list] = {}
    for k in kits:
        kits_por_familia.setdefault((k["nome"], k["cliente"]), []).append({
            "sessao_id": k["sessao_id"],
            "finalizado_em": k["finalizado_em"],
        })

    saidas_por_sessao: dict[int, dict[int, float]] = {}
    for s in saidas:
        saidas_por_sessao.setdefault(s["sessao_id"], {})[s["item_tipo_id"]] = s["total"]

    return {
        "kits_por_familia": kits_por_familia,
        "saidas_por_sessao": saidas_por_sessao,
        "estoque_por_tipo": {
            e["item_tipo_id"]: {
                "quantidade_atual": e["quantidade_atual"],
                "quantidade_minima": e["quantidade_minima"],
            }
            for e in estoque
        },
    }


def _ritmo_semanal(kits: list) -> float | None:
    """Kits por semana, medido do primeiro ao último finalizado. Precisa de
    pelo menos 2 kits e de um intervalo real (todos no mesmo dia não dá
    pra projetar ritmo)."""
    datas = sorted(d for d in (_parse_data(k["finalizado_em"]) for k in kits) if d)
    if len(datas) < 2:
        return None
    dias = (datas[-1] - datas[0]).days
    if dias < 1:
        return None
    return len(datas) / (dias / 7)


def analise_template(template_id: int, base: dict | None = None) -> dict | None:
    """Análise completa de um Kit. Retorna None para Pedidos (são avulsos,
    média por kit não faz sentido) e para templates inexistentes."""
    import app.kit_templates as templates_mod

    template = templates_mod.buscar_template(template_id)
    if not template or template.get("tipo", "kit") != "kit":
        return None

    base = base or carregar_base()
    itens = templates_mod.get_itens_template(template_id)

    # Histórico agregado por família (todas as versões do mesmo nome+cliente),
    # comparado contra o plano da versão atual — senão cada "nova versão"
    # zeraria o histórico.
    kits = base["kits_por_familia"].get((template["nome"], template["cliente"]), [])
    n_kits = len(kits)

    consumo_real: dict[int, float] = {}
    for k in kits:
        for tipo_id, qtd in base["saidas_por_sessao"].get(k["sessao_id"], {}).items():
            consumo_real[tipo_id] = consumo_real.get(tipo_id, 0) + qtd

    linhas = []
    autonomias = []
    total_por_unidade: dict[str, float] = {}

    for item in itens:
        tipo_id = item["item_tipo_id"]
        planejado = item["quantidade_exigida"]
        unidade = item.get("unidade") or "un"
        total_por_unidade[unidade] = total_por_unidade.get(unidade, 0) + planejado

        est = base["estoque_por_tipo"].get(tipo_id)
        tem_estoque = est is not None

        # Só há consumo real observável para itens com estoque vinculado.
        # Para os demais a bipagem sempre bate o plano, então o planejado
        # já é o número correto.
        real_medio = None
        if tem_estoque and n_kits > 0 and tipo_id in consumo_real:
            real_medio = consumo_real[tipo_id] / n_kits

        usado = real_medio if real_medio is not None else planejado
        origem = "real" if real_medio is not None else "planejado"

        desvio_pct = None
        if real_medio is not None and planejado:
            desvio_pct = (real_medio / planejado - 1) * 100

        autonomia = None
        if tem_estoque and usado and usado > 0:
            autonomia = int(max(0, est["quantidade_atual"]) // usado)
            autonomias.append((autonomia, item["descricao"]))

        linhas.append({
            "item_tipo_id": tipo_id,
            "descricao": item["descricao"],
            "unidade": unidade,
            "planejado": planejado,
            "real_medio": real_medio,
            "desvio_pct": desvio_pct,
            "origem": origem,
            "tem_estoque": tem_estoque,
            "estoque_atual": est["quantidade_atual"] if tem_estoque else None,
            "autonomia": autonomia,
        })

    autonomia_kit, gargalo = (min(autonomias) if autonomias else (None, None))

    ritmo = _ritmo_semanal(kits)
    dias_restantes = None
    if autonomia_kit is not None and ritmo:
        dias_restantes = int(autonomia_kit / ritmo * 7)

    datas = sorted(d for d in (_parse_data(k["finalizado_em"]) for k in kits) if d)

    if n_kits == 0:
        confianca = "sem_historico"
    elif n_kits < MIN_KITS_CONFIANCA:
        confianca = "amostra_pequena"
    else:
        confianca = "ok"

    return {
        "n_kits": n_kits,
        "confianca": confianca,
        "primeiro_em": datas[0].strftime("%d/%m/%Y") if datas else None,
        "ultimo_em": datas[-1].strftime("%d/%m/%Y") if datas else None,
        "ritmo_semanal": ritmo,
        "autonomia_kit": autonomia_kit,
        "gargalo": gargalo,
        "dias_restantes": dias_restantes,
        "total_por_unidade": total_por_unidade,
        "itens": linhas,
    }


def resumo_todos_kits() -> dict[int, dict]:
    """{template_id: {autonomia_kit, gargalo, n_kits, confianca}} para a
    coluna da lista de Kits Cadastrados — uma única leitura do banco."""
    import app.kit_templates as templates_mod

    base = carregar_base()
    resumo = {}
    for t in templates_mod.listar_todos():
        if t.get("tipo", "kit") != "kit":
            continue
        analise = analise_template(t["id"], base=base)
        if analise:
            resumo[t["id"]] = {
                "autonomia_kit": analise["autonomia_kit"],
                "gargalo": analise["gargalo"],
                "n_kits": analise["n_kits"],
                "confianca": analise["confianca"],
            }
    return resumo
