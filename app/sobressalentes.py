"""Pacotes de sobressalentes — o "kit coringa".

Funciona como um kit (etiqueta com QR, escaneou e vê o que tem dentro, baixa o
estoque, deixa histórico), mas sem veículo e sem modelo: quem monta escolhe os
itens e as quantidades, e o pacote é só pra um cliente.

O estoque é descontado NA CRIAÇÃO do pacote, item a item, pela mesma função do
envio de sobressalente de sempre (app.estoque) — o mesmo bloqueio por saldo
insuficiente, o mesmo movimento 'sobressalente' com o cliente, agora também
ligado ao pacote. Por isso os relatórios e o histórico de estoque que já
existiam continuam valendo, e cada baixa aponta pra o pacote que a causou.

A linha do tempo (sobressalente_pacote_evento) é só de registro: criação, cada
vez que a etiqueta foi gerada e cada observação adicionada depois. Nada aqui é
sobrescrito: a observação original continua como foi escrita, e corrigir uma
observação é adicionar outra — com quem, data e hora de cada uma.
"""

from database import db, now_brt
import app.estoque as estoque_mod

EVENTO_TEXTO = {
    "criado": "Pacote criado",
    "etiqueta": "Etiqueta gerada",
    "observacao": "Observação adicionada",
}

LIMITE_OBSERVACAO = 1000


def rotulo(pacote_id: int) -> str:
    """"SOB-0007" — derivado do id, nunca guardado."""
    return f"SOB-{pacote_id:04d}"


def _evento(conn, pacote_id: int, tipo: str, conteudo: str = "",
            usuario_id: int | None = None, estoque_id: int | None = None) -> None:
    conn.execute(
        "INSERT INTO sobressalente_pacote_evento (pacote_id, tipo, conteudo, usuario_id, criado_em, estoque_id) "
        "VALUES (?, ?, ?, ?, ?, ?)", (pacote_id, tipo, conteudo, usuario_id, now_brt(), estoque_id))


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


def adicionar_observacao(pacote_id: int, texto: str, usuario_id: int | None,
                         estoque_id: int | None = None) -> str:
    """Nova observação no pacote (ou num item dele). Devolve a quem se refere
    ("Pacote inteiro" ou o nome do item). ValueError se não puder gravar."""
    texto = (texto or "").strip()
    if not texto:
        raise ValueError("Escreva a observação.")
    if len(texto) > LIMITE_OBSERVACAO:
        raise ValueError(f"A observação pode ter até {LIMITE_OBSERVACAO} caracteres.")
    with db() as conn:
        if not conn.execute("SELECT 1 FROM sobressalente_pacote WHERE id = ?", (pacote_id,)).fetchone():
            raise ValueError("Pacote não encontrado.")
        alvo = "Pacote inteiro"
        if estoque_id is not None:
            item = conn.execute(
                "SELECT it.nome FROM sobressalente_pacote_item i JOIN estoque e ON e.id = i.estoque_id "
                "JOIN item_tipo it ON it.id = e.item_tipo_id WHERE i.pacote_id = ? AND i.estoque_id = ?",
                (pacote_id, estoque_id)).fetchone()
            if not item:
                raise ValueError("Este item não faz parte do pacote.")
            alvo = item["nome"]
        _evento(conn, pacote_id, "observacao", texto, usuario_id, estoque_id)
    return alvo


def _eventos_de(conn, pacote_ids: list[int], somente_tipo: str = "") -> list[dict]:
    """Eventos com autor e o item a que se referem, mais novos primeiro."""
    if not pacote_ids:
        return []
    marcas = ",".join("?" * len(pacote_ids))
    params: list = list(pacote_ids)
    filtro = ""
    if somente_tipo:
        filtro = " AND ev.tipo = ?"
        params.append(somente_tipo)
    saida = []
    for ev in conn.execute(
            "SELECT ev.*, u.nome AS usuario_nome, it.nome AS item_nome FROM sobressalente_pacote_evento ev "
            "LEFT JOIN users u ON u.id = ev.usuario_id "
            "LEFT JOIN estoque e ON e.id = ev.estoque_id "
            "LEFT JOIN item_tipo it ON it.id = e.item_tipo_id "
            f"WHERE ev.pacote_id IN ({marcas}){filtro} ORDER BY ev.criado_em DESC, ev.id DESC", params).fetchall():
        d = dict(ev)
        d["tipo_texto"] = EVENTO_TEXTO.get(d["tipo"], d["tipo"])
        d["alvo_texto"] = (d["item_nome"] or "Pacote inteiro") if d["tipo"] == "observacao" else ""
        saida.append(d)
    return saida


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
        eventos = _eventos_de(conn, [pacote_id])
    d = dict(p)
    d["rotulo"] = rotulo(pacote_id)
    d["itens"] = [dict(i) for i in itens]
    d["total_unidades"] = sum(i["quantidade"] for i in d["itens"])
    d["eventos"] = eventos
    d["observacoes"] = [e for e in eventos if e["tipo"] == "observacao"]
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


def envios_relatorio(data_ini: str = "", data_fim: str = "", cliente=None,
                     busca: str = "") -> list[dict]:
    """Relatório de Sobressalentes organizado POR ENVIO: cada pacote (SOB-0001)
    com os itens que ele levou, do mais recente pro mais antigo.

    Vem das baixas de estoque tipo 'sobressalente' — a mesma fonte do relatório
    de sempre, então nada some. A baixa que não tem pacote (envio avulso, de
    antes dos pacotes) vira um envio "Avulso", juntando as baixas do mesmo
    minuto, cliente e pessoa — foi um envio só, feito de uma vez."""
    rows = estoque_mod.listar_sobressalentes(data_ini, data_fim, cliente or [])
    pacotes: dict = {}
    with db() as conn:
        ids = {r["pacote_id"] for r in rows if r.get("pacote_id")}
        if ids:
            marcas = ",".join("?" * len(ids))
            for p in conn.execute(
                    f"SELECT id, nome, observacao FROM sobressalente_pacote WHERE id IN ({marcas})",
                    sorted(ids)).fetchall():
                pacotes[p["id"]] = dict(p)
            for ev in _eventos_de(conn, sorted(ids), "observacao"):
                pacotes[ev["pacote_id"]].setdefault("adicionadas", []).append(ev)
    envios: dict = {}
    for r in rows:
        pid = r.get("pacote_id")
        chave = ("p", pid) if pid else ("a", (r["criado_em"] or "")[:16], r["cliente"], r["criado_por"])
        e = envios.get(chave)
        if e is None:
            info = pacotes.get(pid) or {}
            e = envios[chave] = {
                "pacote_id": pid,
                "rotulo": rotulo(pid) if pid else "Avulso",
                "nome": info.get("nome") or "",
                "observacao": info.get("observacao") or ("" if pid else (r["observacao"] or "")),
                "adicionadas": info.get("adicionadas") or [],
                "cliente": r["cliente"] or "",
                "criado_em": r["criado_em"] or "",
                "operador_nome": r["operador_nome"] or "",
                "itens": [], "total_unidades": 0,
            }
        e["itens"].append({"tipo_nome": r["tipo_nome"], "codigo_barra": r["codigo_barra"],
                           "quantidade": r["quantidade"], "observacao": r["observacao"] or ""})
        e["total_unidades"] += r["quantidade"] or 0
    lista = sorted(envios.values(), key=lambda e: (e["criado_em"], e["pacote_id"] or 0), reverse=True)
    for e in lista:
        e["itens"].sort(key=lambda i: i["tipo_nome"] or "")
        e["itens_texto"] = ", ".join(f"{i['tipo_nome']} ×{i['quantidade']}" for i in e["itens"])
    termo = (busca or "").strip().lower()
    if termo:
        lista = [e for e in lista if termo in " ".join(
            [e["rotulo"], e["nome"], e["cliente"], e["operador_nome"], e["observacao"],
             e["itens_texto"]] + [i["codigo_barra"] or "" for i in e["itens"]]
            + [o["conteudo"] for o in e["adicionadas"]]).lower()]
    return lista
