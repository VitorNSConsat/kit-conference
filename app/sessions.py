from database import db
import app.items as items_mod
import app.kit_templates as templates_mod


def deletar_kit_record(kit_id: str):
    """Remove um kit finalizado e todos os dados vinculados em cascade."""
    with db() as conn:
        sessao = conn.execute(
            "SELECT sessao_id FROM kit_record WHERE kit_id = ?", (kit_id,)
        ).fetchone()
        if sessao:
            conn.execute("DELETE FROM scan_session_items WHERE sessao_id = ?", (sessao[0],))
            conn.execute("DELETE FROM scan_session WHERE id = ?", (sessao[0],))
        conn.execute("DELETE FROM print_queue WHERE kit_id = ?", (kit_id,))
        conn.execute("DELETE FROM kit_record WHERE kit_id = ?", (kit_id,))


def start_session(kit_template_id: int, operador_id: int) -> int:
    template = templates_mod.buscar_template(kit_template_id)
    if not template:
        raise ValueError("Template não encontrado.")
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO scan_session (kit_template_id, kit_template_versao, operador_id) "
            "VALUES (?, ?, ?)",
            (kit_template_id, template["versao"], operador_id)
        )
        sessao_id = cur.lastrowid
    return sessao_id


def get_session(sessao_id: int) -> dict | None:
    with db() as conn:
        row = conn.execute(
            "SELECT s.*, t.nome AS kit_nome, t.cliente, u.nome AS operador_nome "
            "FROM scan_session s "
            "JOIN kit_template t ON t.id = s.kit_template_id "
            "JOIN users u ON u.id = s.operador_id "
            "WHERE s.id = ?",
            (sessao_id,)
        ).fetchone()
    return dict(row) if row else None


def get_contagem(sessao_id: int) -> dict:
    """Retorna mapa {item_tipo_id: quantidade_bipada}."""
    with db() as conn:
        rows = conn.execute(
            "SELECT item_tipo_id, COUNT(*) as qtd FROM scan_session_items "
            "WHERE sessao_id = ? GROUP BY item_tipo_id",
            (sessao_id,)
        ).fetchall()
    return {r["item_tipo_id"]: r["qtd"] for r in rows}


def _barcode_em_sessao(sessao_id: int, codigo_barra: str) -> bool:
    with db() as conn:
        row = conn.execute(
            "SELECT 1 FROM scan_session_items WHERE sessao_id = ? AND codigo_barra = ?",
            (sessao_id, codigo_barra)
        ).fetchone()
    return row is not None


def _barcode_em_kit_ativo(codigo_barra: str) -> bool:
    """Retorna True se o patrimônio já está em um kit finalizado e ativo."""
    with db() as conn:
        row = conn.execute(
            "SELECT 1 FROM scan_session_items si "
            "JOIN scan_session s ON s.id = si.sessao_id "
            "JOIN kit_record kr ON kr.sessao_id = s.id "
            "WHERE si.codigo_barra = ? AND kr.status = 'ativo'",
            (codigo_barra,)
        ).fetchone()
    return row is not None


def register_scan(sessao_id: int, codigo_barra: str,
                  item_tipo_id: int | None = None) -> dict:
    session = get_session(sessao_id)
    if not session or session["status"] != "em_andamento":
        return {"resultado": "rejeitado",
                "mensagem": "Sessão inválida ou já encerrada."}

    item = items_mod.buscar_item(codigo_barra)

    if not item:
        if item_tipo_id is None:
            # Patrimônio desconhecido — pede identificação ao operador
            tipos = items_mod.listar_tipos_para_kit(session["kit_template_id"])
            return {
                "resultado": "desconhecido",
                "mensagem": f"Código '{codigo_barra}' não cadastrado.",
                "codigo_barra": codigo_barra,
                "tipos": tipos,
            }
        # Operador identificou o tipo — valida antes de criar
        itens_template = templates_mod.get_itens_template(session["kit_template_id"])
        if not any(i["item_tipo_id"] == item_tipo_id for i in itens_template):
            return {"resultado": "rejeitado",
                    "mensagem": "Tipo selecionado não pertence a este kit."}
        items_mod.criar_item(codigo_barra, item_tipo_id, session["operador_id"])
        item = items_mod.buscar_item(codigo_barra)

    # Patrimônio existe — verifica se pertence ao kit
    itens_template = templates_mod.get_itens_template(session["kit_template_id"])
    template_item = next(
        (i for i in itens_template if i["item_tipo_id"] == item["item_tipo_id"]), None
    )
    if not template_item:
        return {"resultado": "rejeitado",
                "mensagem": f"'{item['descricao']}' não pertence a este kit."}

    contagem = get_contagem(sessao_id)
    atual = contagem.get(item["item_tipo_id"], 0)
    exigido = template_item["quantidade_exigida"]

    if atual >= exigido:
        return {"resultado": "rejeitado",
                "mensagem": f"'{item['descricao']}': quantidade máxima ({exigido}) já atingida."}

    if _barcode_em_sessao(sessao_id, codigo_barra):
        return {"resultado": "rejeitado",
                "mensagem": f"Patrimônio '{codigo_barra}' já foi bipado nesta sessão."}

    if _barcode_em_kit_ativo(codigo_barra):
        return {"resultado": "rejeitado",
                "mensagem": f"Patrimônio '{codigo_barra}' já está em outro kit ativo."}

    with db() as conn:
        conn.execute(
            "INSERT INTO scan_session_items (sessao_id, codigo_barra, item_tipo_id) "
            "VALUES (?, ?, ?)",
            (sessao_id, codigo_barra, item["item_tipo_id"])
        )

    novo_atual = atual + 1
    return {
        "resultado": "aceito",
        "mensagem": f"'{item['descricao']}' aceito. ({novo_atual}/{exigido})",
        "contagem_atual": novo_atual,
        "quantidade_exigida": exigido,
        "codigo_barra": codigo_barra,
        "item_tipo_id": item["item_tipo_id"],
        "descricao": item["descricao"],
    }


def validate_kit_complete(sessao_id: int) -> dict:
    session = get_session(sessao_id)
    if not session:
        raise ValueError(f"Sessão {sessao_id} não encontrada.")
    itens_template = templates_mod.get_itens_template(session["kit_template_id"])
    contagem = get_contagem(sessao_id)
    faltantes = []
    for item in itens_template:
        if not item["obrigatorio"]:
            continue
        atual = contagem.get(item["item_tipo_id"], 0)
        if atual < item["quantidade_exigida"]:
            faltantes.append({
                "item_tipo_id": item["item_tipo_id"],
                "descricao": item["descricao"],
                "bipado": atual,
                "exigido": item["quantidade_exigida"],
                "faltam": item["quantidade_exigida"] - atual,
            })
    return {
        "status": "completo" if not faltantes else "incompleto",
        "itens_faltantes": faltantes,
    }


def cancel_session(sessao_id: int):
    with db() as conn:
        conn.execute(
            "UPDATE scan_session SET status = 'cancelado', "
            "finalizado_em = CURRENT_TIMESTAMP WHERE id = ?",
            (sessao_id,)
        )
