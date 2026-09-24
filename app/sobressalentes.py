"""Pacotes de sobressalentes — o "kit coringa".

Funciona como um kit (etiqueta com QR, escaneou e vê o que tem dentro, baixa o
estoque, deixa histórico), mas sem veículo e sem modelo: quem monta escolhe os
itens e as quantidades, e o pacote é só pra um cliente.

O estoque é descontado NA CRIAÇÃO do pacote, item a item, pela mesma função do
envio de sobressalente de sempre (app.estoque) — o mesmo bloqueio por saldo
insuficiente, o mesmo movimento 'sobressalente' com o cliente, agora também
ligado ao pacote. Por isso os relatórios e o histórico de estoque que já
existiam continuam valendo, e cada baixa aponta pra o pacote que a causou.

A linha do tempo (sobressalente_pacote_evento) é só de registro: criação e
cada vez que a etiqueta foi gerada. Nada aqui é sobrescrito.
"""

from database import db, now_brt
import app.estoque as estoque_mod

EVENTO_TEXTO = {
    "criado": "Pacote criado",
    "etiqueta": "Etiqueta gerada",
}


def rotulo(pacote_id: int) -> str:
    """"SOB-0007" — derivado do id, nunca guardado."""
    return f"SOB-{pacote_id:04d}"


def _evento(conn, pacote_id: int, tipo: str, conteudo: str = "",
            usuario_id: int | None = None) -> None:
    conn.execute(
        "INSERT INTO sobressalente_pacote_evento (pacote_id, tipo, conteudo, usuario_id, criado_em) "
        "VALUES (?, ?, ?, ?, ?)", (pacote_id, tipo, conteudo, usuario_id, now_brt()))


def criar(linhas: list[dict], cliente: str, criado_por: int, nome: str = "",
          observacao: str = "") -> int:
    """Monta o pacote e desconta o estoque. TUDO OU NADA: se faltar saldo em
    qualquer linha, nada é descontado e nenhum pacote nasce (ValueError)."""
    cliente = (cliente or "").strip()
    if not cliente:
        raise ValueError("Informe o cliente.")
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO sobressalente_pacote (cliente, nome, observacao, criado_em, criado_por) "
            "VALUES (?, ?, ?, ?, ?)",
            (cliente, (nome or "").strip(), (observacao or "").strip(), now_brt(), criado_por))
        pacote_id = cur.lastrowid
    try:
        # Fora do `with` acima: a função de estoque abre a própria conexão, e
        # aninhar duas trava o SQLite.
        resultado = estoque_mod.registrar_sobressalentes_em_lote(
            linhas, cliente, criado_por, pacote_id=pacote_id, rotulo_pacote=rotulo(pacote_id))
    except Exception:
        with db() as conn:
            conn.execute("DELETE FROM sobressalente_pacote WHERE id = ?", (pacote_id,))
        raise
    with db() as conn:
        conn.executemany(
            "INSERT INTO sobressalente_pacote_item (pacote_id, estoque_id, quantidade, observacao) "
            "VALUES (?, ?, ?, ?)",
            [(pacote_id, e["estoque_id"], e["quantidade"], e["observacao"])
             for e in resultado["enviados"]])
        _evento(conn, pacote_id, "criado",
                f"{resultado['itens']} item(ns), {resultado['unidades']} unidade(s) — estoque descontado.",
                criado_por)
    return pacote_id


def registrar_etiqueta(pacote_id: int, usuario_id: int | None) -> None:
    with db() as conn:
        _evento(conn, pacote_id, "etiqueta", "", usuario_id)


def buscar(pacote_id: int) -> dict | None:
    with db() as conn:
        p = conn.execute(
            "SELECT p.*, u.nome AS criado_por_nome FROM sobressalente_pacote p "
            "LEFT JOIN users u ON u.id = p.criado_por WHERE p.id = ?", (pacote_id,)).fetchone()
        if p is None:
            return None
        itens = conn.execute(
            "SELECT i.estoque_id, i.quantidade, i.observacao, e.codigo_barra, it.nome AS tipo_nome "
            "FROM sobressalente_pacote_item i "
            "JOIN estoque e ON e.id = i.estoque_id "
            "JOIN item_tipo it ON it.id = e.item_tipo_id "
            "WHERE i.pacote_id = ? ORDER BY it.nome", (pacote_id,)).fetchall()
        eventos = conn.execute(
            "SELECT ev.*, u.nome AS usuario_nome FROM sobressalente_pacote_evento ev "
            "LEFT JOIN users u ON u.id = ev.usuario_id "
            "WHERE ev.pacote_id = ? ORDER BY ev.criado_em DESC, ev.id DESC", (pacote_id,)).fetchall()
    d = dict(p)
    d["rotulo"] = rotulo(pacote_id)
    d["itens"] = [dict(i) for i in itens]
    d["total_unidades"] = sum(i["quantidade"] for i in d["itens"])
    d["eventos"] = []
    for ev in eventos:
        e = dict(ev)
        e["tipo_texto"] = EVENTO_TEXTO.get(e["tipo"], e["tipo"])
        d["eventos"].append(e)
    return d


def listar(cliente="", data_ini: str = "", data_fim: str = "") -> list[dict]:
    """Pacotes (mais recente primeiro) já com o resumo dos itens — é a lista da aba
    Sobressalentes. `cliente` aceita um nome ou uma lista (filtro de múltipla escolha);
    vazio = todos. Cada pacote traz `itens_texto` ("Fuse 5A ×2, Conector ×1"), usado na
    busca e na coluna Itens."""
    import app.datas as datas_mod
    import app.filtros as filtros_mod
    sql = ("SELECT p.id, p.cliente, p.nome, p.observacao, p.criado_em, u.nome AS criado_por_nome, "
           "       COALESCE(SUM(i.quantidade), 0) AS total_unidades, COUNT(i.id) AS total_itens "
           "FROM sobressalente_pacote p "
           "LEFT JOIN users u ON u.id = p.criado_por "
           "LEFT JOIN sobressalente_pacote_item i ON i.pacote_id = p.id "
           "WHERE 1 = 1")
    params: list = []
    clientes = filtros_mod.lista(cliente if isinstance(cliente, (list, tuple)) else [cliente])
    sql_c, p_c = filtros_mod.em("p.cliente", clientes)
    sql += sql_c
    params += p_c
    sql_data, p_data = datas_mod.clausula("p.criado_em", data_ini, data_fim)
    sql += sql_data
    params += p_data
    sql += " GROUP BY p.id ORDER BY p.criado_em DESC, p.id DESC"
    with db() as conn:
        pacotes = [dict(r) for r in conn.execute(sql, params).fetchall()]
        nomes: dict[int, list[str]] = {}
        if pacotes:
            for r in conn.execute(
                    "SELECT i.pacote_id, it.nome AS tipo_nome, i.quantidade "
                    "FROM sobressalente_pacote_item i JOIN estoque e ON e.id = i.estoque_id "
                    "JOIN item_tipo it ON it.id = e.item_tipo_id ORDER BY it.nome").fetchall():
                nomes.setdefault(r["pacote_id"], []).append(f"{r['tipo_nome']} ×{r['quantidade']}")
    for p in pacotes:
        p["rotulo"] = rotulo(p["id"])
        p["itens_texto"] = ", ".join(nomes.get(p["id"], []))
    return pacotes


def clientes_com_pacote() -> list[str]:
    """Clientes que já têm pacote — as opções do filtro."""
    with db() as conn:
        return [r[0] for r in conn.execute(
            "SELECT DISTINCT cliente FROM sobressalente_pacote ORDER BY cliente").fetchall()]
