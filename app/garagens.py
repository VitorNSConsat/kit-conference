import sqlite3

from database import db, now_brt


def listar() -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT id, nome, criado_em FROM garagens ORDER BY nome"
        ).fetchall()
    return [dict(r) for r in rows]


def buscar(garagem_id: int) -> dict | None:
    with db() as conn:
        row = conn.execute(
            "SELECT id, nome, criado_em FROM garagens WHERE id = ?", (garagem_id,)
        ).fetchone()
    return dict(row) if row else None


def panorama(nome: str) -> dict | None:
    """Tudo que está amarrado a uma garagem.

    O espelho de clientes.panorama(): garagem e cliente não têm vínculo
    próprio no banco — quem liga os dois é o VEÍCULO. Por isso a lista de
    clientes desta garagem é derivada dos veículos que estão nela, e é essa
    a informação que faltava pra saber quem atende o quê."""
    nome = (nome or "").strip()
    if not nome:
        return None
    with db() as conn:
        existe = conn.execute("SELECT * FROM garagens WHERE nome = ?", (nome,)).fetchone()
        if not existe:
            return None
        clientes = [dict(r) for r in conn.execute("""
            SELECT cliente, COUNT(*) AS veiculos,
                   SUM(CASE WHEN TRIM(COALESCE(modelo,'')) = '' THEN 1 ELSE 0 END) AS sem_modelo
            FROM veiculos WHERE ativo = 1 AND UPPER(TRIM(garagem)) = UPPER(?)
            GROUP BY cliente ORDER BY veiculos DESC, cliente
        """, (nome,)).fetchall()]
        modelos = [dict(r) for r in conn.execute("""
            SELECT COALESCE(NULLIF(TRIM(modelo), ''), '(sem kit definido)') AS modelo,
                   COUNT(*) AS veiculos
            FROM veiculos WHERE ativo = 1 AND UPPER(TRIM(garagem)) = UPPER(?)
            GROUP BY modelo ORDER BY veiculos DESC, modelo
        """, (nome,)).fetchall()]
        totais = dict(conn.execute("""
            SELECT COUNT(*) AS veiculos,
                   SUM(CASE WHEN TRIM(COALESCE(modelo,'')) = '' THEN 1 ELSE 0 END) AS sem_modelo
            FROM veiculos WHERE ativo = 1 AND UPPER(TRIM(garagem)) = UPPER(?)
        """, (nome,)).fetchone())
        producao = dict(conn.execute("""
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN kr.status_producao = 'produzido' THEN 1 ELSE 0 END) AS produzido,
                   SUM(CASE WHEN kr.status_producao = 'transito' THEN 1 ELSE 0 END) AS transito,
                   SUM(CASE WHEN kr.status_producao IN ('cliente_instalando','cliente_concluido')
                            THEN 1 ELSE 0 END) AS no_cliente
            FROM kit_record kr WHERE UPPER(TRIM(COALESCE(kr.garagem,''))) = UPPER(?)
        """, (nome,)).fetchone())
    return {"garagem": dict(existe), "clientes": clientes, "modelos": modelos,
            "totais": totais, "producao": producao}


def deletar(garagem_id: int):
    with db() as conn:
        conn.execute("DELETE FROM garagens WHERE id = ?", (garagem_id,))


def criar(nome: str) -> int | None:
    nome = nome.strip().upper()
    if not nome:
        return None
    try:
        with db() as conn:
            cur = conn.execute(
                "INSERT INTO garagens (nome, criado_em) VALUES (?, ?)",
                (nome, now_brt())
            )
            return cur.lastrowid
    except sqlite3.IntegrityError:
        return None
