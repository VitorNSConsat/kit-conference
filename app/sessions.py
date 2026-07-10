from database import db
import app.items as items_mod
import app.kit_templates as templates_mod


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
    return cur.lastrowid


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
    """Retorna mapa {codigo_barra: quantidade_bipada}."""
    with db() as conn:
        rows = conn.execute(
            "SELECT codigo_barra, COUNT(*) as qtd FROM scan_session_items "
            "WHERE sessao_id = ? GROUP BY codigo_barra",
            (sessao_id,)
        ).fetchall()
    return {r["codigo_barra"]: r["qtd"] for r in rows}


def _serials_usados(sessao_id: int, codigo_barra: str) -> set:
    with db() as conn:
        rows = conn.execute(
            "SELECT serial FROM scan_session_items "
            "WHERE sessao_id = ? AND codigo_barra = ? AND serial IS NOT NULL",
            (sessao_id, codigo_barra)
        ).fetchall()
    return {r["serial"] for r in rows}


def _serial_em_outro_kit(serial: str, codigo_barra: str) -> bool:
    with db() as conn:
        row = conn.execute(
            "SELECT 1 FROM scan_session_items si "
            "JOIN scan_session s ON s.id = si.sessao_id "
            "JOIN kit_record kr ON kr.sessao_id = s.id "
            "WHERE si.serial = ? AND si.codigo_barra = ? AND kr.status = 'ativo'",
            (serial, codigo_barra)
        ).fetchone()
    return row is not None


def register_scan(sessao_id: int, codigo_barra: str,
                  serial: str | None = None) -> dict:
    """
    Retorna dict com:
      resultado: 'aceito' | 'rejeitado'
      mensagem: str
      contagem_atual: int (se aceito)
      quantidade_exigida: int (se aceito)
    """
    session = get_session(sessao_id)
    if not session or session["status"] != "em_andamento":
        return {"resultado": "rejeitado",
                "mensagem": "Sessão inválida ou já encerrada."}

    item = items_mod.buscar_item(codigo_barra)
    if not item:
        return {"resultado": "rejeitado",
                "mensagem": f"Código '{codigo_barra}' não encontrado no catálogo. "
                            f"Cadastre o item antes de continuar."}

    itens_template = templates_mod.get_itens_template(session["kit_template_id"])
    template_item = next(
        (i for i in itens_template if i["codigo_barra"] == codigo_barra), None
    )
    if not template_item:
        return {"resultado": "rejeitado",
                "mensagem": f"Item '{item['descricao']}' não pertence a este kit."}

    contagem = get_contagem(sessao_id)
    atual = contagem.get(codigo_barra, 0)
    exigido = template_item["quantidade_exigida"]

    if atual >= exigido:
        return {"resultado": "rejeitado",
                "mensagem": f"'{item['descricao']}' já foi bipado {atual} vez(es) "
                            f"— quantidade máxima ({exigido}) atingida."}

    if item["controla_serial"]:
        if not serial:
            serial = codigo_barra  # leitor envia o próprio código como serial
        if serial in _serials_usados(sessao_id, codigo_barra):
            return {"resultado": "rejeitado",
                    "mensagem": f"Serial '{serial}' já registrado nesta sessão."}
        if _serial_em_outro_kit(serial, codigo_barra):
            return {"resultado": "rejeitado",
                    "mensagem": f"Serial '{serial}' já registrado em outro kit ativo."}

    with db() as conn:
        conn.execute(
            "INSERT INTO scan_session_items (sessao_id, codigo_barra, serial) "
            "VALUES (?, ?, ?)",
            (sessao_id, codigo_barra, serial if item["controla_serial"] else None)
        )

    novo_atual = atual + 1
    return {
        "resultado": "aceito",
        "mensagem": f"'{item['descricao']}' aceito. ({novo_atual}/{exigido})",
        "contagem_atual": novo_atual,
        "quantidade_exigida": exigido,
        "codigo_barra": codigo_barra,
        "descricao": item["descricao"],
    }


def validate_kit_complete(sessao_id: int) -> dict:
    """
    Retorna dict com:
      status: 'completo' | 'incompleto'
      itens_faltantes: list (vazio se completo)
    """
    session = get_session(sessao_id)
    itens_template = templates_mod.get_itens_template(session["kit_template_id"])
    contagem = get_contagem(sessao_id)
    faltantes = []
    for item in itens_template:
        if not item["obrigatorio"]:
            continue
        cb = item["codigo_barra"]
        atual = contagem.get(cb, 0)
        if atual < item["quantidade_exigida"]:
            faltantes.append({
                "codigo_barra": cb,
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
