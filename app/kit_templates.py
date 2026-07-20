import io
from database import db, now_brt


def listar_templates_ativos() -> list:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM kit_template WHERE ativo = 1 ORDER BY nome"
        ).fetchall()
    return [dict(r) for r in rows]


def listar_todos() -> list:
    with db() as conn:
        rows = conn.execute(
            "SELECT t.*, u.nome AS criado_por_nome FROM kit_template t "
            "LEFT JOIN users u ON u.id = t.criado_por ORDER BY t.nome, t.versao"
        ).fetchall()
    return [dict(r) for r in rows]


def buscar_template(template_id: int) -> dict | None:
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM kit_template WHERE id = ?", (template_id,)
        ).fetchone()
    return dict(row) if row else None


def get_itens_template(template_id: int) -> list:
    with db() as conn:
        rows = conn.execute(
            "SELECT ki.*, COALESCE(it.nome, '[Tipo removido]') AS descricao, "
            "COALESCE(it.unidade, 'un') AS unidade "
            "FROM kit_template_items ki "
            "LEFT JOIN item_tipo it ON it.id = ki.item_tipo_id "
            "WHERE ki.kit_template_id = ? "
            "ORDER BY ki.id",
            (template_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def criar_template(nome: str, cliente: str, criado_por: int,
                   itens: list[dict]) -> int:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO kit_template (nome, cliente, versao, criado_por) "
            "VALUES (?, ?, 1, ?)",
            (nome, cliente, criado_por)
        )
        template_id = cur.lastrowid
        for item in itens:
            conn.execute(
                "INSERT INTO kit_template_items "
                "(kit_template_id, item_tipo_id, quantidade_exigida, obrigatorio, "
                "componente_codigo, requer_serial) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (template_id, item["item_tipo_id"],
                 item["quantidade_exigida"], int(item.get("obrigatorio", True)),
                 item.get("componente_codigo") or None,
                 int(bool(item.get("requer_serial", False))))
            )
    return template_id


def nova_versao(template_id: int, criado_por: int) -> int:
    """Clona template com versao+1 e desativa o original."""
    template = buscar_template(template_id)
    itens = get_itens_template(template_id)
    nova_ver = template["versao"] + 1
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO kit_template (nome, cliente, versao, criado_por) "
            "VALUES (?, ?, ?, ?)",
            (template["nome"], template["cliente"], nova_ver, criado_por)
        )
        novo_id = cur.lastrowid
        conn.execute("UPDATE kit_template SET ativo = 0 WHERE id = ?", (template_id,))
        for item in itens:
            conn.execute(
                "INSERT INTO kit_template_items "
                "(kit_template_id, item_tipo_id, quantidade_exigida, obrigatorio, "
                "componente_codigo, requer_serial) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (novo_id, item["item_tipo_id"],
                 item["quantidade_exigida"], item["obrigatorio"],
                 item.get("componente_codigo"),
                 item.get("requer_serial", 0))
            )
    return novo_id


def atualizar_template(template_id: int, nome: str, cliente: str,
                       itens: list[dict]):
    """Atualiza nome, cliente e itens. Itens antigos são substituídos."""
    with db() as conn:
        conn.execute(
            "UPDATE kit_template SET nome = ?, cliente = ? WHERE id = ?",
            (nome, cliente, template_id)
        )
        conn.execute(
            "DELETE FROM kit_template_items WHERE kit_template_id = ?", (template_id,)
        )
        for item in itens:
            conn.execute(
                "INSERT INTO kit_template_items "
                "(kit_template_id, item_tipo_id, quantidade_exigida, obrigatorio, "
                "componente_codigo, requer_serial) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (template_id, item["item_tipo_id"],
                 item["quantidade_exigida"], int(item.get("obrigatorio", True)),
                 item.get("componente_codigo") or None,
                 int(bool(item.get("requer_serial", False))))
            )


def deletar_template(template_id: int):
    """Exclui template em cascade. Bloqueia apenas sessões em andamento."""
    with db() as conn:
        sessoes = conn.execute(
            "SELECT COUNT(*) FROM scan_session WHERE kit_template_id = ? AND status = 'em_andamento'",
            (template_id,)
        ).fetchone()[0]
        if sessoes:
            raise ValueError(f"Template possui {sessoes} sessão(ões) em andamento. Finalize antes de excluir.")

        # Cascade: print_queue → kit_record → scan_session_items → scan_session → itens → template
        sessao_ids = [r[0] for r in conn.execute(
            "SELECT id FROM scan_session WHERE kit_template_id = ?", (template_id,)
        ).fetchall()]
        for sid in sessao_ids:
            conn.execute("DELETE FROM scan_session_items WHERE sessao_id = ?", (sid,))
        kit_ids = [r[0] for r in conn.execute(
            "SELECT kit_id FROM kit_record WHERE kit_template_id = ?", (template_id,)
        ).fetchall()]
        for kid in kit_ids:
            conn.execute("DELETE FROM print_queue WHERE kit_id = ?", (kid,))
        conn.execute("DELETE FROM kit_record WHERE kit_template_id = ?", (template_id,))
        conn.execute("DELETE FROM scan_session WHERE kit_template_id = ?", (template_id,))
        conn.execute("DELETE FROM kit_template_items WHERE kit_template_id = ?", (template_id,))
        conn.execute("DELETE FROM kit_template WHERE id = ?", (template_id,))


def toggle_ativo(template_id: int):
    with db() as conn:
        conn.execute(
            "UPDATE kit_template SET ativo = 1 - ativo WHERE id = ?", (template_id,)
        )


def criar_template_do_bom(nome: str, cliente: str, criado_por: int,
                           conteudo: bytes) -> tuple[int, dict]:
    """Cria um kit_template a partir de um BOM Excel.

    Detecta automaticamente o cabeçalho ('Description' e 'Quantity').
    Cria item_tipo caso ainda não exista. Retorna (template_id, stats).
    """
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(conteudo), data_only=True)
    ws = wb.active

    col_desc = col_qty = None
    past_header = False
    itens: list[dict] = []
    tipos_criados = 0

    with db() as conn:
        for row in ws.iter_rows(values_only=True):
            cells = [str(c).strip().lower() if c else "" for c in row]

            if not past_header:
                if "description" in cells:
                    col_desc = next(i for i, c in enumerate(cells) if c == "description")
                    for label in ("quantity", "qty", "quantidade", "qtd"):
                        if label in cells:
                            col_qty = next(i for i, c in enumerate(cells) if c == label)
                            break
                    past_header = True
                continue

            if col_desc is None or col_desc >= len(row):
                continue
            desc = str(row[col_desc]).strip() if row[col_desc] else ""
            if not desc or desc.lower() == "none":
                continue

            qty_raw = (row[col_qty] if col_qty is not None and col_qty < len(row)
                       and row[col_qty] is not None else None)
            try:
                qty = max(1, int(float(str(qty_raw)))) if qty_raw is not None else 1
            except (ValueError, TypeError):
                qty = 1

            existing = conn.execute(
                "SELECT id FROM item_tipo WHERE nome = ?", (desc,)
            ).fetchone()
            if existing:
                tipo_id = existing["id"]
            else:
                cur = conn.execute(
                    "INSERT INTO item_tipo (nome, criado_em) VALUES (?, ?)",
                    (desc, now_brt())
                )
                tipo_id = cur.lastrowid
                tipos_criados += 1

            itens.append({
                "item_tipo_id": tipo_id,
                "quantidade_exigida": qty,
                "obrigatorio": True,
                "componente_codigo": None,
                "requer_serial": False,
            })

    wb.close()

    if not past_header:
        raise ValueError("Cabeçalho 'Description' não encontrado na planilha.")
    if not itens:
        raise ValueError("Nenhum item válido encontrado na planilha.")

    template_id = criar_template(nome, cliente, criado_por, itens)
    return template_id, {"itens_adicionados": len(itens), "tipos_criados": tipos_criados}
