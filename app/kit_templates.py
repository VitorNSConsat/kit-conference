from database import db


def listar_templates_ativos():
    """Lista todos os templates de kits ativos."""
    with db() as conn:
        rows = conn.execute(
            "SELECT id, nome, cliente, versao FROM kit_template WHERE ativo = 1 ORDER BY nome ASC"
        ).fetchall()
    return [dict(row) for row in rows]
