"""Conferência manual (celular): os kits em montagem, item a item, pra conferir
a caixa antes de o kit fechar — o caso de quem montou 90% e espera uma peça
chegar.

A marcação de "conferi" fica no celular de quem está olhando; o único registro
no sistema é a PRÉ-CONFERÊNCIA (botão no fim do kit), que guarda só a lista dos
itens marcados. Nada mexe na bipagem: o kit continua pela bipagem normal. A régua do que falta é a MESMA da finalização da bipagem
(sessions.validate_kit_complete): itens do modelo × o que já foi bipado.
"""
from database import db, now_brt
from app.validacoes import _verifica_em_conjunto


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
        # Conjunto: a MESMA regra da verificação original (validacoes) — tocar
        # num item marca junto os outros do mesmo conjunto que JÁ estão no kit
        # (bipados), a menos que o conjunto esteja marcado pra conferir item a
        # item. Peça do conjunto que ainda não chegou fica de fora: continua
        # pendente, que é o ponto da pré-conferência.
        por_codigo: dict = {}
        for i in itens:
            if i["conjunto"] and i["estado"] in ("completo", "parcial"):
                por_codigo.setdefault(i["conjunto"], []).append(i)
        for codigo, membros in por_codigo.items():
            junto = len(membros) >= 2 and _verifica_em_conjunto(s["kit_template_id"], codigo)
            for i in membros:
                if junto:
                    i["grupo"] = codigo
                    i["junto_com"] = [o["descricao"] for o in membros if o is not i]
        for i in itens:
            i.setdefault("grupo", "")
            i.setdefault("junto_com", [])
        # O que falta primeiro: é atrás disso que quem abre esta tela está.
        ordem = {"falta": 0, "parcial": 1, "completo": 2, "fora": 3}
        itens.sort(key=lambda i: ordem[i["estado"]])
        s["itens"] = itens
        s["faltam"] = faltam
        s["total"] = sum(1 for i in itens if i["estado"] != "fora")
        s["completos"] = sum(1 for i in itens if i["estado"] == "completo")
    # Pré-conferência já registrada (se houver): a tela mostra quando/quem e
    # já começa com esses itens marcados.
    registradas = da_sessao([s["id"] for s in sessoes])
    for s in sessoes:
        s["pre"] = registradas.get(s["id"])
    return sessoes


def _num(v):
    """2.0 → 2 (metros e unidades aparecem sem ',0' à toa)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    return int(f) if f == int(f) else round(f, 2)


# ── Pré-conferência registrada no sistema ────────────────────────────────────
# A conferência manual continua só no celular até alguém tocar em "Registrar
# pré-conferência". Aí vai pro banco SÓ a lista dos itens marcados — nada na
# bipagem, nada na conferência oficial. Quando o kit fica pronto, a tela de
# conferência dele pergunta se aplica: sim marca esses itens como conferidos
# (o que não foi pré-conferido, como a peça que faltava, continua pendente);
# não segue a conferência normal.

def registrar(sessao_id: int, item_tipo_ids: list, user_id: int | None) -> dict:
    """Grava (ou substitui) a pré-conferência de uma bipagem em andamento.
    Só entram itens do modelo daquele kit."""
    import json
    with db() as conn:
        s = conn.execute("SELECT kit_template_id, status FROM scan_session WHERE id = ?",
                         (sessao_id,)).fetchone()
        if not s:
            raise ValueError("Kit não encontrado.")
        if s["status"] != "em_andamento":
            raise ValueError("Este kit não está mais em montagem.")
        do_modelo = {r[0] for r in conn.execute(
            "SELECT item_tipo_id FROM kit_template_items WHERE kit_template_id = ?",
            (s["kit_template_id"],)).fetchall()}
        ids = sorted({int(i) for i in item_tipo_ids if str(i).isdigit() and int(i) in do_modelo})
        conn.execute(
            "INSERT INTO pre_conferencia (sessao_id, item_tipo_ids, usuario_id, registrado_em) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(sessao_id) DO UPDATE SET "
            "item_tipo_ids = excluded.item_tipo_ids, usuario_id = excluded.usuario_id, "
            "registrado_em = excluded.registrado_em, decisao = NULL, decidido_por = NULL, decidido_em = NULL",
            (sessao_id, json.dumps(ids), user_id, now_brt()))
    return {"itens": len(ids), "item_tipo_ids": ids}


def da_sessao(sessao_ids: list) -> dict:
    """{sessao_id: {item_tipo_ids, registrado_em, usuario_nome, decisao}}."""
    import json
    if not sessao_ids:
        return {}
    marcas = ",".join("?" * len(sessao_ids))
    with db() as conn:
        rows = conn.execute(
            f"SELECT p.*, u.nome AS usuario_nome FROM pre_conferencia p "
            f"LEFT JOIN users u ON u.id = p.usuario_id WHERE p.sessao_id IN ({marcas})",
            list(sessao_ids)).fetchall()
    saida = {}
    for r in rows:
        d = dict(r)
        d["item_tipo_ids"] = json.loads(d["item_tipo_ids"] or "[]")
        saida[d["sessao_id"]] = d
    return saida


def pendente_do_kit(kit_id: str) -> dict | None:
    """A pré-conferência do kit pronto que ainda espera a decisão (aplicar ou não)."""
    with db() as conn:
        k = conn.execute("SELECT sessao_id FROM kit_record WHERE kit_id = ?", (kit_id,)).fetchone()
    if not k:
        return None
    p = da_sessao([k["sessao_id"]]).get(k["sessao_id"])
    return p if p and not p["decisao"] else None


def decidir(kit_id: str, aplicar: bool, user_id: int | None) -> int:
    """Aplica (marca como conferidos os itens pré-conferidos que o kit tem) ou
    ignora. Devolve quantos itens foram marcados.

    Marca item a item, SEM puxar o conjunto junto (o toque na tela puxa): a
    peça que faltou na pré-conferência tem que continuar pendente mesmo se for
    do mesmo conjunto de uma que foi pré-conferida."""
    p = pendente_do_kit(kit_id)
    if not p:
        return 0
    marcados = 0
    with db() as conn:
        k = conn.execute("SELECT kit_template_id, sessao_id FROM kit_record WHERE kit_id = ?",
                         (kit_id,)).fetchone()
        exigidos = {r[0] for r in conn.execute(
            "SELECT item_tipo_id FROM kit_template_items WHERE kit_template_id = ?",
            (k["kit_template_id"],)).fetchall()}
        if aplicar:
            agora = now_brt()
            for tid in p["item_tipo_ids"]:
                if tid in exigidos:
                    cur = conn.execute(
                        "INSERT OR IGNORE INTO kit_verificacao_itens "
                        "(kit_id, item_tipo_id, conferido_por, conferido_em) VALUES (?, ?, ?, ?)",
                        (kit_id, tid, user_id, agora))
                    marcados += cur.rowcount
        conn.execute("UPDATE pre_conferencia SET decisao = ?, decidido_por = ?, decidido_em = ? "
                     "WHERE sessao_id = ?",
                     ("aplicada" if aplicar else "ignorada", user_id, now_brt(), k["sessao_id"]))
    return marcados


# ── Relatório de pré-check ───────────────────────────────────────────────────
SITUACOES_PRECHECK = (
    ("montagem", "Em montagem"),
    ("aguardando", "Aguardando validação"),
    ("dupla", "Verificado 2 vezes"),
)


def relatorio(data_ini: str = "", data_fim: str = "", situacao=None,
              cliente=None, busca: str = "") -> list[dict]:
    """Um kit por linha: os que tiveram pré-check (pré-conferência registrada
    no celular) e em que pé estão.

      montagem   — pré-check feito, o kit ainda está sendo bipado;
      aguardando — o kit está pronto mas a verificação oficial ainda não foi
                   feita (com o pré-check aplicado, ignorado ou sem decisão);
      dupla      — pré-check + verificação oficial: verificado duas vezes.

    Também diz, item a item, o que foi pré-checado e o que faltou (não
    pré-checado e, no kit pronto, ainda não conferido na conferência)."""
    import json
    import app.datas as datas_mod
    sql = """
        SELECT p.sessao_id, p.item_tipo_ids, p.registrado_em, p.decisao, p.decidido_em,
               u.nome AS pre_por, ud.nome AS decidido_por_nome,
               s.status AS sessao_status, s.kit_template_id,
               t.nome AS kit_nome, t.cliente,
               COALESCE(v.numero, kr.veiculo, s.veiculo, '') AS veiculo,
               kr.kit_id, kr.finalizado_em, COALESCE(kr.verificacao_corte, 0) AS corte
        FROM pre_conferencia p
        JOIN scan_session s ON s.id = p.sessao_id
        JOIN kit_template t ON t.id = s.kit_template_id
        LEFT JOIN kit_record kr ON kr.sessao_id = p.sessao_id
        LEFT JOIN veiculos v ON v.id = COALESCE(kr.veiculo_id, s.veiculo_id)
        LEFT JOIN users u ON u.id = p.usuario_id
        LEFT JOIN users ud ON ud.id = p.decidido_por
        WHERE 1 = 1"""
    params: list = []
    sql_d, p_d = datas_mod.clausula("p.registrado_em", data_ini, data_fim)
    sql += sql_d + " ORDER BY p.registrado_em DESC"
    params += p_d
    with db() as conn:
        linhas = [dict(r) for r in conn.execute(sql, params).fetchall()]
        nomes = {r[0]: r[1] for r in conn.execute("SELECT id, nome FROM item_tipo").fetchall()}
        for l in linhas:
            modelo = [r[0] for r in conn.execute(
                "SELECT item_tipo_id FROM kit_template_items WHERE kit_template_id = ? ORDER BY id",
                (l["kit_template_id"],)).fetchall()]
            pre = set(json.loads(l["item_tipo_ids"] or "[]"))
            l["pre_itens"] = [nomes.get(i, "?") for i in modelo if i in pre]
            l["nao_pre"] = [nomes.get(i, "?") for i in modelo if i not in pre]
            l["total_modelo"] = len(modelo)
            validacao = None
            pendentes = []
            if l["kit_id"]:
                validacao = conn.execute(
                    "SELECT kv.validado_em, uv.nome AS validado_por FROM kit_validacoes kv "
                    "LEFT JOIN users uv ON uv.id = kv.validado_por "
                    "WHERE kv.kit_id = ? AND kv.id > ? ORDER BY kv.id DESC LIMIT 1",
                    (l["kit_id"], l["corte"])).fetchone()
                conferidos = {r[0] for r in conn.execute(
                    "SELECT item_tipo_id FROM kit_verificacao_itens WHERE kit_id = ?", (l["kit_id"],))}
                pendentes = [nomes.get(i, "?") for i in modelo if i not in conferidos]
            l["validacao"] = dict(validacao) if validacao else None
            # Na conferência do kit pronto: o que ainda não foi conferido.
            l["pendentes_conferencia"] = pendentes if l["kit_id"] and not validacao else []
            if not l["kit_id"]:
                l["situacao"] = "montagem"
            elif validacao:
                l["situacao"] = "dupla"
            else:
                l["situacao"] = "aguardando"
            l["situacao_texto"] = dict(SITUACOES_PRECHECK)[l["situacao"]]
            l["decisao_texto"] = {"aplicada": "Aplicado", "ignorada": "Não aplicado"}.get(
                l["decisao"] or "", "Sem decisão ainda" if l["kit_id"] else "—")

    sits = [x for x in (situacao or []) if x in dict(SITUACOES_PRECHECK)]
    if sits:
        linhas = [l for l in linhas if l["situacao"] in sits]
    clientes = [c for c in (cliente or []) if c]
    if clientes:
        linhas = [l for l in linhas if l["cliente"] in clientes]
    termo = (busca or "").strip().lower()
    if termo:
        linhas = [l for l in linhas if termo in " ".join(
            [l["veiculo"] or "", l["kit_nome"] or "", l["cliente"] or "", l["pre_por"] or ""]
            + l["pre_itens"] + l["nao_pre"]).lower()]
    return linhas
