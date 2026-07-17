from database import db, now_brt
import app.items as items_mod
import app.kit_templates as templates_mod
import app.estoque as estoque_mod


def deletar_kit_record(kit_id: str):
    """Remove um kit finalizado e todos os dados vinculados em cascade."""
    with db() as conn:
        sessao = conn.execute(
            "SELECT sessao_id FROM kit_record WHERE kit_id = ?", (kit_id,)
        ).fetchone()
        # Ordem respeita as FKs: filhos antes dos pais
        if sessao:
            conn.execute("DELETE FROM scan_session_items WHERE sessao_id = ?", (sessao[0],))
        conn.execute("DELETE FROM kit_validacoes WHERE kit_id = ?", (kit_id,))
        conn.execute("DELETE FROM print_queue WHERE kit_id = ?", (kit_id,))
        conn.execute("DELETE FROM kit_record WHERE kit_id = ?", (kit_id,))
        if sessao:
            conn.execute("DELETE FROM scan_session WHERE id = ?", (sessao[0],))


def start_session(kit_template_id: int, operador_id: int) -> int:
    template = templates_mod.buscar_template(kit_template_id)
    if not template:
        raise ValueError("Template não encontrado.")
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO scan_session (kit_template_id, kit_template_versao, operador_id, iniciado_em) "
            "VALUES (?, ?, ?, ?)",
            (kit_template_id, template["versao"], operador_id, now_brt())
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
    """Retorna {item_tipo_id: quantidade_bipada} — conta apenas itens com serial completo."""
    with db() as conn:
        rows = conn.execute(
            "SELECT item_tipo_id, COUNT(*) as qtd FROM scan_session_items "
            "WHERE sessao_id = ? AND (status IS NULL OR status = 'completo') "
            "GROUP BY item_tipo_id",
            (sessao_id,)
        ).fetchall()
    return {r["item_tipo_id"]: r["qtd"] for r in rows}


def get_pendente_serial(sessao_id: int) -> dict | None:
    """Retorna o item aguardando serial number nesta sessão, ou None."""
    with db() as conn:
        row = conn.execute(
            "SELECT ssi.id, ssi.codigo_barra, ssi.item_tipo_id, it.nome AS descricao "
            "FROM scan_session_items ssi "
            "JOIN item_tipo it ON it.id = ssi.item_tipo_id "
            "WHERE ssi.sessao_id = ? AND ssi.status = 'aguardando_serial' "
            "LIMIT 1",
            (sessao_id,)
        ).fetchone()
    return dict(row) if row else None


def registrar_serial(sessao_id: int, serial_barra: str) -> dict:
    """Registra o serial number do item pendente."""
    pendente = get_pendente_serial(sessao_id)
    if not pendente:
        return register_scan(sessao_id, serial_barra)

    if serial_barra == pendente["codigo_barra"]:
        return {"resultado": "rejeitado",
                "mensagem": "O serial não pode ser igual ao código do item. Bipe o serial number."}

    with db() as conn:
        existing = conn.execute(
            "SELECT 1 FROM scan_session_items WHERE sessao_id = ? AND serial_number = ?",
            (sessao_id, serial_barra)
        ).fetchone()
        if existing:
            return {"resultado": "rejeitado",
                    "mensagem": f"Serial '{serial_barra}' já registrado nesta sessão."}
        conn.execute(
            "UPDATE scan_session_items SET serial_number = ?, status = 'completo' WHERE id = ?",
            (serial_barra, pendente["id"])
        )

    session = get_session(sessao_id)
    itens_template = templates_mod.get_itens_template(session["kit_template_id"])
    template_item = next(
        (i for i in itens_template if i["item_tipo_id"] == pendente["item_tipo_id"]), None
    )
    exigido = template_item["quantidade_exigida"] if template_item else 1
    contagem = get_contagem(sessao_id)
    novo_atual = contagem.get(pendente["item_tipo_id"], 0)

    return {
        "resultado": "aceito",
        "mensagem": f"'{pendente['descricao']}' com serial '{serial_barra}' registrado. ({novo_atual}/{exigido})",
        "contagem_atual": novo_atual,
        "quantidade_exigida": exigido,
        "codigo_barra": pendente["codigo_barra"],
        "serial_number": serial_barra,
        "item_tipo_id": pendente["item_tipo_id"],
        "descricao": pendente["descricao"],
    }


def cancelar_serial(sessao_id: int) -> dict:
    """Descarta o item aguardando serial — operador pode bipar o item novamente."""
    with db() as conn:
        conn.execute(
            "DELETE FROM scan_session_items WHERE sessao_id = ? AND status = 'aguardando_serial'",
            (sessao_id,)
        )
    return {"resultado": "cancelado_serial",
            "mensagem": "Bipagem cancelada. Bipe o item novamente se necessário."}


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


def checar_componente(sessao_id: int, codigo_barra: str) -> dict | None:
    """Verifica se o código é um componente e retorna itens + contagem atual para o modal.
    NÃO registra nada. Retorna None se o código não é um componente."""
    session = get_session(sessao_id)
    if not session or session["status"] != "em_andamento":
        return None

    with db() as conn:
        itens = conn.execute(
            "SELECT ki.item_tipo_id, ki.quantidade_exigida, it.nome AS descricao "
            "FROM kit_template_items ki "
            "JOIN item_tipo it ON it.id = ki.item_tipo_id "
            "WHERE ki.kit_template_id = ? AND ki.componente_codigo = ?",
            (session["kit_template_id"], codigo_barra)
        ).fetchall()
        itens = [dict(r) for r in itens]

    if not itens:
        return None

    contagem = get_contagem(sessao_id)
    for item in itens:
        atual = contagem.get(item["item_tipo_id"], 0)
        item["atual"] = atual
        item["faltam"] = max(0, item["quantidade_exigida"] - atual)

    return {
        "resultado": "componente_pendente",
        "codigo_barra": codigo_barra,
        "itens": itens,
    }


def confirmar_componente(sessao_id: int, codigo_barra: str,
                         quantidades: dict) -> dict:
    """Registra as quantidades informadas pelo operador para cada item do componente."""
    session = get_session(sessao_id)
    if not session or session["status"] != "em_andamento":
        return {"resultado": "rejeitado", "mensagem": "Sessão inválida ou já encerrada."}

    with db() as conn:
        itens = conn.execute(
            "SELECT ki.item_tipo_id, ki.quantidade_exigida, it.nome AS descricao "
            "FROM kit_template_items ki "
            "JOIN item_tipo it ON it.id = ki.item_tipo_id "
            "WHERE ki.kit_template_id = ? AND ki.componente_codigo = ?",
            (session["kit_template_id"], codigo_barra)
        ).fetchall()
        itens = [dict(r) for r in itens]

    if not itens:
        return {"resultado": "rejeitado", "mensagem": "Componente não encontrado no template."}

    contagem = get_contagem(sessao_id)
    atualizacoes = []

    with db() as conn:
        for item in itens:
            tipo_id = item["item_tipo_id"]
            exigido = item["quantidade_exigida"]
            atual = contagem.get(tipo_id, 0)
            qtd_informada = int(quantidades.get(str(tipo_id), 0))
            adicionar = min(qtd_informada, max(0, exigido - atual))
            for seq in range(adicionar):
                conn.execute(
                    "INSERT INTO scan_session_items (sessao_id, codigo_barra, item_tipo_id, status, bipado_em) "
                    "VALUES (?, ?, ?, 'completo', ?)",
                    (sessao_id, f"COMP:{codigo_barra}:{tipo_id}:{atual + seq}", tipo_id, now_brt())
                )
            atualizacoes.append({
                "item_tipo_id": tipo_id,
                "descricao": item["descricao"],
                "contagem_atual": atual + adicionar,
                "quantidade_exigida": exigido,
                "adicionados": adicionar,
            })

    adicionados = [u for u in atualizacoes if u["adicionados"] > 0]
    if not adicionados:
        return {"resultado": "rejeitado",
                "mensagem": "Nenhum item adicionado (quantidades já atingidas ou zeradas)."}

    nomes = " + ".join(f"{u['descricao']} ×{u['adicionados']}" for u in adicionados)
    return {
        "resultado": "componente",
        "mensagem": f"📦 Componente '{codigo_barra}': {nomes}",
        "codigo_barra": codigo_barra,
        "atualizacoes": atualizacoes,
    }


def register_scan(sessao_id: int, codigo_barra: str,
                  item_tipo_id: int | None = None) -> dict:
    session = get_session(sessao_id)
    if not session or session["status"] != "em_andamento":
        return {"resultado": "rejeitado",
                "mensagem": "Sessão inválida ou já encerrada."}

    # ── Verifica se é um item de estoque ────────────────────────────────────────
    est = estoque_mod.buscar_por_codigo(codigo_barra)
    if est:
        itens_template = templates_mod.get_itens_template(session["kit_template_id"])
        template_item = next(
            (i for i in itens_template if i["item_tipo_id"] == est["item_tipo_id"]), None
        )
        if not template_item:
            return {"resultado": "rejeitado",
                    "mensagem": f"'{est['tipo_nome']}' não pertence a este kit."}

        # Impede registrar o mesmo estoque duas vezes na mesma sessão
        with db() as conn:
            ja_registrado = conn.execute(
                "SELECT 1 FROM estoque_movimentos "
                "WHERE sessao_id = ? AND estoque_id = ? AND tipo = 'saida'",
                (sessao_id, est["id"])
            ).fetchone()
        if ja_registrado:
            return {"resultado": "rejeitado",
                    "mensagem": f"'{est['tipo_nome']}': estoque já registrado nesta sessão."}

        qtd = template_item["quantidade_exigida"]
        if est["quantidade_atual"] < qtd:
            return {"resultado": "rejeitado",
                    "mensagem": (f"'{est['tipo_nome']}': estoque insuficiente "
                                 f"({est['quantidade_atual']} disponíveis, {qtd} necessários).")}

        with db() as conn:
            for seq in range(qtd):
                conn.execute(
                    "INSERT INTO scan_session_items "
                    "(sessao_id, codigo_barra, item_tipo_id, status, bipado_em) VALUES (?, ?, ?, 'completo', ?)",
                    (sessao_id, f"ESTOQUE:{codigo_barra}:{seq}", est["item_tipo_id"], now_brt())
                )
            conn.execute(
                "UPDATE estoque SET quantidade_atual = quantidade_atual - ? WHERE id = ?",
                (qtd, est["id"])
            )
            conn.execute(
                "INSERT INTO estoque_movimentos "
                "(estoque_id, tipo, quantidade, sessao_id, criado_por, observacao) "
                "VALUES (?, 'saida', ?, ?, ?, 'Kit')",
                (est["id"], qtd, sessao_id, session["operador_id"])
            )

        novo_qtd = est["quantidade_atual"] - qtd
        alerta = (f" ⚠️ Estoque baixo ({novo_qtd} restantes)"
                  if novo_qtd <= est["quantidade_minima"] else "")
        return {
            "resultado": "aceito",
            "mensagem": f"📦 {est['tipo_nome']}: {qtd} unidades do estoque.{alerta}",
            "contagem_atual": qtd,
            "quantidade_exigida": qtd,
            "item_tipo_id": est["item_tipo_id"],
            "descricao": est["tipo_nome"],
        }
    # ────────────────────────────────────────────────────────────────────────────

    item = items_mod.buscar_item(codigo_barra)
    item_recem_criado = False

    if not item:
        if item_tipo_id is None:
            tipos = items_mod.listar_tipos_para_kit(session["kit_template_id"])
            return {
                "resultado": "desconhecido",
                "mensagem": f"Código '{codigo_barra}' não cadastrado.",
                "codigo_barra": codigo_barra,
                "tipos": tipos,
            }
        itens_template = templates_mod.get_itens_template(session["kit_template_id"])
        if not any(i["item_tipo_id"] == item_tipo_id for i in itens_template):
            return {"resultado": "rejeitado",
                    "mensagem": "Tipo selecionado não pertence a este kit."}
        items_mod.criar_item(codigo_barra, item_tipo_id, session["operador_id"])
        item = items_mod.buscar_item(codigo_barra)
        item_recem_criado = True

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

    if not item_recem_criado and _barcode_em_kit_ativo(codigo_barra):
        return {"resultado": "rejeitado",
                "mensagem": f"Patrimônio '{codigo_barra}' já está em outro kit ativo."}

    requer_serial = bool(template_item.get("requer_serial"))

    with db() as conn:
        conn.execute(
            "INSERT INTO scan_session_items (sessao_id, codigo_barra, item_tipo_id, status, bipado_em) "
            "VALUES (?, ?, ?, ?, ?)",
            (sessao_id, codigo_barra, item["item_tipo_id"],
             "aguardando_serial" if requer_serial else "completo", now_brt())
        )

    if requer_serial:
        return {
            "resultado": "aguardando_serial",
            "mensagem": f"'{item['descricao']}' bipado. Agora bipe o serial number.",
            "codigo_barra": codigo_barra,
            "item_tipo_id": item["item_tipo_id"],
            "descricao": item["descricao"],
        }

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
    estoque_mod.reverter_saidas_sessao(sessao_id)
    with db() as conn:
        conn.execute(
            "UPDATE scan_session SET status = 'cancelado', "
            "finalizado_em = ? WHERE id = ?",
            (now_brt(), sessao_id)
        )
