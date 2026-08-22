from database import db, now_brt
import app.datas as datas_mod


def registrar(kit_id: str, user_id: int, observacao: str) -> int:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO kit_validacoes (kit_id, validado_por, validado_em, observacao) "
            "VALUES (?, ?, ?, ?)",
            (kit_id, user_id, now_brt(), observacao or None)
        )
        return cur.lastrowid


def listar_por_kit(kit_id: str) -> list:
    with db() as conn:
        rows = conn.execute(
            "SELECT kv.*, u.nome AS user_nome "
            "FROM kit_validacoes kv "
            "JOIN users u ON u.id = kv.validado_por "
            "WHERE kv.kit_id = ? "
            "ORDER BY kv.validado_em DESC, kv.id DESC",
            (kit_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def listar_relatorio(data_ini: str = "", data_fim: str = "", user_id: str = "") -> list:
    query = """
        SELECT kv.id, kv.kit_id, kv.validado_em, kv.observacao,
               uv.nome AS validado_por_nome,
               kr.finalizado_em, kr.veiculo, kr.garagem,
               kt.nome AS kit_nome, kt.cliente,
               uo.nome AS operador_nome,
               (
                   SELECT GROUP_CONCAT(sub.r, ' | ')
                   FROM (
                       SELECT it.nome || ' x' || COUNT(*) AS r
                       FROM scan_session_items si
                       JOIN item_tipo it ON it.id = si.item_tipo_id
                       WHERE si.sessao_id = kr.sessao_id
                       GROUP BY si.item_tipo_id
                   ) sub
               ) AS itens_resumo
        FROM kit_validacoes kv
        JOIN kit_record kr ON kr.kit_id = kv.kit_id
        JOIN kit_template kt ON kt.id = kr.kit_template_id
        JOIN users uv ON uv.id = kv.validado_por
        JOIN users uo ON uo.id = kr.operador_id
        WHERE 1=1
    """
    params = []
    sql_data, p_data = datas_mod.clausula("kv.validado_em", data_ini, data_fim)
    query += sql_data
    params += p_data
    if user_id and str(user_id).isdigit():
        query += " AND kv.validado_por = ?"
        params.append(int(user_id))
    # Sem LIMIT. O que existia aqui (LIMIT 500) cortava em silêncio: um
    # período com 600 verificações devolvia 500 e a tela não tinha como
    # avisar nem paginar — as outras 100 simplesmente não existiam pra quem
    # olhava. Quem limita o volume é o período escolhido; a tela pagina o
    # resultado, então o tamanho não pesa na renderização.
    query += " ORDER BY kv.validado_em DESC, kv.id DESC"
    with db() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def tipos_do_kit(sessao_id: int, kit_template_id: int | None = None) -> set:
    """item_tipo_ids que a conferência deve cobrar.

    Base: o que está de fato no kit (bipado). Passando `kit_template_id`, o
    que o template NÃO pede mais fica de fora — kit fechado antes de uma
    edição do modelo não deve travar a verificação por causa de um item que
    hoje não faz parte dele. A peça continua na caixa e continua listada na
    tela; só não é mais exigida no checklist."""
    with db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT item_tipo_id FROM scan_session_items WHERE sessao_id = ?",
            (sessao_id,)
        ).fetchall()
        tipos = {r["item_tipo_id"] for r in rows}
        if kit_template_id is None:
            return tipos
        do_template = {r["item_tipo_id"] for r in conn.execute(
            "SELECT item_tipo_id FROM kit_template_items WHERE kit_template_id = ?",
            (kit_template_id,)).fetchall()}
    return tipos & do_template


def listar_conferidos(kit_id: str) -> set:
    with db() as conn:
        rows = conn.execute(
            "SELECT item_tipo_id FROM kit_verificacao_itens WHERE kit_id = ?", (kit_id,)
        ).fetchall()
    return {r["item_tipo_id"] for r in rows}


def _verifica_em_conjunto(kit_template_id: int, componente_codigo: str) -> bool:
    """Se esse conjunto (template + código) foi marcado como exceção
    (verificação manual, item a item) na aba Conjuntos. Sem linha
    configurada = comportamento padrão, verifica em conjunto."""
    with db() as conn:
        row = conn.execute(
            "SELECT verifica_em_conjunto FROM kit_template_conjuntos "
            "WHERE kit_template_id = ? AND componente_codigo = ?",
            (kit_template_id, componente_codigo)
        ).fetchone()
    return bool(row["verifica_em_conjunto"]) if row else True


def _grupo_conjunto(kit_template_id: int, sessao_id: int, item_tipo_id: int) -> list:
    """item_tipo_ids do mesmo conjunto que item_tipo_id (mesmo
    componente_codigo no template), restrito ao que de fato está presente
    no kit — inclui o próprio item_tipo_id quando não é conjunto, ou quando
    o conjunto está marcado pra verificação manual (exceção). Um clique num
    item do conjunto conta como conferir o conjunto inteiro, igual à
    bipagem (confirmar_componente já trata o conjunto como uma unidade só)
    — a menos que a exceção esteja ligada."""
    presentes = tipos_do_kit(sessao_id)
    with db() as conn:
        codigo = conn.execute(
            "SELECT componente_codigo FROM kit_template_items "
            "WHERE kit_template_id = ? AND item_tipo_id = ? LIMIT 1",
            (kit_template_id, item_tipo_id)
        ).fetchone()
        if not codigo or not codigo["componente_codigo"]:
            return [item_tipo_id] if item_tipo_id in presentes else []
        if not _verifica_em_conjunto(kit_template_id, codigo["componente_codigo"]):
            return [item_tipo_id] if item_tipo_id in presentes else []
        rows = conn.execute(
            "SELECT DISTINCT item_tipo_id FROM kit_template_items "
            "WHERE kit_template_id = ? AND componente_codigo = ?",
            (kit_template_id, codigo["componente_codigo"])
        ).fetchall()
    return [r["item_tipo_id"] for r in rows if r["item_tipo_id"] in presentes]


def grupos_conjunto(kit_template_id: int, sessao_id: int) -> dict:
    """Mapa item_tipo_id -> nomes dos OUTROS tipos do mesmo conjunto (só pra
    tipos que de fato têm parceiros presentes no kit E cujo conjunto não
    está marcado pra verificação manual — usado pra exibir a dica visual
    'confere junto com X, Y' no checklist)."""
    presentes = tipos_do_kit(sessao_id)
    with db() as conn:
        rows = conn.execute(
            "SELECT kti.item_tipo_id, kti.componente_codigo, it.nome "
            "FROM kit_template_items kti "
            "JOIN item_tipo it ON it.id = kti.item_tipo_id "
            "WHERE kti.kit_template_id = ? AND kti.componente_codigo IS NOT NULL",
            (kit_template_id,)
        ).fetchall()
    por_codigo: dict = {}
    for r in rows:
        if r["item_tipo_id"] in presentes:
            por_codigo.setdefault(r["componente_codigo"], []).append((r["item_tipo_id"], r["nome"]))
    mapa: dict = {}
    for codigo, membros in por_codigo.items():
        if len(membros) < 2 or not _verifica_em_conjunto(kit_template_id, codigo):
            continue
        for tid, _nome in membros:
            mapa[tid] = [nome for outro_id, nome in membros if outro_id != tid]
    return mapa


def conferir_item(kit_id: str, kit_template_id: int, sessao_id: int,
                  item_tipo_id: int, user_id: int) -> list:
    """Marca item_tipo_id (e os demais do mesmo conjunto, se houver e se o
    conjunto não estiver marcado pra verificação manual) como conferidos
    neste kit. Retorna os item_tipo_ids afetados."""
    grupo = _grupo_conjunto(kit_template_id, sessao_id, item_tipo_id)
    with db() as conn:
        for tid in grupo:
            conn.execute(
                "INSERT OR IGNORE INTO kit_verificacao_itens "
                "(kit_id, item_tipo_id, conferido_por, conferido_em) VALUES (?, ?, ?, ?)",
                (kit_id, tid, user_id, now_brt())
            )
    return grupo


def desfazer_item(kit_id: str, kit_template_id: int, sessao_id: int, item_tipo_id: int) -> list:
    """Desfaz a conferência de item_tipo_id (e do conjunto inteiro junto,
    quando aplicável)."""
    grupo = _grupo_conjunto(kit_template_id, sessao_id, item_tipo_id)
    with db() as conn:
        for tid in grupo:
            conn.execute(
                "DELETE FROM kit_verificacao_itens WHERE kit_id = ? AND item_tipo_id = ?",
                (kit_id, tid)
            )
    return grupo


def listar_relatorio_agrupado(data_ini: str = "", data_fim: str = "", user_id: str = "") -> list:
    """Mesmo relatório de listar_relatorio(), mas agrupado por kit — cada
    kit aparece uma vez só, com a lista de verificações (1ª, 2ª, 3ª...) em
    ordem cronológica dentro dele, em vez de uma linha duplicada por
    verificação (usado na exportação em Excel, pra não repetir veículo/
    cliente/itens uma vez para cada verificação do mesmo kit)."""
    linhas = listar_relatorio(data_ini, data_fim, user_id)  # já vem DESC por validado_em
    agrupado = {}
    ordem = []
    for r in linhas:
        kid = r["kit_id"]
        if kid not in agrupado:
            agrupado[kid] = {
                "kit_id": kid, "kit_nome": r["kit_nome"], "cliente": r["cliente"],
                "veiculo": r.get("veiculo"), "garagem": r.get("garagem"),
                "operador_nome": r["operador_nome"], "finalizado_em": r.get("finalizado_em"),
                "itens_resumo": r.get("itens_resumo"),
                "verificacoes": [],
            }
            ordem.append(kid)
        agrupado[kid]["verificacoes"].append({
            "validado_por_nome": r["validado_por_nome"],
            "validado_em": r["validado_em"],
            "observacao": r.get("observacao"),
        })
    resultado = []
    for kid in ordem:
        grupo = agrupado[kid]
        grupo["verificacoes"].reverse()  # mais antiga primeiro = "Verificação 1"
        resultado.append(grupo)
    return resultado
