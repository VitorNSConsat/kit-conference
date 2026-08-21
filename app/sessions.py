from database import db, now_brt
import app.items as items_mod
import app.kit_templates as templates_mod
import app.estoque as estoque_mod
import app.codigos_gerados as codigos_gerados_mod
import app.datas as datas_mod


def _descontar_estoque_por_patrimonio_novo(item_tipo_id: int, sessao_id: int, criado_por: int) -> None:
    """Se o tipo tiver estoque vinculado, desconta 1 unidade — equivale a
    retirar uma unidade física da caixa de estoque ao criar um patrimônio
    novo. Só é chamado na criação (primeira vez que o código é visto);
    reutilizar o mesmo patrimônio depois não desconta de novo. Permite
    ficar negativo — sinaliza contagem de estoque desatualizada em vez de
    travar o operador em campo."""
    est = estoque_mod.buscar_por_tipo(item_tipo_id)
    if est:
        estoque_mod.registrar_saida(est["id"], 1, sessao_id, criado_por)


def _eh_quantidade_lote(template_item: dict | None) -> bool:
    """True quando um único código de patrimônio representa uma quantidade
    em lote (metro ou mais de 1 unidade) em vez de uma peça só — é o
    critério que dispara o fluxo de 'quantos você está adicionando?'
    (quantidade_pendente/confirmar_quantidade). Esses itens não descontam
    estoque na criação do patrimônio (não dá pra saber quanto ainda);
    o desconto correto acontece só quando a quantidade é confirmada."""
    if not template_item or template_item.get("componente_codigo") or template_item.get("requer_serial"):
        return False
    return template_item.get("unidade") == "m" or (template_item.get("quantidade_exigida") or 0) > 1


def _aviso_quantidade(template_item: dict | None) -> str:
    """Lembrete anexado à mensagem de aceite pra item que exige mais de 1
    unidade e não é conjunto (componente_codigo) — conjunto já tem seu
    próprio fluxo de conferência de quantidades, não precisa do aviso
    extra. Ajuda o operador a não perder a conta em itens bipados um a um."""
    if not template_item or template_item.get("componente_codigo"):
        return ""
    if (template_item.get("quantidade_exigida") or 0) <= 1:
        return ""
    return " ⚠️ Verifique a quantidade depositada na caixa."


def deletar_kit_record(kit_id: str):
    """Remove um kit finalizado e todos os dados vinculados em cascade."""
    with db() as conn:
        sessao = conn.execute(
            "SELECT sessao_id FROM kit_record WHERE kit_id = ?", (kit_id,)
        ).fetchone()
        # Ordem respeita as FKs: filhos antes dos pais
        if sessao:
            conn.execute("DELETE FROM scan_session_items WHERE sessao_id = ?", (sessao[0],))
            conn.execute("DELETE FROM estoque_movimentos WHERE sessao_id = ?", (sessao[0],))
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


def definir_destino(sessao_id: int, veiculo_id: int | None, veiculo: str,
                     garagem: str, modelo: str) -> bool:
    """Grava o destino (veículo/garagem/modelo) escolhido ANTES de começar a
    bipar. Só age em sessão em_andamento — garagem vazia é o sinal usado em
    todo o resto do fluxo pra saber que o destino ainda não foi definido,
    então essa função nunca grava garagem vazia de propósito."""
    garagem = garagem.strip().upper()
    if not garagem:
        return False
    with db() as conn:
        cur = conn.execute(
            "UPDATE scan_session SET veiculo_id = ?, veiculo = ?, garagem = ?, modelo = ? "
            "WHERE id = ? AND status = 'em_andamento'",
            (veiculo_id, veiculo.strip(), garagem, modelo.strip(), sessao_id)
        )
        return cur.rowcount > 0


def get_session(sessao_id: int) -> dict | None:
    with db() as conn:
        row = conn.execute(
            "SELECT s.*, t.nome AS kit_nome, t.cliente, t.tipo AS kit_tipo, u.nome AS operador_nome "
            "FROM scan_session s "
            "JOIN kit_template t ON t.id = s.kit_template_id "
            "JOIN users u ON u.id = s.operador_id "
            "WHERE s.id = ?",
            (sessao_id,)
        ).fetchone()
    return dict(row) if row else None


def get_contagem(sessao_id: int) -> dict:
    """Retorna {item_tipo_id: quantidade_bipada} — usa SUM(quantidade) para multi-unit."""
    with db() as conn:
        rows = conn.execute(
            "SELECT item_tipo_id, SUM(COALESCE(quantidade, 1)) as qtd FROM scan_session_items "
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


def registrar_serial(sessao_id: int, serial_barra: str, operador_id: int | None = None) -> dict:
    """Registra o serial number do item pendente."""
    pendente = get_pendente_serial(sessao_id)
    if not pendente:
        return register_scan(sessao_id, serial_barra, operador_id=operador_id)

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
    aviso = _aviso_quantidade(template_item)

    return {
        "resultado": "aceito",
        "mensagem": f"'{pendente['descricao']}' com serial '{serial_barra}' registrado. ({novo_atual}/{exigido})" + aviso,
        "quantidade_aviso": bool(aviso),
        "contagem_atual": novo_atual,
        "quantidade_exigida": exigido,
        "codigo_barra": pendente["codigo_barra"],
        "serial_number": serial_barra,
        "item_tipo_id": pendente["item_tipo_id"],
        "descricao": pendente["descricao"],
    }


def get_pendente_patrimonio_fixo(sessao_id: int) -> dict | None:
    """Retorna o tipo aguardando patrimônio após código fixo detectado, ou None."""
    with db() as conn:
        row = conn.execute(
            "SELECT ssi.id, ssi.codigo_barra AS codigo_fixo, ssi.item_tipo_id, it.nome AS descricao "
            "FROM scan_session_items ssi "
            "JOIN item_tipo it ON it.id = ssi.item_tipo_id "
            "WHERE ssi.sessao_id = ? AND ssi.status = 'aguardando_patrimonio' "
            "LIMIT 1",
            (sessao_id,)
        ).fetchone()
    return dict(row) if row else None


def cancelar_patrimonio_fixo(sessao_id: int) -> dict:
    with db() as conn:
        conn.execute(
            "DELETE FROM scan_session_items WHERE sessao_id = ? AND status = 'aguardando_patrimonio'",
            (sessao_id,)
        )
    return {"resultado": "cancelado_patrimonio_fixo",
            "mensagem": "Código da caixa cancelado. Bipe novamente se necessário."}


def registrar_patrimonio_de_fixo(sessao_id: int, codigo_patrimonio: str, operador_id: int | None = None) -> dict:
    """Registra o patrimônio do item identificado por código fixo."""
    pendente = get_pendente_patrimonio_fixo(sessao_id)
    if not pendente:
        return register_scan(sessao_id, codigo_patrimonio, operador_id=operador_id)

    session = get_session(sessao_id)
    if not session or session["status"] != "em_andamento":
        return {"resultado": "rejeitado", "mensagem": "Sessão inválida ou já encerrada."}

    tipo_id = pendente["item_tipo_id"]

    item = items_mod.buscar_item(codigo_patrimonio)
    if not item:
        items_mod.criar_item(codigo_patrimonio, tipo_id, session["operador_id"])
        item = items_mod.buscar_item(codigo_patrimonio)
        codigos_gerados_mod.sincronizar_tipo_se_reciclavel(codigo_patrimonio, tipo_id)
        _descontar_estoque_por_patrimonio_novo(tipo_id, sessao_id, session["operador_id"])
    elif item["item_tipo_id"] != tipo_id:
        return {"resultado": "rejeitado",
                "mensagem": (f"Patrimônio '{codigo_patrimonio}' já é do tipo '{item['descricao']}', "
                             f"não '{pendente['descricao']}'. Cancele e bipe novamente.")}

    with db() as conn:
        conn.execute(
            "DELETE FROM scan_session_items WHERE id = ?", (pendente["id"],)
        )

    if _barcode_em_sessao(sessao_id, codigo_patrimonio):
        return {"resultado": "rejeitado",
                "mensagem": f"Patrimônio '{codigo_patrimonio}' já foi bipado nesta sessão."}

    itens_template = templates_mod.get_itens_template(session["kit_template_id"])
    template_item = next(
        (i for i in itens_template if i["item_tipo_id"] == tipo_id), None
    )
    if not template_item:
        return {"resultado": "rejeitado",
                "mensagem": f"'{pendente['descricao']}' não pertence a este kit."}

    contagem = get_contagem(sessao_id)
    atual = contagem.get(tipo_id, 0)
    exigido = template_item["quantidade_exigida"]

    if atual >= exigido:
        return {"resultado": "rejeitado",
                "mensagem": f"'{pendente['descricao']}': quantidade máxima ({exigido}) já atingida."}

    requer_serial = bool(template_item.get("requer_serial"))

    with db() as conn:
        conn.execute(
            "INSERT INTO scan_session_items (sessao_id, codigo_barra, item_tipo_id, status, bipado_em, operador_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (sessao_id, codigo_patrimonio, tipo_id,
             "aguardando_serial" if requer_serial else "completo", now_brt(), operador_id)
        )

    if requer_serial:
        return {
            "resultado": "aguardando_serial",
            "mensagem": f"'{pendente['descricao']}' patrimônio bipado. Agora bipe o serial number.",
            "codigo_barra": codigo_patrimonio,
            "item_tipo_id": tipo_id,
            "descricao": pendente["descricao"],
        }

    novo_atual = atual + 1
    aviso = _aviso_quantidade(template_item)
    return {
        "resultado": "aceito",
        "mensagem": f"'{pendente['descricao']}' aceito. ({novo_atual}/{exigido})" + aviso,
        "quantidade_aviso": bool(aviso),
        "contagem_atual": novo_atual,
        "quantidade_exigida": exigido,
        "codigo_barra": codigo_patrimonio,
        "item_tipo_id": tipo_id,
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


def desfazer_ultimo_item(sessao_id: int) -> dict:
    """Desfaz a última bipagem completa desta sessão — para quando o operador
    bipa o código errado e não quer perder o progresso do kit inteiro.

    Remove todas as linhas de scan_session_items que compartilham o mesmo
    'bipado_em' do último item (cobre bipagens em lote de um só evento, como
    conjunto/componente ou estoque em quantidade) e reverte o desconto de
    estoque vinculado a esse mesmo instante, se houver.

    Não mexe em bipagens pendentes (aguardando serial ou patrimônio da
    caixa) — essas já têm cancelamento próprio (cancelar_serial /
    cancelar_patrimonio_fixo). Não remove um patrimônio novo do catálogo
    caso a bipagem tenha criado um — só desfaz a entrada dele neste kit."""
    session = get_session(sessao_id)
    if not session or session["status"] != "em_andamento":
        return {"resultado": "rejeitado", "mensagem": "Sessão inválida ou já encerrada."}

    with db() as conn:
        ultimo = conn.execute(
            "SELECT * FROM scan_session_items "
            "WHERE sessao_id = ? AND status = 'completo' "
            "ORDER BY id DESC LIMIT 1",
            (sessao_id,)
        ).fetchone()
    if not ultimo:
        return {"resultado": "rejeitado", "mensagem": "Nenhum item bipado para desfazer."}
    ultimo = dict(ultimo)

    with db() as conn:
        lote_rows = conn.execute(
            "SELECT * FROM scan_session_items "
            "WHERE sessao_id = ? AND status = 'completo' AND bipado_em = ?",
            (sessao_id, ultimo["bipado_em"])
        ).fetchall()
        lote_rows = [dict(r) for r in lote_rows]
        ids = [r["id"] for r in lote_rows]

        conn.execute(
            f"DELETE FROM scan_session_items WHERE id IN ({','.join('?' * len(ids))})",
            ids
        )

        movimentos = conn.execute(
            "SELECT * FROM estoque_movimentos "
            "WHERE sessao_id = ? AND tipo = 'saida' AND criado_em = ?",
            (sessao_id, ultimo["bipado_em"])
        ).fetchall()
        for m in movimentos:
            conn.execute(
                "UPDATE estoque SET quantidade_atual = quantidade_atual + ? WHERE id = ?",
                (m["quantidade"], m["estoque_id"])
            )
            conn.execute(
                "UPDATE estoque_movimentos SET tipo = 'saida_cancelada' WHERE id = ?",
                (m["id"],)
            )

    tipos_afetados = sorted({r["item_tipo_id"] for r in lote_rows})
    itens_template = templates_mod.get_itens_template(session["kit_template_id"])
    exigido_map = {i["item_tipo_id"]: i["quantidade_exigida"] for i in itens_template}
    contagem = get_contagem(sessao_id)

    with db() as conn:
        nomes = conn.execute(
            f"SELECT id, nome FROM item_tipo WHERE id IN ({','.join('?' * len(tipos_afetados))})",
            tipos_afetados
        ).fetchall()
    nomes_map = {r["id"]: r["nome"] for r in nomes}
    resumo = ", ".join(nomes_map.get(t, "?") for t in tipos_afetados)

    return {
        "resultado": "desfeito",
        "mensagem": f"↩️ Última bipagem desfeita: {resumo}",
        "atualizacoes": [
            {
                "item_tipo_id": t,
                "contagem_atual": contagem.get(t, 0),
                "quantidade_exigida": exigido_map.get(t, 0),
            }
            for t in tipos_afetados
        ],
    }


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


def _historico_kit_ativo(codigo_barra: str) -> dict | None:
    """Retorna o kit ativo mais recente que contém este código de barras, ou None."""
    with db() as conn:
        row = conn.execute(
            "SELECT kr.kit_id, kr.finalizado_em "
            "FROM scan_session_items si "
            "JOIN scan_session s ON s.id = si.sessao_id "
            "JOIN kit_record kr ON kr.sessao_id = s.id "
            "WHERE si.codigo_barra = ? AND kr.status = 'ativo' "
            "ORDER BY kr.finalizado_em DESC LIMIT 1",
            (codigo_barra,)
        ).fetchone()
    return dict(row) if row else None


def registrar_conjunto(sessao_id: int, codigo_barra: str,
                       operador_id: int | None = None) -> dict | None:
    """Bipar o código de um conjunto registra de uma vez tudo que o template
    define pra ele — sem parar pra confirmar quantidade na tela.

    O controle passou a ser todo na montagem do template: se o conjunto diz
    3 parafusos, são 3 parafusos, e o operador só ouve o beep normal como em
    qualquer outro item. Antes abria um modal pedindo confirmação item a
    item, o que na prática só atrasava a bipagem — a conferência de
    quantidade já acontece depois, na tela de verificação do kit.

    Retorna None se o código não pertence a nenhum conjunto deste template
    (aí quem chama segue o fluxo normal de bipagem)."""
    session = get_session(sessao_id)
    if not session or session["status"] != "em_andamento":
        return None

    with db() as conn:
        itens = conn.execute(
            "SELECT ki.item_tipo_id, ki.quantidade_exigida "
            "FROM kit_template_items ki "
            "WHERE ki.kit_template_id = ? AND ki.componente_codigo = ?",
            (session["kit_template_id"], codigo_barra)
        ).fetchall()
    if not itens:
        return None

    # Manda o que ainda falta de cada item — nunca ultrapassa o exigido, e
    # bipar o mesmo conjunto de novo não duplica nada.
    contagem = get_contagem(sessao_id)
    quantidades = {
        str(r["item_tipo_id"]): max(
            0, r["quantidade_exigida"] - contagem.get(r["item_tipo_id"], 0))
        for r in itens
    }
    return confirmar_componente(sessao_id, codigo_barra, quantidades, operador_id)


def checar_componente(sessao_id: int, codigo_barra: str) -> dict | None:
    """Verifica se o código é um componente e retorna itens + contagem atual para o modal.
    NÃO registra nada. Retorna None se o código não é um componente.

    Fora de uso desde que o conjunto passou a ser registrado direto
    (registrar_conjunto). Mantido porque é uma consulta sem efeito colateral
    e é o caminho de volta caso o modal precise voltar algum dia."""
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
                         quantidades: dict, operador_id: int | None = None) -> dict:
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
        return {"resultado": "rejeitado", "mensagem": "Conjunto não encontrado no template."}

    contagem = get_contagem(sessao_id)
    atualizacoes = []

    with db() as conn:
        for item in itens:
            tipo_id = item["item_tipo_id"]
            exigido = item["quantidade_exigida"]
            atual = contagem.get(tipo_id, 0)
            qtd_informada = int(quantidades.get(str(tipo_id), 0))
            adicionar = min(qtd_informada, max(0, exigido - atual))
            est = estoque_mod.buscar_por_tipo(tipo_id)
            debitado = 1 if est else 0
            for seq in range(adicionar):
                conn.execute(
                    "INSERT INTO scan_session_items (sessao_id, codigo_barra, item_tipo_id, status, bipado_em, operador_id, estoque_debitado) "
                    "VALUES (?, ?, ?, 'completo', ?, ?, ?)",
                    (sessao_id, f"COMP:{codigo_barra}:{tipo_id}:{atual + seq}", tipo_id, now_brt(), operador_id, debitado)
                )
            atualizacoes.append({
                "item_tipo_id": tipo_id,
                "descricao": item["descricao"],
                "contagem_atual": atual + adicionar,
                "quantidade_exigida": exigido,
                "adicionados": adicionar,
                "estoque_id": est["id"] if est else None,
            })

    # Desconta do estoque vinculado (quando existir) — fora da transação
    # acima de propósito: registrar_saida() abre sua própria conexão, e
    # chamá-la dentro de uma transação já aberta trava o banco (mesmo
    # motivo documentado em veiculos_mod.importar_excel). Sem checar saldo
    # antes: mesmo padrão de _descontar_estoque_por_patrimonio_novo — não
    # trava o operador no meio da montagem do kit, um estoque negativo só
    # sinaliza que a contagem física precisa ser corrigida depois.
    operador_estoque = operador_id or session["operador_id"]
    for u in atualizacoes:
        if u["adicionados"] > 0 and u["estoque_id"]:
            estoque_mod.registrar_saida(u["estoque_id"], u["adicionados"], sessao_id, operador_estoque)

    adicionados = [u for u in atualizacoes if u["adicionados"] > 0]
    if not adicionados:
        return {"resultado": "rejeitado",
                "mensagem": "Nenhum item adicionado (quantidades já atingidas ou zeradas)."}

    nomes = " + ".join(f"{u['descricao']} ×{u['adicionados']}" for u in adicionados)
    return {
        "resultado": "componente",
        "mensagem": f"📦 Conjunto '{codigo_barra}': {nomes}",
        "codigo_barra": codigo_barra,
        "atualizacoes": atualizacoes,
    }


def register_scan(sessao_id: int, codigo_barra: str,
                  item_tipo_id: int | None = None,
                  operador_id: int | None = None) -> dict:
    session = get_session(sessao_id)
    if not session or session["status"] != "em_andamento":
        return {"resultado": "rejeitado",
                "mensagem": "Sessão inválida ou já encerrada."}

    # ── Verifica se é um código fixo de tipo ────────────────────────────────────
    tipo_fixo = items_mod.buscar_tipo_por_codigo_fixo(codigo_barra)
    if tipo_fixo:
        itens_template = templates_mod.get_itens_template(session["kit_template_id"])
        template_item_fixo = next(
            (i for i in itens_template if i["item_tipo_id"] == tipo_fixo["id"]), None
        )
        if not template_item_fixo:
            return {"resultado": "rejeitado",
                    "mensagem": f"'{tipo_fixo['nome']}' não pertence a este kit."}
        contagem = get_contagem(sessao_id)
        if contagem.get(tipo_fixo["id"], 0) >= template_item_fixo["quantidade_exigida"]:
            return {"resultado": "rejeitado",
                    "mensagem": (f"'{tipo_fixo['nome']}': quantidade máxima "
                                 f"({template_item_fixo['quantidade_exigida']}) já atingida.")}
        with db() as conn:
            conn.execute(
                "INSERT INTO scan_session_items "
                "(sessao_id, codigo_barra, item_tipo_id, status, bipado_em, operador_id) "
                "VALUES (?, ?, ?, 'aguardando_patrimonio', ?, ?)",
                (sessao_id, codigo_barra, tipo_fixo["id"], now_brt(), operador_id)
            )
        return {
            "resultado": "aguardando_patrimonio_fixo",
            "mensagem": f"Código da caixa '{tipo_fixo['nome']}' detectado. Bipe o patrimônio.",
            "tipo_id": tipo_fixo["id"],
            "tipo_nome": tipo_fixo["nome"],
            "codigo_fixo": codigo_barra,
        }
    # ────────────────────────────────────────────────────────────────────────────

    # ── Verifica se é um item de estoque (código direto ou QR da etiqueta) ───────
    est = estoque_mod.buscar_por_referencia(codigo_barra)
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
                    "(sessao_id, codigo_barra, item_tipo_id, status, bipado_em, operador_id) "
                    "VALUES (?, ?, ?, 'completo', ?, ?)",
                    (sessao_id, f"ESTOQUE:{est['codigo_barra']}:{seq}", est["item_tipo_id"], now_brt(), operador_id)
                )
            conn.execute(
                "UPDATE estoque SET quantidade_atual = quantidade_atual - ? WHERE id = ?",
                (qtd, est["id"])
            )
            conn.execute(
                "INSERT INTO estoque_movimentos "
                "(estoque_id, tipo, quantidade, sessao_id, criado_por, observacao, criado_em) "
                "VALUES (?, 'saida', ?, ?, ?, 'Kit', ?)",
                (est["id"], qtd, sessao_id, session["operador_id"], now_brt())
            )

        novo_qtd = est["quantidade_atual"] - qtd
        alerta = (f" ⚠️ Estoque baixo ({novo_qtd} restantes)"
                  if novo_qtd <= est["quantidade_minima"] else "")
        aviso = _aviso_quantidade(template_item)
        return {
            "resultado": "aceito",
            "mensagem": f"📦 {est['tipo_nome']}: {qtd} unidades do estoque.{alerta}" + aviso,
            "quantidade_aviso": bool(aviso),
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
            # Exclude tipos already at capacity in this session
            contagem_modal = get_contagem(sessao_id)
            itens_tpl = templates_mod.get_itens_template(session["kit_template_id"])
            exigido_map = {i["item_tipo_id"]: i["quantidade_exigida"] for i in itens_tpl}
            tipos = [t for t in tipos
                     if contagem_modal.get(t["id"], 0) < exigido_map.get(t["id"], float("inf"))]
            return {
                "resultado": "desconhecido",
                "mensagem": f"Código '{codigo_barra}' não cadastrado.",
                "codigo_barra": codigo_barra,
                "tipos": tipos,
            }
        itens_template = templates_mod.get_itens_template(session["kit_template_id"])
        template_item_novo = next((i for i in itens_template if i["item_tipo_id"] == item_tipo_id), None)
        if not template_item_novo:
            return {"resultado": "rejeitado",
                    "mensagem": "Tipo selecionado não pertence a este kit."}
        items_mod.criar_item(codigo_barra, item_tipo_id, session["operador_id"])
        item = items_mod.buscar_item(codigo_barra)
        item_recem_criado = True
        codigos_gerados_mod.sincronizar_tipo_se_reciclavel(codigo_barra, item_tipo_id)
        # Item de quantidade em lote (metro/>1 unidade): não desconta aqui —
        # ainda não se sabe quanto vai ser confirmado. O desconto correto
        # acontece em confirmar_quantidade, com a quantidade real informada.
        if not _eh_quantidade_lote(template_item_novo):
            _descontar_estoque_por_patrimonio_novo(item_tipo_id, sessao_id, session["operador_id"])
        kit_ant = _historico_kit_ativo(codigo_barra)
        if kit_ant:
            return {
                "resultado": "substituicao_pendente",
                "mensagem": (f"Patrimônio já utilizado no kit {kit_ant['kit_id']}. "
                             "Confirme a substituição e informe o motivo."),
                "codigo_barra": codigo_barra,
                "kit_id": kit_ant["kit_id"],
            }

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

    requer_serial = bool(template_item.get("requer_serial"))
    unidade = template_item.get("unidade", "un")

    # Metro items always ask for quantity; unit items ask when > 1 needed
    if (unidade == "m" or exigido - atual > 1) and not requer_serial:
        sufixo = "m" if unidade == "m" else " unidade(s)"
        return {
            "resultado": "quantidade_pendente",
            "mensagem": (f"'{item['descricao']}' precisa de {exigido}{sufixo} no kit "
                         f"({atual}{sufixo} já bipada(s)). Quanto você está adicionando?"),
            "codigo_barra": codigo_barra,
            "item_tipo_id": item["item_tipo_id"],
            "descricao": item["descricao"],
            "exigido": exigido,
            "atual": atual,
            "restante": exigido - atual,
            "unidade": unidade,
        }

    if _barcode_em_sessao(sessao_id, codigo_barra):
        return {"resultado": "rejeitado",
                "mensagem": f"Patrimônio '{codigo_barra}' já foi bipado nesta sessão."}

    if not item_recem_criado and not item.get("reutilizavel") and _barcode_em_kit_ativo(codigo_barra):
        return {"resultado": "rejeitado",
                "mensagem": f"Patrimônio '{codigo_barra}' já está em outro kit ativo."}

    with db() as conn:
        conn.execute(
            "INSERT INTO scan_session_items (sessao_id, codigo_barra, item_tipo_id, status, bipado_em, operador_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (sessao_id, codigo_barra, item["item_tipo_id"],
             "aguardando_serial" if requer_serial else "completo", now_brt(), operador_id)
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
    aviso = _aviso_quantidade(template_item)
    return {
        "resultado": "aceito",
        "mensagem": f"'{item['descricao']}' aceito. ({novo_atual}/{exigido})" + aviso,
        "quantidade_aviso": bool(aviso),
        "contagem_atual": novo_atual,
        "quantidade_exigida": exigido,
        "codigo_barra": codigo_barra,
        "item_tipo_id": item["item_tipo_id"],
        "descricao": item["descricao"],
    }


def confirmar_substituicao(sessao_id: int, codigo_barra: str, motivo: str, operador_id: int | None = None) -> dict:
    """Registra bip de patrimônio em substituição, ignorando o histórico de kits anteriores."""
    session = get_session(sessao_id)
    if not session or session["status"] != "em_andamento":
        return {"resultado": "rejeitado", "mensagem": "Sessão inválida ou já encerrada."}

    item = items_mod.buscar_item(codigo_barra)
    if not item:
        return {"resultado": "rejeitado",
                "mensagem": f"Item '{codigo_barra}' não encontrado. Bipe novamente."}

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

    requer_serial = bool(template_item.get("requer_serial"))
    obs = motivo.strip() or None

    with db() as conn:
        conn.execute(
            "INSERT INTO scan_session_items "
            "(sessao_id, codigo_barra, item_tipo_id, status, bipado_em, observacao, operador_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (sessao_id, codigo_barra, item["item_tipo_id"],
             "aguardando_serial" if requer_serial else "completo", now_brt(), obs, operador_id)
        )

    if requer_serial:
        return {
            "resultado": "aguardando_serial",
            "mensagem": f"'{item['descricao']}' (substituição) bipado. Agora bipe o serial number.",
            "codigo_barra": codigo_barra,
            "item_tipo_id": item["item_tipo_id"],
            "descricao": item["descricao"],
        }

    novo_atual = atual + 1
    motivo_texto = motivo.strip() or "—"
    aviso = _aviso_quantidade(template_item)
    return {
        "resultado": "aceito",
        "mensagem": (f"✅ '{item['descricao']}' substituído. Motivo: {motivo_texto} ({novo_atual}/{exigido})"
                    + aviso),
        "quantidade_aviso": bool(aviso),
        "contagem_atual": novo_atual,
        "quantidade_exigida": exigido,
        "codigo_barra": codigo_barra,
        "item_tipo_id": item["item_tipo_id"],
        "descricao": item["descricao"],
    }


def confirmar_quantidade(sessao_id: int, codigo_barra: str, quantidade: float, operador_id: int | None = None) -> dict:
    """Registra N unidades ou metros de um item de uma só vez."""
    session = get_session(sessao_id)
    if not session or session["status"] != "em_andamento":
        return {"resultado": "rejeitado", "mensagem": "Sessão inválida ou já encerrada."}

    if quantidade <= 0:
        return {"resultado": "rejeitado", "mensagem": "Quantidade inválida."}

    item = items_mod.buscar_item(codigo_barra)
    if not item:
        return {"resultado": "rejeitado",
                "mensagem": f"Item '{codigo_barra}' não encontrado. Bipe novamente."}

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
    unidade = template_item.get("unidade", "un")
    quantidade = min(quantidade, exigido - atual)

    if quantidade <= 0:
        return {"resultado": "rejeitado",
                "mensagem": f"'{item['descricao']}': quantidade máxima já atingida."}

    # Itens em metros ou reutilizáveis podem aparecer em múltiplos kits ativos
    if unidade != "m" and not item.get("reutilizavel") and _barcode_em_kit_ativo(codigo_barra):
        return {"resultado": "rejeitado",
                "mensagem": f"Patrimônio '{codigo_barra}' já está em outro kit ativo."}

    est = estoque_mod.buscar_por_tipo(item["item_tipo_id"])
    with db() as conn:
        conn.execute(
            "INSERT INTO scan_session_items "
            "(sessao_id, codigo_barra, item_tipo_id, status, bipado_em, quantidade, operador_id, estoque_debitado) "
            "VALUES (?, ?, ?, 'completo', ?, ?, ?, ?)",
            (sessao_id, codigo_barra, item["item_tipo_id"], now_brt(), quantidade, operador_id,
             quantidade if est else 0)
        )

    # Desconta do estoque vinculado (quando existir) a quantidade real
    # confirmada — fora da transação acima de propósito, mesmo motivo
    # documentado em confirmar_componente (registrar_saida abre sua própria
    # conexão). Sem checar saldo antes: mesma filosofia do resto do sistema.
    if est:
        estoque_mod.registrar_saida(est["id"], quantidade, sessao_id, operador_id or session["operador_id"])

    novo_atual = atual + quantidade

    def _fmt(n: float) -> str:
        f = round(float(n), 2)
        return str(int(f)) if f == int(f) else str(f)

    sufixo = "m" if unidade == "m" else ""
    label = "metro(s)" if unidade == "m" else "unidade(s)"
    aviso = _aviso_quantidade(template_item)
    return {
        "resultado": "aceito",
        "mensagem": (f"✅ '{item['descricao']}': {_fmt(quantidade)}{sufixo} {label} adicionado(s). "
                     f"({_fmt(novo_atual)}{sufixo}/{_fmt(exigido)}{sufixo})"
                     + aviso),
        "quantidade_aviso": bool(aviso),
        "contagem_atual": novo_atual,
        "quantidade_exigida": exigido,
        "codigo_barra": codigo_barra,
        "item_tipo_id": item["item_tipo_id"],
        "descricao": item["descricao"],
        "unidade": unidade,
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


def comparar_troca_template(sessao_id: int, novo_template_id: int,
                            incluir_finalizada: bool = False) -> dict | None:
    """Prévia de trocar o kit de uma bipagem — o que sobrevive à troca e o
    que não. É o MESMO comparador pra todos os usos: troca no meio da
    bipagem, troca de um kit já pronto e o painel de divergências do kit
    (que compara a sessão com o próprio template atual) — uma regra só,
    pra troca pela bipagem e pela tela do kit nunca discordarem.

    Tudo que foi bipado fica gravado por TIPO de item (scan_session_items.
    item_tipo_id), não por template, então trocar o template não apaga bip
    nenhum: o que os dois kits têm em comum continua contado. É justamente
    isso que evita rebipar o kit inteiro quando alguém começou no modelo
    errado.

    Devolve três listas:
      aproveitados — bipagens que continuam valendo no kit novo
      excedentes   — bipado a mais: item que não existe no kit novo, ou
                     acima da quantidade que ele pede
      faltantes    — o que ainda falta bipar depois da troca

    incluir_finalizada=True libera a prévia pra sessões já finalizadas —
    é o caso do kit pronto."""
    session = get_session(sessao_id)
    if not session:
        return None
    if session["status"] != "em_andamento" and not incluir_finalizada:
        return None
    novo = templates_mod.buscar_template(novo_template_id)
    if not novo:
        return None

    itens_novo = templates_mod.get_itens_template(novo_template_id)
    exigido = {i["item_tipo_id"]: i for i in itens_novo}
    contagem = get_contagem(sessao_id)

    nomes = {}
    with db() as conn:
        for r in conn.execute("SELECT id, nome FROM item_tipo").fetchall():
            nomes[r["id"]] = r["nome"]

    aproveitados, excedentes = [], []
    for tipo_id, bipado in contagem.items():
        item = exigido.get(tipo_id)
        cabe = min(bipado, item["quantidade_exigida"]) if item else 0
        if cabe:
            aproveitados.append({
                "item_tipo_id": tipo_id,
                "descricao": item["descricao"],
                "quantidade": cabe,
                "exigido": item["quantidade_exigida"],
            })
        sobra = bipado - cabe
        if sobra > 0:
            excedentes.append({
                "item_tipo_id": tipo_id,
                "descricao": item["descricao"] if item else nomes.get(tipo_id, "?"),
                "quantidade": sobra,
                "no_kit_novo": item is not None,
            })

    faltantes = []
    for item in itens_novo:
        atual = min(contagem.get(item["item_tipo_id"], 0), item["quantidade_exigida"])
        if atual < item["quantidade_exigida"]:
            faltantes.append({
                "item_tipo_id": item["item_tipo_id"],
                "descricao": item["descricao"],
                "bipado": atual,
                "exigido": item["quantidade_exigida"],
                "faltam": item["quantidade_exigida"] - atual,
            })

    aproveitados.sort(key=lambda x: x["descricao"])
    excedentes.sort(key=lambda x: x["descricao"])
    return {
        "sessao": session,
        "novo": novo,
        "aproveitados": aproveitados,
        "excedentes": excedentes,
        "faltantes": faltantes,
        "total_bipado": sum(contagem.values()),
        "total_aproveitado": sum(a["quantidade"] for a in aproveitados),
    }


def trocar_template(sessao_id: int, novo_template_id: int) -> dict:
    """Troca o kit de uma bipagem EM ANDAMENTO, preservando as bipagens.

    Toda a tela de bipagem (itens exigidos, progresso, validação do
    finalizar) é derivada de scan_session.kit_template_id na hora, então
    trocar essa referência já faz os itens que faltam aparecerem sozinhos.

    O `modelo` gravado na sessão também é atualizado, porque ele é o nome do
    kit e vai parar na etiqueta e no relatório do kit finalizado."""
    session = get_session(sessao_id)
    if not session:
        return {"resultado": "rejeitado", "mensagem": "Sessão não encontrada."}
    if session["status"] != "em_andamento":
        return {"resultado": "rejeitado",
                "mensagem": "Só dá pra trocar o kit de uma bipagem em andamento."}
    if session["kit_template_id"] == novo_template_id:
        return {"resultado": "rejeitado", "mensagem": "Esse já é o kit desta bipagem."}
    novo = templates_mod.buscar_template(novo_template_id)
    if not novo:
        return {"resultado": "rejeitado", "mensagem": "Kit não encontrado."}
    if not novo.get("ativo"):
        return {"resultado": "rejeitado", "mensagem": "Esse kit está desativado."}

    with db() as conn:
        conn.execute(
            "UPDATE scan_session SET kit_template_id = ?, kit_template_versao = ?, "
            "modelo = ? WHERE id = ? AND status = 'em_andamento'",
            (novo_template_id, novo["versao"], novo["nome"], sessao_id)
        )
    return {"resultado": "trocado", "kit_nome": novo["nome"],
            "kit_anterior": session["kit_nome"]}


def _linhas_para_remover(conn, sessao_id: int, item_tipo_id: int,
                         unidades: float) -> list[dict]:
    """Escolhe QUAIS linhas de bipagem saem quando um tipo precisa perder
    `unidades` — usado na prévia (pra mostrar o que volta pro estoque) e na
    execução da troca de kit pronto, então os dois nunca divergem.

    Remove primeiro as linhas vindas do estoque (prefixo ESTOQUE:/COMP: —
    cada uma vale 1 unidade e É estornável, mesma regra do remover_item da
    bipagem) e só depois as bipagens de patrimônio, que não movimentam
    estoque: o patrimônio fica LIVRE ao sair do kit (a checagem de "já está
    em kit ativo" olha as linhas de sessão). Linha de quantidade em lote
    (metros de cabo) pode ser reduzida parcialmente."""
    rows = conn.execute(
        "SELECT id, codigo_barra, COALESCE(quantidade, 1) AS quantidade "
        "FROM scan_session_items "
        "WHERE sessao_id = ? AND item_tipo_id = ? "
        "AND (status IS NULL OR status = 'completo') "
        "ORDER BY (CASE WHEN codigo_barra LIKE 'ESTOQUE:%' "
        "               OR codigo_barra LIKE 'COMP:%' THEN 0 ELSE 1 END), id DESC",
        (sessao_id, item_tipo_id)
    ).fetchall()
    plano, restante = [], unidades
    for r in rows:
        if restante <= 0:
            break
        estorna = (r["codigo_barra"].startswith("ESTOQUE:")
                   or r["codigo_barra"].startswith("COMP:"))
        tira = min(r["quantidade"], restante)
        plano.append({
            "id": r["id"], "codigo_barra": r["codigo_barra"],
            "quantidade_remover": tira,
            "remove_linha": tira >= r["quantidade"],
            "estorna": estorna,
        })
        restante -= tira
    return plano


def previa_troca_kit_pronto(kit_id: str, novo_template_id: int) -> dict | None:
    """Prévia da troca de kit de um kit JÁ PRONTO — o mesmo comparador da
    troca em bipagem, mais o efeito no estoque, porque aqui a troca mexe em
    coisa física já montada e descontada:

      excedentes ganham `volta_estoque` (unidades estornadas — só as que
        SAÍRAM do estoque, identificadas pelo prefixo) e
        `libera_patrimonio` (bipagens de patrimônio que ficam livres);
      faltantes ganham `tem_estoque`/`estoque_disponivel`/`estoque_suficiente`
        — tipo com estoque vinculado sai do estoque sozinho na confirmação;
        tipo sem estoque não tem de onde sair e fica como pendência visível
        no painel de divergências do kit (patrimônio nunca é escolhido
        automaticamente: o sistema não sabe QUAL unidade física está na
        caixa, e chutar roubaria patrimônio de outro kit).

    `bloqueios` lista os faltantes cujo estoque vinculado não tem saldo —
    com bloqueio a troca não executa (troca de kit pronto é ação deliberada
    de escritório, então segue a regra de travar; desconto automático de
    bipagem é que nunca trava)."""
    with db() as conn:
        kr = conn.execute("SELECT * FROM kit_record WHERE kit_id = ?", (kit_id,)).fetchone()
    if not kr:
        return None
    kr = dict(kr)
    previa = comparar_troca_template(kr["sessao_id"], novo_template_id,
                                     incluir_finalizada=True)
    if previa is None:
        return None

    with db() as conn:
        for exc in previa["excedentes"]:
            plano = _linhas_para_remover(conn, kr["sessao_id"],
                                         exc["item_tipo_id"], exc["quantidade"])
            exc["volta_estoque"] = sum(
                p["quantidade_remover"] for p in plano if p["estorna"])
            exc["libera_patrimonio"] = sum(
                1 for p in plano if not p["estorna"] and p["remove_linha"])

    bloqueios = []
    for f in previa["faltantes"]:
        est = estoque_mod.buscar_por_tipo(f["item_tipo_id"])
        f["tem_estoque"] = est is not None
        f["estoque_disponivel"] = est["quantidade_atual"] if est else 0
        f["estoque_suficiente"] = bool(est) and est["quantidade_atual"] >= f["faltam"]
        if est and not f["estoque_suficiente"]:
            bloqueios.append(
                f"{f['descricao']}: precisa de {f['faltam']}, estoque tem "
                f"{est['quantidade_atual']}")
    previa["bloqueios"] = bloqueios
    previa["kit"] = kr
    return previa


def trocar_template_kit_pronto(kit_id: str, novo_template_id: int,
                               operador_id: int) -> dict:
    """Executa a troca de kit de um kit JÁ PRONTO, numa transação só:
    remove as bipagens excedentes (estornando ao estoque o que veio dele),
    puxa do estoque os faltantes de tipo com estoque vinculado e atualiza
    sessão e kit_record pro template novo. Ou tudo, ou nada — qualquer
    validação que falhe sai ANTES do primeiro UPDATE, e uma exceção no meio
    desfaz o resto (o with db() só commita ao sair sem erro).

    O operador não corrige estoque na mão: cada movimento fica em
    estoque_movimentos citando o kit, e o saldo é reconferido DENTRO da
    transação — a prévia pode ter ficado velha entre mostrar e confirmar."""
    with db() as conn:
        kr = conn.execute("SELECT * FROM kit_record WHERE kit_id = ?", (kit_id,)).fetchone()
    if not kr:
        return {"resultado": "rejeitado", "mensagem": "Kit não encontrado."}
    kr = dict(kr)
    if kr["kit_template_id"] == novo_template_id:
        return {"resultado": "rejeitado", "mensagem": "Esse já é o kit deste registro."}
    novo = templates_mod.buscar_template(novo_template_id)
    if not novo:
        return {"resultado": "rejeitado", "mensagem": "Kit não encontrado."}
    if not novo.get("ativo"):
        return {"resultado": "rejeitado", "mensagem": "Esse kit está desativado."}
    previa = previa_troca_kit_pronto(kit_id, novo_template_id)
    if previa is None:
        return {"resultado": "rejeitado", "mensagem": "Não foi possível comparar os kits."}
    if previa["bloqueios"]:
        return {"resultado": "rejeitado",
                "mensagem": "Estoque insuficiente — " + "; ".join(previa["bloqueios"])}

    sessao_id = kr["sessao_id"]
    estornos: list[str] = []
    saidas: list[str] = []
    try:
        _executar_troca_kit_pronto(kit_id, sessao_id, novo_template_id, novo,
                                   previa, operador_id, estornos, saidas)
    except ValueError as e:
        # O rollback do with db() já desfez tudo — nada ficou pela metade.
        return {"resultado": "rejeitado", "mensagem": str(e)}

    pendentes = [f for f in previa["faltantes"] if not f["tem_estoque"]]
    return {"resultado": "trocado",
            "kit_anterior": previa["sessao"]["kit_nome"],
            "kit_nome": novo["nome"],
            "estornos": estornos, "saidas": saidas,
            "pendentes": [{"descricao": p["descricao"], "faltam": p["faltam"]}
                          for p in pendentes]}


def _executar_troca_kit_pronto(kit_id: str, sessao_id: int, novo_template_id: int,
                               novo: dict, previa: dict, operador_id: int,
                               estornos: list, saidas: list) -> None:
    with db() as conn:
        # 1. Excedentes: remove/reduz as linhas e estorna o que veio do
        # estoque. Movimentos inline na MESMA conexão — repor_estoque()
        # abriria uma segunda conexão dentro da transação e travaria o
        # SQLite ("database is locked").
        for exc in previa["excedentes"]:
            plano = _linhas_para_remover(conn, sessao_id,
                                         exc["item_tipo_id"], exc["quantidade"])
            devolver = 0
            for p in plano:
                if p["remove_linha"]:
                    conn.execute("DELETE FROM scan_session_items WHERE id = ?", (p["id"],))
                else:
                    conn.execute(
                        "UPDATE scan_session_items SET quantidade = quantidade - ? "
                        "WHERE id = ?", (p["quantidade_remover"], p["id"]))
                if p["estorna"]:
                    devolver += p["quantidade_remover"]
            if devolver:
                est = conn.execute(
                    "SELECT id FROM estoque WHERE item_tipo_id = ?",
                    (exc["item_tipo_id"],)).fetchone()
                if est:
                    conn.execute(
                        "UPDATE estoque SET quantidade_atual = quantidade_atual + ? "
                        "WHERE id = ?", (devolver, est["id"]))
                    conn.execute(
                        "INSERT INTO estoque_movimentos (estoque_id, tipo, quantidade, "
                        "sessao_id, criado_por, observacao, criado_em) "
                        "VALUES (?, 'entrada', ?, ?, ?, ?, ?)",
                        (est["id"], devolver, sessao_id, operador_id,
                         f"Estorno — troca de kit do kit pronto {kit_id[:8]}", now_brt()))
                    estornos.append(f"{exc['descricao']}: +{devolver}")

        # 2. Faltantes com estoque vinculado: saldo reconferido AGORA, e a
        # exceção desfaz a transação inteira — nada fica pela metade.
        for f in previa["faltantes"]:
            est = conn.execute(
                "SELECT e.*, it.nome AS tipo_nome FROM estoque e "
                "JOIN item_tipo it ON it.id = e.item_tipo_id "
                "WHERE e.item_tipo_id = ?", (f["item_tipo_id"],)).fetchone()
            if not est:
                continue  # sem estoque vinculado: fica como pendência visível
            if est["quantidade_atual"] < f["faltam"]:
                raise ValueError(
                    f"Estoque de '{est['tipo_nome']}' mudou durante a troca "
                    f"({est['quantidade_atual']} < {f['faltam']}). Nada foi alterado.")
            # Sufixo T{n} no lugar do seq numérico pra nunca colidir com as
            # linhas que a bipagem original gravou; o estorno do
            # remover_item lê o código do meio, então continua funcionando.
            for i in range(int(f["faltam"])):
                conn.execute(
                    "INSERT INTO scan_session_items (sessao_id, codigo_barra, "
                    "item_tipo_id, status, bipado_em, operador_id) "
                    "VALUES (?, ?, ?, 'completo', ?, ?)",
                    (sessao_id, f"ESTOQUE:{est['codigo_barra']}:T{i}",
                     f["item_tipo_id"], now_brt(), operador_id))
            conn.execute(
                "UPDATE estoque SET quantidade_atual = quantidade_atual - ? "
                "WHERE id = ?", (f["faltam"], est["id"]))
            conn.execute(
                "INSERT INTO estoque_movimentos (estoque_id, tipo, quantidade, "
                "sessao_id, criado_por, observacao, criado_em) "
                "VALUES (?, 'saida', ?, ?, ?, ?, ?)",
                (est["id"], f["faltam"], sessao_id, operador_id,
                 f"Troca de kit do kit pronto {kit_id[:8]}", now_brt()))
            saidas.append(f"{f['descricao']}: -{f['faltam']}")

        # 3. Sessão e kit_record apontam pro template novo — o modelo (nome
        # do kit) vai junto, é ele que aparece em etiqueta e relatório.
        conn.execute(
            "UPDATE scan_session SET kit_template_id = ?, kit_template_versao = ?, "
            "modelo = ? WHERE id = ?",
            (novo_template_id, novo["versao"], novo["nome"], sessao_id))
        # 4. A conferência item a item descrevia o conteúdo ANTIGO — o kit
        # mudou, então tem que ser refeita. O histórico de verificações
        # (kit_validacoes) NÃO é apagado: é registro de quem verificou e
        # quando, e apagar seria reescrever o passado. Em vez disso guardamos
        # o id da última verificação de antes da troca — daí pra trás, a tela
        # mostra "anterior à troca" e o selo de verificado só volta quando
        # alguém verificar de novo.
        corte = conn.execute(
            "SELECT COALESCE(MAX(id), 0) FROM kit_validacoes WHERE kit_id = ?",
            (kit_id,)).fetchone()[0]
        conn.execute(
            "UPDATE kit_record SET kit_template_id = ?, kit_template_versao = ?, "
            "modelo = ?, modelo_trocado_em = ?, verificacao_corte = ? WHERE kit_id = ?",
            (novo_template_id, novo["versao"], novo["nome"], now_brt(), corte, kit_id))
        conn.execute("DELETE FROM kit_verificacao_itens WHERE kit_id = ?", (kit_id,))


def listar_sessoes_em_andamento(template_id: int | None = None,
                                operador_id: int | None = None) -> list:
    """Lista sessões em andamento, opcionalmente filtradas por template ou operador."""
    with db() as conn:
        conditions = ["s.status = 'em_andamento'"]
        params: list = []
        if template_id is not None:
            conditions.append("s.kit_template_id = ?")
            params.append(template_id)
        if operador_id is not None:
            conditions.append("s.operador_id = ?")
            params.append(operador_id)
        where = " AND ".join(conditions)
        rows = conn.execute(
            f"SELECT s.*, t.nome AS kit_nome, t.cliente, u.nome AS operador_nome "
            f"FROM scan_session s "
            f"JOIN kit_template t ON t.id = s.kit_template_id "
            f"JOIN users u ON u.id = s.operador_id "
            f"WHERE {where} ORDER BY s.iniciado_em DESC",
            params
        ).fetchall()
    return [dict(r) for r in rows]


def listar_itens_por_operador(sessao_id: int) -> list[dict]:
    """Itens já bipados (status completo) nesta sessão, agrupados por quem
    bipou cada um, na ordem em que cada operador apareceu pela primeira
    vez. Linhas sem operador_id (bipadas antes desta coluna existir) ficam
    agrupadas sob a chave None, nome "Sem operador registrado"."""
    with db() as conn:
        rows = conn.execute(
            "SELECT ssi.id, ssi.operador_id, u.nome AS operador_nome, "
            "it.nome AS descricao, ssi.codigo_barra, ssi.bipado_em "
            "FROM scan_session_items ssi "
            "JOIN item_tipo it ON it.id = ssi.item_tipo_id "
            "LEFT JOIN users u ON u.id = ssi.operador_id "
            "WHERE ssi.sessao_id = ? AND (ssi.status IS NULL OR ssi.status = 'completo') "
            "ORDER BY ssi.bipado_em",
            (sessao_id,)
        ).fetchall()

    grupos: dict = {}
    ordem: list = []
    for r in rows:
        op_id = r["operador_id"]
        if op_id not in grupos:
            grupos[op_id] = {
                "operador_id": op_id,
                "operador_nome": r["operador_nome"] or "Sem operador registrado",
                "itens": [],
            }
            ordem.append(op_id)
        grupos[op_id]["itens"].append({
            "id": r["id"],
            "descricao": r["descricao"],
            "codigo_barra": r["codigo_barra"],
            "bipado_em": r["bipado_em"],
        })
    return [grupos[op_id] for op_id in ordem]


def remover_item(sessao_id: int, item_id: int) -> dict:
    """Remove uma bipagem específica (uma linha só, não o lote inteiro) de
    uma sessão ainda em andamento — corrige item errado, quantidade errada
    ou metro errado sem precisar desfazer tudo que veio depois.

    Se o código bipado tiver o prefixo "ESTOQUE:<codigo>:<seq>" (item veio
    de uma caixa de estoque vinculada, register_scan grava assim) ou
    "COMP:<conjunto>:<tipo_id>:<seq>" (item veio de um conjunto cujo tipo
    tem estoque vinculado, confirmar_componente grava assim), devolve 1
    unidade ao estoque de origem automaticamente. Casar isso por timestamp
    (mesmo instante) seria arriscado — o relógio só tem precisão de
    segundo, então duas bipagens diferentes no mesmo segundo fariam o
    estorno acertar o movimento errado. O prefixo identifica a origem sem
    ambiguidade, então só ele decide se estorna ou não."""
    session = get_session(sessao_id)
    if not session or session["status"] != "em_andamento":
        return {"resultado": "rejeitado", "mensagem": "Sessão inválida ou já encerrada."}

    with db() as conn:
        item = conn.execute(
            "SELECT * FROM scan_session_items "
            "WHERE id = ? AND sessao_id = ? AND status = 'completo'",
            (item_id, sessao_id)
        ).fetchone()
    if not item:
        return {"resultado": "rejeitado", "mensagem": "Item não encontrado nesta sessão."}
    item = dict(item)

    with db() as conn:
        conn.execute("DELETE FROM scan_session_items WHERE id = ?", (item_id,))

    # Só tenta estornar quando dá pra identificar o estoque de origem sem
    # ambiguidade — pelo prefixo "ESTOQUE:<codigo>:<seq>" que register_scan
    # grava quando o item veio de uma caixa de estoque vinculada. Casar por
    # timestamp (mesmo segundo) é arriscado demais: duas bipagens dentro do
    # mesmo segundo (comum, o timestamp só tem precisão de segundo) fariam
    # o estorno pegar o movimento de estoque errado.
    estoque_ajustado = False
    partes = item["codigo_barra"].split(":")
    est = None
    if len(partes) >= 3 and partes[0] == "ESTOQUE":
        codigo_estoque = ":".join(partes[1:-1])
        est = estoque_mod.buscar_por_referencia(codigo_estoque)
    elif len(partes) >= 4 and partes[0] == "COMP":
        # Conjunto não guarda um código de estoque no codigo_barra — o
        # tipo do item já identifica o estoque vinculado sem ambiguidade
        # (estoque.item_tipo_id é único), não precisa parsear mais nada.
        est = estoque_mod.buscar_por_tipo(item["item_tipo_id"])
    if est:
        estoque_mod.repor_estoque(
            est["id"], 1, item.get("operador_id") or session["operador_id"],
            observacao=f"Estorno automático — exclusão de bipagem (sessão {sessao_id})"
        )
        estoque_ajustado = True

    with db() as conn:
        tipo_row = conn.execute(
            "SELECT nome FROM item_tipo WHERE id = ?", (item["item_tipo_id"],)
        ).fetchone()
    descricao = tipo_row["nome"] if tipo_row else "?"

    itens_template = templates_mod.get_itens_template(session["kit_template_id"])
    template_item = next(
        (i for i in itens_template if i["item_tipo_id"] == item["item_tipo_id"]), None
    )
    contagem = get_contagem(sessao_id)

    return {
        "resultado": "item_removido",
        "mensagem": (f"🗑 Bipagem de '{descricao}' removida."
                     + (" Estoque devolvido." if estoque_ajustado else "")),
        "item_tipo_id": item["item_tipo_id"],
        "contagem_atual": contagem.get(item["item_tipo_id"], 0),
        "quantidade_exigida": template_item["quantidade_exigida"] if template_item else 0,
    }


def operadores_da_sessao(sessao_id: int) -> list[dict]:
    """Operadores distintos que bipararam algo (status completo) nesta
    sessão, na ordem da primeira bipagem de cada um. Ignora linhas sem
    operador_id (sessões/itens de antes desta coluna existir)."""
    with db() as conn:
        rows = conn.execute(
            "SELECT ssi.operador_id, u.nome AS operador_nome, COUNT(*) AS total_itens, "
            "MIN(ssi.bipado_em) AS primeira_bipagem "
            "FROM scan_session_items ssi "
            "JOIN users u ON u.id = ssi.operador_id "
            "WHERE ssi.sessao_id = ? AND ssi.operador_id IS NOT NULL "
            "AND (ssi.status IS NULL OR ssi.status = 'completo') "
            "GROUP BY ssi.operador_id "
            "ORDER BY primeira_bipagem",
            (sessao_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def cancel_session(sessao_id: int):
    estoque_mod.reverter_saidas_sessao(sessao_id)
    with db() as conn:
        conn.execute(
            "UPDATE scan_session SET status = 'cancelado', "
            "finalizado_em = ? WHERE id = ?",
            (now_brt(), sessao_id)
        )


def listar_por_operador(operador_id: int | None = None,
                        data_ini: str = "", data_fim: str = "",
                        incluir_finalizados: bool = True) -> list[dict]:
    """Kits de cada operador — em andamento E finalizados, na mesma lista.

    O relatório de kits só mostra kit finalizado, então não dava pra ver o
    que está em montagem agora nem quem abriu cada um. Aqui os dois estados
    aparecem juntos, ordenados do mais recente pro mais antigo.

    `operador` é sempre QUEM ABRIU a bipagem — é quem responde pelo kit.
    Quando outra pessoa finalizou, `finalizado_por_nome` vem preenchido e a
    tela mostra os dois nomes (kit feito em dupla)."""
    em_andamento = """
        SELECT 'em_andamento' AS estado,
               NULL            AS kit_id,
               ss.id           AS sessao_id,
               ss.iniciado_em  AS comecou_em,
               NULL            AS finalizado_em,
               ss.veiculo, ss.garagem,
               kt.nome AS kit_nome, kt.cliente,
               ss.operador_id,
               uo.nome AS operador_nome,
               NULL    AS finalizado_por_nome,
               (SELECT COUNT(*) FROM scan_session_items si
                 WHERE si.sessao_id = ss.id) AS itens_bipados
        FROM scan_session ss
        JOIN kit_template kt ON kt.id = ss.kit_template_id
        JOIN users uo ON uo.id = ss.operador_id
        WHERE ss.status = 'em_andamento'
    """
    finalizados = """
        SELECT 'finalizado' AS estado,
               kr.kit_id,
               kr.sessao_id,
               ss.iniciado_em    AS comecou_em,
               kr.finalizado_em,
               COALESCE(v.numero, kr.veiculo) AS veiculo,
               kr.garagem,
               kt.nome AS kit_nome, kt.cliente,
               kr.operador_id,
               uo.nome AS operador_nome,
               CASE WHEN kr.finalizado_por IS NOT NULL
                     AND kr.finalizado_por != kr.operador_id
                    THEN uf.nome END AS finalizado_por_nome,
               (SELECT COUNT(*) FROM scan_session_items si
                 WHERE si.sessao_id = kr.sessao_id) AS itens_bipados
        FROM kit_record kr
        JOIN kit_template kt ON kt.id = kr.kit_template_id
        JOIN scan_session ss ON ss.id = kr.sessao_id
        JOIN users uo ON uo.id = kr.operador_id
        LEFT JOIN users uf ON uf.id = kr.finalizado_por
        LEFT JOIN veiculos v ON v.id = kr.veiculo_id
        WHERE 1=1
    """
    p_and: list = []
    p_fim: list = []
    if operador_id:
        em_andamento += " AND ss.operador_id = ?"
        finalizados += " AND kr.operador_id = ?"
        p_and.append(operador_id)
        p_fim.append(operador_id)
    sql_and, p_d_and = datas_mod.clausula("ss.iniciado_em", data_ini, data_fim)
    sql_fim, p_d_fim = datas_mod.clausula("kr.finalizado_em", data_ini, data_fim)
    em_andamento += sql_and
    finalizados += sql_fim
    p_and += p_d_and
    p_fim += p_d_fim

    with db() as conn:
        linhas = [dict(r) for r in conn.execute(em_andamento, p_and).fetchall()]
        if incluir_finalizados:
            linhas += [dict(r) for r in conn.execute(finalizados, p_fim).fetchall()]

    # Em andamento primeiro (é o que precisa de atenção agora), cada bloco
    # do mais recente pro mais antigo.
    em = sorted((l for l in linhas if l["estado"] == "em_andamento"),
                key=lambda l: l["comecou_em"] or "", reverse=True)
    fim = sorted((l for l in linhas if l["estado"] != "em_andamento"),
                 key=lambda l: l["finalizado_em"] or "", reverse=True)
    return em + fim


def resumo_por_operador(data_ini: str = "", data_fim: str = "") -> list[dict]:
    """Quantos kits cada operador abriu no período, separando o que ainda
    está em montagem do que já foi finalizado."""
    linhas = listar_por_operador(data_ini=data_ini, data_fim=data_fim)
    por_operador: dict = {}
    for l in linhas:
        r = por_operador.setdefault(l["operador_id"], {
            "operador_id": l["operador_id"],
            "operador_nome": l["operador_nome"],
            "em_andamento": 0, "finalizados": 0, "em_dupla": 0,
        })
        if l["estado"] == "em_andamento":
            r["em_andamento"] += 1
        else:
            r["finalizados"] += 1
            if l["finalizado_por_nome"]:
                r["em_dupla"] += 1
    return sorted(por_operador.values(),
                  key=lambda r: (-(r["em_andamento"] + r["finalizados"]), r["operador_nome"]))
