import sqlite3

from database import db, now_brt


def listar() -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT id, nome, criado_em FROM clientes ORDER BY nome"
        ).fetchall()
    return [dict(r) for r in rows]


def buscar(cliente_id: int) -> dict | None:
    with db() as conn:
        row = conn.execute(
            "SELECT id, nome, criado_em FROM clientes WHERE id = ?", (cliente_id,)
        ).fetchone()
    return dict(row) if row else None


def panorama(nome: str) -> dict | None:
    """Tudo que está amarrado a um cliente, numa consulta por bloco.

    Junta o que hoje só dá pra saber abrindo três telas: quais garagens ele
    usa (derivadas dos veículos, não de um cadastro à parte — garagem e
    cliente não têm vínculo próprio, o veículo é que liga os dois), quantos
    veículos tem em cada uma, onde esses veículos estão no fluxo, e quais
    kits/modelos ele usa."""
    nome = (nome or "").strip()
    if not nome:
        return None
    with db() as conn:
        existe = conn.execute("SELECT * FROM clientes WHERE nome = ?", (nome,)).fetchone()
        if not existe:
            return None
        garagens = [dict(r) for r in conn.execute("""
            SELECT COALESCE(NULLIF(TRIM(garagem), ''), '(sem garagem)') AS garagem,
                   COUNT(*) AS veiculos,
                   SUM(CASE WHEN TRIM(COALESCE(modelo,'')) = '' THEN 1 ELSE 0 END) AS sem_modelo
            FROM veiculos WHERE ativo = 1 AND cliente = ?
            GROUP BY garagem ORDER BY veiculos DESC, garagem
        """, (nome,)).fetchall()]
        modelos = [dict(r) for r in conn.execute("""
            SELECT COALESCE(NULLIF(TRIM(modelo), ''), '(sem kit definido)') AS modelo,
                   COUNT(*) AS veiculos
            FROM veiculos WHERE ativo = 1 AND cliente = ?
            GROUP BY modelo ORDER BY veiculos DESC, modelo
        """, (nome,)).fetchall()]
        totais = dict(conn.execute("""
            SELECT COUNT(*) AS veiculos,
                   SUM(CASE WHEN TRIM(COALESCE(garagem,'')) = '' THEN 1 ELSE 0 END) AS sem_garagem,
                   SUM(CASE WHEN TRIM(COALESCE(modelo,'')) = '' THEN 1 ELSE 0 END) AS sem_modelo
            FROM veiculos WHERE ativo = 1 AND cliente = ?
        """, (nome,)).fetchone())
        kits = [dict(r) for r in conn.execute("""
            SELECT kt.id, kt.nome, kt.versao, kt.ativo, kt.tipo,
                   (SELECT COUNT(*) FROM kit_record kr WHERE kr.kit_template_id = kt.id) AS montados
            FROM kit_template kt WHERE kt.cliente = ? ORDER BY kt.ativo DESC, kt.nome
        """, (nome,)).fetchall()]
        producao = dict(conn.execute("""
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN kr.status_producao = 'produzido' THEN 1 ELSE 0 END) AS produzido,
                   SUM(CASE WHEN kr.status_producao = 'transito' THEN 1 ELSE 0 END) AS transito,
                   SUM(CASE WHEN kr.status_producao IN ('cliente_instalando','cliente_concluido')
                            THEN 1 ELSE 0 END) AS no_cliente
            FROM kit_record kr JOIN kit_template kt ON kt.id = kr.kit_template_id
            WHERE kt.cliente = ?
        """, (nome,)).fetchone())
    return {"cliente": dict(existe), "garagens": garagens, "modelos": modelos,
            "totais": totais, "kits": kits, "producao": producao}


def deletar(cliente_id: int):
    with db() as conn:
        conn.execute("DELETE FROM clientes WHERE id = ?", (cliente_id,))


def criar(nome: str) -> int | None:
    nome = nome.strip()
    if not nome:
        return None
    try:
        with db() as conn:
            cur = conn.execute(
                "INSERT INTO clientes (nome, criado_em) VALUES (?, ?)",
                (nome, now_brt())
            )
            return cur.lastrowid
    except sqlite3.IntegrityError:
        return None
