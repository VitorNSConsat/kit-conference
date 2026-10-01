"""Relatório de mudanças de patrimônio — tudo que aconteceu com uma peça DEPOIS
da montagem do kit: saiu de um veículo e entrou em outro, foi retirada, chegou
como reposição ou teve o código/serial corrigido.

Não há tabela própria de "mudanças": cada ação já deixa o rastro nas linhas de
bipagem (scan_session_items), e é dali que o relatório é montado. Por isso ele
traz também as mudanças feitas antes de o relatório existir.

  • MOVIDO   — a linha de origem vira status 'movido' e uma linha nova entra no
               kit de destino (pos_montagem=1, "Veio do veículo X em ... — motivo").
               As duas viram UM evento: saiu de A, entrou em B.
  • RETIRADO — a linha vira 'retirado' ("Retirado do kit em ... — motivo").
  • REPOSIÇÃO— linha nova num kit já fechado ("Atribuído manualmente em ...").
  • CORREÇÃO — a coluna `correcao` da linha ("Código corrigido de X para Y em ...").
"""
import re
from urllib.parse import unquote

from database import db
import app.datas as datas_mod

TIPOS = {
    "movido": "Movido entre veículos",
    "retirado": "Retirado do kit",
    "reposicao": "Reposição (entrou no kit)",
    "correcao": "Correção de código/serial",
}

ESTAGIO_TEXTO = {
    "produzido": "Galpão — Produzido",
    "transito": "Em trânsito",
    "cliente_instalando": "Cliente",
    "cliente_concluido": "Cliente",
}

_RE_DATA = re.compile(r" em (\d{4}-\d{2}-\d{2} \d{2}:\d{2}(?::\d{2})?)")
_RE_DESTINO = re.compile(r"Movido para o veículo (.+?) em \d{4}-")


def _data_do_texto(texto: str) -> str:
    m = _RE_DATA.search(texto or "")
    return m.group(1) if m else ""


def _motivo_do_texto(texto: str) -> str:
    """O motivo vem depois do " — " que as funções de patrimônio gravam."""
    texto = texto or ""
    return texto.split(" — ", 1)[1].strip() if " — " in texto else ""


_SQL_LINHAS = """
    SELECT si.id, si.codigo_barra, si.serial_number, si.bipado_em, si.status,
           si.observacao, si.correcao, COALESCE(si.pos_montagem, 0) AS pos_montagem,
           it.nome AS tipo, kr.finalizado_em, kr.kit_id, kt.cliente,
           COALESCE(v.numero, kr.veiculo, '') AS veiculo,
           op.nome AS operador_nome, cp.nome AS corrigido_por_nome
    FROM scan_session_items si
    JOIN item_tipo it ON it.id = si.item_tipo_id
    JOIN kit_record kr ON kr.sessao_id = si.sessao_id
    JOIN kit_template kt ON kt.id = kr.kit_template_id
    LEFT JOIN veiculos v ON v.id = kr.veiculo_id
    LEFT JOIN users op ON op.id = si.operador_id
    LEFT JOIN users cp ON cp.id = si.corrigido_por
    WHERE si.status IN ('movido', 'retirado')
       OR COALESCE(si.pos_montagem, 0) = 1
       OR si.correcao IS NOT NULL
       OR si.bipado_em > kr.finalizado_em
    ORDER BY si.id
"""


def _onde_esta_agora(conn, codigos: set) -> dict:
    """{codigo: texto} — o kit onde cada peça está hoje (linha que ainda vale)."""
    if not codigos:
        return {}
    agora = {}
    lista = sorted(codigos)
    for i in range(0, len(lista), 500):
        parte = lista[i:i + 500]
        marcas = ",".join("?" * len(parte))
        for r in conn.execute(f"""
            SELECT si.codigo_barra, kr.status_producao, kt.cliente,
                   COALESCE(v.numero, kr.veiculo, ss.veiculo, '') AS veiculo
            FROM scan_session_items si
            JOIN scan_session ss ON ss.id = si.sessao_id
            JOIN kit_template kt ON kt.id = ss.kit_template_id
            LEFT JOIN kit_record kr ON kr.sessao_id = ss.id
            LEFT JOIN veiculos v ON v.id = kr.veiculo_id
            WHERE si.codigo_barra IN ({marcas})
              AND (si.status IS NULL OR si.status NOT IN ('movido', 'retirado'))
            ORDER BY si.bipado_em, si.id
        """, parte).fetchall():
            etapa = ESTAGIO_TEXTO.get(r["status_producao"] or "",
                                      "Em produção" if not r["status_producao"] else "Galpão")
            if etapa == "Cliente" and r["cliente"]:
                etapa = f"Cliente — {r['cliente']}"
            agora[r["codigo_barra"]] = {"veiculo": r["veiculo"] or "—", "etapa": etapa}
    return agora


def _quem_retirou(conn) -> dict:
    """{codigo: [(quando, quem)]} — a retirada não grava o usuário na linha,
    mas a auditoria guarda o POST com quem fez."""
    quem: dict = {}
    for r in conn.execute(
            "SELECT caminho, user_nome, criado_em FROM auditoria "
            "WHERE caminho LIKE '/admin/items/patrimonio/%/retirar'").fetchall():
        codigo = unquote(r["caminho"][len("/admin/items/patrimonio/"):-len("/retirar")])
        quem.setdefault(codigo, []).append((r["criado_em"] or "", r["user_nome"] or ""))
    return quem


def _mais_proximo(candidatos: list, quando: str) -> str:
    """Quem fez a retirada: o registro de auditoria do mesmo minuto."""
    for criado_em, nome in candidatos:
        if criado_em[:16] == (quando or "")[:16]:
            return nome
    return ""


def listar_mudancas(data_ini: str = "", data_fim: str = "", tipos=None,
                    busca: str = "", cliente=None) -> list[dict]:
    """Uma linha por mudança, da mais recente pra mais antiga."""
    with db() as conn:
        linhas = [dict(r) for r in conn.execute(_SQL_LINHAS).fetchall()]
        retiradas = _quem_retirou(conn)
        eventos = []
        usadas = set()
        por_codigo: dict = {}
        for l in linhas:
            por_codigo.setdefault(l["codigo_barra"], []).append(l)

        def entrada(l):
            return l["pos_montagem"] or (l["bipado_em"] and l["finalizado_em"]
                                         and l["bipado_em"] > l["finalizado_em"])

        for l in linhas:
            base = {"patrimonio": l["codigo_barra"], "item": l["tipo"],
                    "serial": l["serial_number"] or ""}
            if l["status"] == "movido":
                # A entrada correspondente: a próxima linha da MESMA peça que
                # chegou depois da montagem dizendo de onde veio.
                destino = next((d for d in por_codigo[l["codigo_barra"]]
                                if d["id"] > l["id"] and d["id"] not in usadas and entrada(d)
                                and (d["observacao"] or "").startswith("Veio do veículo")), None)
                if destino:
                    usadas.add(destino["id"])
                    eventos.append({**base, "tipo": "movido",
                                    "data": destino["bipado_em"] or _data_do_texto(l["observacao"]),
                                    "saiu_de": l["veiculo"] or "—", "entrou_em": destino["veiculo"] or "—",
                                    "motivo": _motivo_do_texto(destino["observacao"]) or _motivo_do_texto(l["observacao"]),
                                    "por": destino["operador_nome"] or "",
                                    "cliente": destino["cliente"] or l["cliente"] or ""})
                else:
                    m = _RE_DESTINO.search(l["observacao"] or "")
                    eventos.append({**base, "tipo": "movido", "data": _data_do_texto(l["observacao"]),
                                    "saiu_de": l["veiculo"] or "—", "entrou_em": m.group(1) if m else "—",
                                    "motivo": _motivo_do_texto(l["observacao"]), "por": "",
                                    "cliente": l["cliente"] or ""})
            elif l["status"] == "retirado":
                quando = _data_do_texto(l["observacao"])
                eventos.append({**base, "tipo": "retirado", "data": quando,
                                "saiu_de": l["veiculo"] or "—", "entrou_em": "",
                                "motivo": _motivo_do_texto(l["observacao"]),
                                "por": _mais_proximo(retiradas.get(l["codigo_barra"], []), quando),
                                "cliente": l["cliente"] or ""})
            if l["correcao"]:
                eventos.append({**base, "tipo": "correcao", "data": _data_do_texto(l["correcao"]),
                                "saiu_de": "", "entrou_em": "", "veiculo": l["veiculo"] or "—",
                                "motivo": _motivo_do_texto(l["correcao"]),
                                "detalhe": l["correcao"].split(" em ")[0],
                                "por": l["corrigido_por_nome"] or "", "cliente": l["cliente"] or ""})
        # O que entrou depois da montagem e não é o outro lado de um "movido"
        # é reposição: peça nova (ou do estoque) posta num kit já fechado.
        for l in linhas:
            if l["id"] in usadas or l["status"] in ("movido", "retirado") or not entrada(l):
                continue
            if (l["observacao"] or "").startswith("Veio do veículo"):
                continue
            eventos.append({"patrimonio": l["codigo_barra"], "item": l["tipo"],
                            "serial": l["serial_number"] or "", "tipo": "reposicao",
                            "data": l["bipado_em"] or "", "saiu_de": "",
                            "entrou_em": l["veiculo"] or "—",
                            "motivo": _motivo_do_texto(l["observacao"]),
                            "por": l["operador_nome"] or "", "cliente": l["cliente"] or ""})
        agora = _onde_esta_agora(conn, {e["patrimonio"] for e in eventos})

    for e in eventos:
        e["tipo_texto"] = TIPOS[e["tipo"]]
        e.setdefault("detalhe", "")
        e.setdefault("veiculo", "")
        a = agora.get(e["patrimonio"])
        e["agora_veiculo"] = a["veiculo"] if a else ""
        e["agora_etapa"] = a["etapa"] if a else ""
        e["agora_texto"] = (f"{a['veiculo']} · {a['etapa']}" if a
                            else "Fora de kit (retirado)")

    # Filtros — sobre o evento já montado, pra o Excel e a tela baterem.
    ini, fim = datas_mod.intervalo(data_ini, data_fim)
    if ini:
        eventos = [e for e in eventos if e["data"] and e["data"] >= ini]
    if fim:
        eventos = [e for e in eventos if e["data"] and e["data"] < fim]
    tipos = [t for t in (tipos or []) if t in TIPOS]
    if tipos:
        eventos = [e for e in eventos if e["tipo"] in tipos]
    clientes = [c for c in (cliente or []) if c]
    if clientes:
        eventos = [e for e in eventos if e["cliente"] in clientes]
    termo = (busca or "").strip().lower()
    if termo:
        campos = ("patrimonio", "item", "serial", "saiu_de", "entrou_em", "veiculo",
                  "motivo", "por", "agora_texto", "detalhe")
        eventos = [e for e in eventos
                   if any(termo in (e.get(c) or "").lower() for c in campos)]
    eventos.sort(key=lambda e: e["data"] or "", reverse=True)
    return eventos
