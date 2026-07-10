from database import db


def start_session(kit_template_id: int, operador_id: int) -> int:
    """
    Cria uma nova sessão de scan e retorna o id.

    Args:
        kit_template_id: ID do template de kit
        operador_id: ID do usuário (operador) que iniciou a sessão

    Returns:
        ID da nova sessão criada
    """
    with db() as conn:
        # Pega a versão atual do template
        template = conn.execute(
            "SELECT versao FROM kit_template WHERE id = ?", (kit_template_id,)
        ).fetchone()

        if not template:
            raise ValueError(f"Template com id {kit_template_id} não encontrado")

        versao = template["versao"]

        # Cria a nova sessão
        cursor = conn.execute(
            """INSERT INTO scan_session
               (kit_template_id, kit_template_versao, operador_id, status)
               VALUES (?, ?, ?, 'em_andamento')""",
            (kit_template_id, versao, operador_id)
        )

        return cursor.lastrowid
