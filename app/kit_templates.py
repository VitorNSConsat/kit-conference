from database import db


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
            "SELECT ki.*, COALESCE(it.nome, '[Tipo removido]') AS descricao "
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
