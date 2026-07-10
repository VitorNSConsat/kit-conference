import json
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
            "SELECT ki.*, i.descricao, i.unidade, i.controla_serial "
            "FROM kit_template_items ki "
            "JOIN item_master i ON i.codigo_barra = ki.codigo_barra "
            "WHERE ki.kit_template_id = ?",
            (template_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def criar_template(nome: str, cliente: str, criado_por: int,
                   itens: list[dict]) -> int:
    """itens: [{'codigo_barra': str, 'quantidade_exigida': int, 'obrigatorio': bool}]"""
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
                "(kit_template_id, codigo_barra, quantidade_exigida, obrigatorio) "
                "VALUES (?, ?, ?, ?)",
                (template_id, item["codigo_barra"],
                 item["quantidade_exigida"], int(item.get("obrigatorio", True)))
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
                "(kit_template_id, codigo_barra, quantidade_exigida, obrigatorio) "
                "VALUES (?, ?, ?, ?)",
                (novo_id, item["codigo_barra"],
                 item["quantidade_exigida"], item["obrigatorio"])
            )
    return novo_id


def toggle_ativo(template_id: int):
    with db() as conn:
        conn.execute(
            "UPDATE kit_template SET ativo = NOT ativo WHERE id = ?", (template_id,)
        )
