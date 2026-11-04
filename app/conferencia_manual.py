"""Conferência manual (celular): os kits em montagem, item a item, pra conferir
a caixa antes de o kit fechar — o caso de quem montou 90% e espera uma peça
chegar.

SÓ LEITURA. Nada aqui grava: a marcação de "conferi" é do celular de quem
está olhando (fica no navegador), e o kit continua seguindo pela bipagem
normal. A régua do que falta é a MESMA da finalização da bipagem
(sessions.validate_kit_complete): itens do modelo × o que já foi bipado.
"""
from database import db


def listar() -> list[dict]:
    """Todos os kits/pedidos com bipagem em andamento, cada um com os itens do
    modelo (pedido × bipado) e os que foram bipados fora do modelo.

    Três consultas no total, não uma por kit: com centenas de kits em
    montagem, uma por kit seria a diferença entre abrir na hora e travar."""
    with db() as conn:
        sessoes = [dict(r) for r in conn.execute("""
            SELECT s.id, s.kit_template_id, s.iniciado_em,
                   COALESCE(v.numero, s.veiculo, '') AS veiculo,
                   COALESCE(s.garagem, v.garagem, '') AS garagem,
                   t.nome AS kit_nome, t.cliente, t.tipo, u.nome AS operador_nome
            FROM scan_session s
            JOIN kit_template t ON t.id = s.kit_template_id
            JOIN users u ON u.id = s.operador_id
            LEFT JOIN veiculos v ON v.id = s.veiculo_id
            WHERE s.status = 'em_andamento'
            ORDER BY t.cliente, veiculo, s.iniciado_em
        """).fetchall()]
        if not sessoes:
            return []
        ids = [s["id"] for s in sessoes]
        tpls = sorted({s["kit_template_id"] for s in sessoes})
        m_ids, m_tpl = ",".join("?" * len(ids)), ",".join("?" * len(tpls))

        modelo: dict = {}
        for r in conn.execute(f"""
            SELECT ki.kit_template_id, ki.item_tipo_id, ki.quantidade_exigida, ki.obrigatorio,
                   COALESCE(ki.componente_codigo, '') AS conjunto,
                   COALESCE(it.nome, '[Tipo removido]') AS descricao,
                   COALESCE(it.unidade, 'un') AS unidade
            FROM kit_template_items ki
            LEFT JOIN item_tipo it ON it.id = ki.item_tipo_id
            WHERE ki.kit_template_id IN ({m_tpl})
            ORDER BY ki.id
        """, tpls).fetchall():
            modelo.setdefault(r["kit_template_id"], []).append(dict(r))

        # O que foi bipado em cada sessão (só linha completa — a mesma regra da
        # contagem da bipagem), com os códigos de patrimônio pra conferir na caixa.
        bipado: dict = {}
        for r in conn.execute(f"""
            SELECT si.sessao_id, si.item_tipo_id, SUM(COALESCE(si.quantidade, 1)) AS qtd,
                   GROUP_CONCAT(CASE WHEN COALESCE(si.quantidade, 1) = 1 THEN si.codigo_barra END, ', ') AS codigos,
                   it.nome AS descricao, COALESCE(it.unidade, 'un') AS unidade
            FROM scan_session_items si
            LEFT JOIN item_tipo it ON it.id = si.item_tipo_id
            WHERE si.sessao_id IN ({m_ids}) AND (si.status IS NULL OR si.status = 'completo')
            GROUP BY si.sessao_id, si.item_tipo_id
        """, ids).fetchall():
            bipado.setdefault(r["sessao_id"], {})[r["item_tipo_id"]] = dict(r)

    for s in sessoes:
        feitos = bipado.get(s["id"], {})
        itens, faltam = [], 0
        for m in modelo.get(s["kit_template_id"], []):
            b = feitos.get(m["item_tipo_id"]) or {}
            qtd = b.get("qtd") or 0
            exigido = m["quantidade_exigida"] or 0
            if qtd >= exigido:
                estado = "completo"
            elif qtd > 0:
                estado = "parcial"
            else:
                estado = "falta"
            if estado != "completo" and m["obrigatorio"]:
                faltam += 1
            itens.append({
                "item_tipo_id": m["item_tipo_id"], "descricao": m["descricao"],
                "exigido": _num(exigido), "bipado": _num(qtd), "unidade": m["unidade"],
                "faltam": _num(max(0, exigido - qtd)),
                "obrigatorio": bool(m["obrigatorio"]), "conjunto": m["conjunto"],
                "codigos": b.get("codigos") or "", "estado": estado,
            })
        no_modelo = {m["item_tipo_id"] for m in modelo.get(s["kit_template_id"], [])}
        for tipo_id, b in feitos.items():
            if tipo_id not in no_modelo:
                itens.append({
                    "item_tipo_id": tipo_id, "descricao": b["descricao"] or "?",
                    "exigido": 0, "bipado": _num(b["qtd"]), "unidade": b["unidade"],
                    "obrigatorio": False, "conjunto": "", "codigos": b["codigos"] or "",
                    "estado": "fora",
                })
        # O que falta primeiro: é atrás disso que quem abre esta tela está.
        ordem = {"falta": 0, "parcial": 1, "completo": 2, "fora": 3}
        itens.sort(key=lambda i: ordem[i["estado"]])
        s["itens"] = itens
        s["faltam"] = faltam
        s["total"] = sum(1 for i in itens if i["estado"] != "fora")
        s["completos"] = sum(1 for i in itens if i["estado"] == "completo")
    return sessoes


def _num(v):
    """2.0 → 2 (metros e unidades aparecem sem ',0' à toa)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    return int(f) if f == int(f) else round(f, 2)
