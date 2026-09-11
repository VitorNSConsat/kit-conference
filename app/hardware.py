"""Gestão de Hardware: ocorrências de defeito/RMA de equipamento, do registro
até a solução final — Brasil, Suécia e fabricante acompanhados separadamente.

Cadastro de produto é próprio (hardware_produto), separado do item_tipo do
estoque de propósito: são realidades diferentes (equipamento completo com
defeito x peça de reposição do estoque).

A "última ação de cada frente" (Brasil/Suécia/Fabricante) NÃO fica cacheada
na própria ocorrência — é calculada a partir de hardware_ocorrencia_evento
(o evento mais recente de cada tipo acao_*), o mesmo padrão usado pra coluna
Remessa em app/remessas.py (mapa_por_referencia). Evita manter um cache
sincronizado a cada evento novo, que divergiria silenciosamente se um POST
falhasse no meio do caminho.
"""

import os
import re
import unicodedata
import uuid
from datetime import datetime, timedelta

from database import db, now_brt

# Status, prioridade, categoria de defeito e localização são catálogos
# editáveis no banco (hardware_*_opcao) — ver seção "Catálogos" abaixo.
# "Aguardando de" continua fixo: é a estrutura das 3 frentes de ação
# (Brasil/Suécia/Fabricante) mais "cliente", amarrada ao resto do código
# (AREAS_ACAO, as abas de ação), não um simples rótulo de lista.
AGUARDANDO_DE = (
    ("brasil",     "Brasil"),
    ("suecia",     "Suécia"),
    ("fabricante", "Fabricante"),
    ("cliente",    "Cliente"),
)
AGUARDANDO_TEXTO = dict(AGUARDANDO_DE)

RESULTADOS_FINAIS = (
    "Reparado", "Substituído", "Atualizado/Reconfigurado",
    "Defeito não reproduzido", "Sem defeito identificado",
    "Devolvido ao fornecedor", "RMA aprovado", "RMA recusado",
    "Descartado", "Devolvido ao estoque", "Outro",
)

AREAS_ACAO = ("brasil", "suecia", "fabricante")
AREA_TEXTO = {"brasil": "Brasil", "suecia": "Suécia", "fabricante": "Fabricante"}

EVENTO_TEXTO = {
    "criacao":         "Ocorrência criada",
    "atualizacao":     "Atualização",
    "acao_brasil":     "Ação — Brasil",
    "acao_suecia":     "Ação — Suécia",
    "acao_fabricante": "Ação — Fabricante",
    "status":          "Status alterado",
    "responsavel":     "Responsável alterado",
    "prioridade":      "Prioridade alterada",
    "reabertura":      "Reaberta",
    "resultado_final": "Resultado final registrado",
    "anexo":           "Arquivo anexado",
}

# Extensão E content-type declarado são checados juntos — um .zip renomeado
# pra .png (ou vice-versa) não deve passar. Sem vídeo, por decisão explícita.
EXTENSOES_ANEXO_PERMITIDAS = (".jpg", ".jpeg", ".png", ".pdf", ".txt", ".csv", ".xlsx", ".zip")
MIME_POR_EXTENSAO = {
    ".jpg":  ("image/jpeg",),
    ".jpeg": ("image/jpeg",),
    ".png":  ("image/png",),
    ".pdf":  ("application/pdf",),
    ".txt":  ("text/plain",),
    ".csv":  ("text/csv", "application/vnd.ms-excel", "text/plain"),
    ".xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",),
    ".zip":  ("application/zip", "application/x-zip-compressed", "application/octet-stream"),
}
MAX_ANEXO_BYTES = int(os.getenv("HARDWARE_MAX_ANEXO_MB", "15")) * 1024 * 1024
PASTA_UPLOADS = os.path.join("uploads", "hardware")
SEM_ATUALIZACAO_DIAS_PADRAO = 7


def rotulo(ocorrencia_id: int) -> str:
    """"HW-0001" — derivado do id, nunca guardado (nada pra congelar aqui,
    diferente do nome de produto/cliente, que pode mudar depois)."""
    return f"HW-{ocorrencia_id:04d}"


# ── Catálogos editáveis (status, prioridade, categoria de defeito, localização) ──
# Os quatro campos de lista da ocorrência viraram tabelas editáveis (era lista
# fixa em Python). "chave" é o valor gravado em hardware_ocorrencia — nunca
# muda depois de criada, só o "nome" (rótulo exibido) pode ser renomeado, pra
# ocorrência antiga continuar apontando pro registro certo mesmo depois de um
# "editar". Categoria de defeito é a exceção: sempre foi uma lista de texto
# livre sem chave própria, então o nome RENOMEIA IGUAL À CHAVE — editar
# propaga pras ocorrências já gravadas com o nome antigo (ver editar_opcao).
_CATALOGOS = {
    "status":      {"tabela": "hardware_status_opcao",      "tem_chave": True,  "tem_cor": True,  "tem_terminal": True},
    "prioridade":  {"tabela": "hardware_prioridade_opcao",  "tem_chave": True,  "tem_cor": True,  "tem_terminal": False},
    "categoria":   {"tabela": "hardware_categoria_opcao",   "tem_chave": False, "tem_cor": False, "tem_terminal": False},
    "localizacao": {"tabela": "hardware_localizacao_opcao", "tem_chave": True,  "tem_cor": False, "tem_terminal": False},
}


def _slugify(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    texto = re.sub(r"[^a-zA-Z0-9]+", "_", texto).strip("_").lower()
    return texto or "opcao"


def _catalogo(tipo: str) -> dict:
    cfg = _CATALOGOS.get(tipo)
    if not cfg:
        raise ValueError(f"Catálogo desconhecido: {tipo}")
    return cfg


def listar_opcoes(tipo: str, incluir_inativas: bool = False) -> list[dict]:
    cfg = _catalogo(tipo)
    sql = f"SELECT * FROM {cfg['tabela']}"
    if not incluir_inativas:
        sql += " WHERE ativo = 1"
    sql += " ORDER BY ordem, nome"
    with db() as conn:
        rows = conn.execute(sql).fetchall()
    return [dict(r) for r in rows]


def opcoes_mapa(tipo: str, incluir_inativas: bool = True) -> dict[str, dict]:
    """{chave-ou-nome: linha} — uma consulta só, reaproveitada por todos os
    itens de uma listagem (mesmo padrão de ultimas_acoes: monta o mapa uma
    vez fora do loop, nunca uma query por linha)."""
    cfg = _catalogo(tipo)
    campo = "chave" if cfg["tem_chave"] else "nome"
    return {r[campo]: r for r in listar_opcoes(tipo, incluir_inativas=incluir_inativas)}


def criar_opcao(tipo: str, nome: str, cor: str = "", eh_terminal: bool = False) -> int:
    cfg = _catalogo(tipo)
    nome = (nome or "").strip()
    if not nome:
        raise ValueError("Informe o nome.")
    with db() as conn:
        maior_ordem = conn.execute(f"SELECT COALESCE(MAX(ordem), -1) FROM {cfg['tabela']}").fetchone()[0]
        campos = ["nome", "ordem", "criado_em"]
        valores = [nome, maior_ordem + 1, now_brt()]
        if cfg["tem_chave"]:
            existentes = {r["chave"] for r in conn.execute(f"SELECT chave FROM {cfg['tabela']}").fetchall()}
            base = _slugify(nome)
            chave = base
            i = 2
            while chave in existentes:
                chave = f"{base}_{i}"
                i += 1
            campos.insert(0, "chave")
            valores.insert(0, chave)
        if cfg["tem_cor"]:
            campos.append("cor")
            valores.append(cor.strip() if (cor or "").strip() else "#246b84")
        if cfg["tem_terminal"]:
            campos.append("eh_terminal")
            valores.append(1 if eh_terminal else 0)
        try:
            cur = conn.execute(
                f"INSERT INTO {cfg['tabela']} ({', '.join(campos)}) VALUES ({', '.join('?' * len(campos))})",
                valores)
        except Exception:
            raise ValueError(f'Já existe um item chamado "{nome}".')
    return cur.lastrowid


def editar_opcao(tipo: str, opcao_id: int, nome: str, cor: str = "", eh_terminal: bool | None = None) -> None:
    cfg = _catalogo(tipo)
    nome = (nome or "").strip()
    if not nome:
        raise ValueError("Informe o nome.")
    campos = ["nome = ?"]
    valores = [nome]
    if cfg["tem_cor"] and (cor or "").strip():
        campos.append("cor = ?")
        valores.append(cor.strip())
    if cfg["tem_terminal"] and eh_terminal is not None:
        campos.append("eh_terminal = ?")
        valores.append(1 if eh_terminal else 0)
    with db() as conn:
        nome_antigo = None
        if tipo == "categoria":
            row = conn.execute(f"SELECT nome FROM {cfg['tabela']} WHERE id = ?", (opcao_id,)).fetchone()
            nome_antigo = row["nome"] if row else None
        try:
            conn.execute(f"UPDATE {cfg['tabela']} SET {', '.join(campos)} WHERE id = ?", (*valores, opcao_id))
        except Exception:
            raise ValueError(f'Já existe um item chamado "{nome}".')
        # Categoria não tem chave própria — o nome É o valor gravado na
        # ocorrência, então renomear precisa propagar pro que já foi salvo,
        # senão a ocorrência antiga fica com um rótulo que não existe mais
        # em lugar nenhum da tela.
        if nome_antigo and nome_antigo != nome:
            conn.execute("UPDATE hardware_ocorrencia SET categoria_defeito = ? WHERE categoria_defeito = ?",
                         (nome, nome_antigo))


def desativar_opcao(tipo: str, opcao_id: int) -> None:
    cfg = _catalogo(tipo)
    with db() as conn:
        row = conn.execute(f"SELECT sistema FROM {cfg['tabela']} WHERE id = ?", (opcao_id,)).fetchone()
        if row and row["sistema"]:
            raise ValueError("Este item é usado pelo sistema e não pode ser removido — só renomeado.")
        conn.execute(f"UPDATE {cfg['tabela']} SET ativo = 0 WHERE id = ?", (opcao_id,))


def reativar_opcao(tipo: str, opcao_id: int) -> None:
    cfg = _catalogo(tipo)
    with db() as conn:
        conn.execute(f"UPDATE {cfg['tabela']} SET ativo = 1 WHERE id = ?", (opcao_id,))


def status_terminais() -> tuple[str, ...]:
    with db() as conn:
        rows = conn.execute("SELECT chave FROM hardware_status_opcao WHERE eh_terminal = 1").fetchall()
    return tuple(r["chave"] for r in rows)


def status_chaves_andamento() -> list[str]:
    """Status ativos que não são "não iniciado" nem terminal — usado só pra
    montar o link de filtro do card "Em andamento" do dashboard."""
    with db() as conn:
        rows = conn.execute(
            "SELECT chave FROM hardware_status_opcao "
            "WHERE ativo = 1 AND eh_terminal = 0 AND chave != 'nao_iniciado'").fetchall()
    return [r["chave"] for r in rows]


def prioridade_padrao() -> str:
    with db() as conn:
        row = conn.execute("SELECT chave FROM hardware_prioridade_opcao WHERE sistema = 1 LIMIT 1").fetchone()
    return row["chave"] if row else "normal"


# ── Produto ──────────────────────────────────────────────────────────────────

def listar_produtos(incluir_inativos: bool = False) -> list[dict]:
    with db() as conn:
        sql = "SELECT * FROM hardware_produto"
        if not incluir_inativos:
            sql += " WHERE ativo = 1"
        sql += " ORDER BY nome"
        rows = conn.execute(sql).fetchall()
    return [dict(r) for r in rows]


def criar_produto(nome: str, fabricante: str = "", categoria: str = "") -> int:
    nome = (nome or "").strip()
    if not nome:
        raise ValueError("Informe o nome do produto.")
    with db() as conn:
        existente = conn.execute(
            "SELECT id FROM hardware_produto WHERE LOWER(TRIM(nome)) = LOWER(?)", (nome,)).fetchone()
        if existente:
            raise ValueError(f'Já existe um produto chamado "{nome}".')
        cur = conn.execute(
            "INSERT INTO hardware_produto (nome, fabricante, categoria, criado_em) VALUES (?, ?, ?, ?)",
            (nome, (fabricante or "").strip(), (categoria or "").strip(), now_brt()))
    return cur.lastrowid


def desativar_produto(produto_id: int) -> None:
    with db() as conn:
        conn.execute("UPDATE hardware_produto SET ativo = 0 WHERE id = ?", (produto_id,))


def reativar_produto(produto_id: int) -> None:
    with db() as conn:
        conn.execute("UPDATE hardware_produto SET ativo = 1 WHERE id = ?", (produto_id,))


# ── Consulta ─────────────────────────────────────────────────────────────────

_CAMPOS = (
    "ho.*, COALESCE(hp.nome, ho.produto_nome) AS produto_exibido, "
    "u.nome AS responsavel_nome, uc.nome AS criado_por_nome"
)


def _query_base() -> str:
    return (
        f"SELECT {_CAMPOS} FROM hardware_ocorrencia ho "
        "LEFT JOIN hardware_produto hp ON hp.id = ho.hardware_produto_id "
        "LEFT JOIN users u ON u.id = ho.responsavel_id "
        "LEFT JOIN users uc ON uc.id = ho.criado_por "
    )


def _dias_desde(data_texto: str | None, agora: datetime | None = None) -> int | None:
    if not data_texto:
        return None
    agora = agora or datetime.now()
    for fmt, tamanho in (("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%d", 10)):
        try:
            return max(0, (agora - datetime.strptime(data_texto[:tamanho], fmt)).days)
        except ValueError:
            continue
    return None


def ultimas_acoes(ocorrencia_ids: list[int] | None = None) -> dict[int, dict]:
    """{ocorrencia_id: {"brasil": {...}, "suecia": {...}, "fabricante": {...}}}
    com a AÇÃO MAIS RECENTE de cada frente — nunca todas, só a última, que é
    o que a tela de resumo e os cards precisam. O histórico completo por
    área vive em listar_eventos_area()."""
    sql = (
        "SELECT e.ocorrencia_id, e.tipo, e.conteudo, e.situacao, e.criado_em, u.nome AS usuario_nome "
        "FROM hardware_ocorrencia_evento e LEFT JOIN users u ON u.id = e.usuario_id "
        "WHERE e.tipo IN ('acao_brasil','acao_suecia','acao_fabricante') "
    )
    params: list = []
    if ocorrencia_ids:
        marcas = ",".join("?" * len(ocorrencia_ids))
        sql += f"AND e.ocorrencia_id IN ({marcas}) "
        params = list(ocorrencia_ids)
    sql += "ORDER BY e.criado_em DESC, e.id DESC"
    with db() as conn:
        rows = conn.execute(sql, params).fetchall()
    mapa: dict[int, dict] = {}
    for r in rows:
        area = r["tipo"].replace("acao_", "")
        bucket = mapa.setdefault(r["ocorrencia_id"], {})
        if area not in bucket:  # já vem ordenado DESC — o primeiro que aparece é o mais recente
            bucket[area] = {"conteudo": r["conteudo"], "situacao": r["situacao"],
                             "data": r["criado_em"], "usuario_nome": r["usuario_nome"]}
    return mapa


_COR_PADRAO = "#99a1ab"


def _com_calculos(itens: list[dict]) -> list[dict]:
    agora = datetime.now()
    mapa_acoes = ultimas_acoes([i["id"] for i in itens]) if itens else {}
    # incluir_inativas=True: uma ocorrência antiga com status/prioridade/
    # localização já desativada continua exibindo o rótulo/cor certos —
    # só some da lista de opções pra ocorrência NOVA (ver listar_opcoes()).
    mapa_status = opcoes_mapa("status")
    mapa_prioridade = opcoes_mapa("prioridade")
    mapa_localizacao = opcoes_mapa("localizacao")
    for it in itens:
        it["rotulo"] = rotulo(it["id"])
        st = mapa_status.get(it["status"])
        it["status_texto"] = st["nome"] if st else it["status"]
        it["status_cor"] = st["cor"] if st else _COR_PADRAO
        pr = mapa_prioridade.get(it["prioridade"])
        it["prioridade_texto"] = pr["nome"] if pr else it["prioridade"]
        it["prioridade_cor"] = pr["cor"] if pr else _COR_PADRAO
        lc = mapa_localizacao.get(it["localizacao"])
        it["localizacao_texto"] = lc["nome"] if lc else (it["localizacao"] or "")
        it["aguardando_texto"] = AGUARDANDO_TEXTO.get(it["aguardando_de"], "")
        it["acoes"] = mapa_acoes.get(it["id"], {})
        it["dias_em_aberto"] = _dias_desde(it["data_registro"], agora)
        it["dias_sem_atualizacao"] = _dias_desde(it["atualizado_em"], agora)
    return itens


def listar(filtros: dict | None = None) -> list[dict]:
    filtros = filtros or {}
    where = ["ho.ativo = 1"]
    params: list = []

    if filtros.get("status"):
        marcas = ",".join("?" * len(filtros["status"]))
        where.append(f"ho.status IN ({marcas})")
        params += filtros["status"]
    if filtros.get("prioridade"):
        marcas = ",".join("?" * len(filtros["prioridade"]))
        where.append(f"ho.prioridade IN ({marcas})")
        params += filtros["prioridade"]
    if filtros.get("cliente"):
        where.append("ho.cliente = ?")
        params.append(filtros["cliente"])
    if filtros.get("hardware_produto_id"):
        where.append("ho.hardware_produto_id = ?")
        params.append(filtros["hardware_produto_id"])
    if filtros.get("categoria_defeito"):
        where.append("ho.categoria_defeito = ?")
        params.append(filtros["categoria_defeito"])
    if filtros.get("responsavel_id"):
        where.append("ho.responsavel_id = ?")
        params.append(filtros["responsavel_id"])
    if filtros.get("aguardando_de"):
        marcas = ",".join("?" * len(filtros["aguardando_de"]))
        where.append(f"ho.aguardando_de IN ({marcas})")
        params += filtros["aguardando_de"]
    if filtros.get("busca"):
        termo = f"%{filtros['busca'].strip()}%"
        where.append("(ho.cliente LIKE ? OR ho.produto_nome LIKE ? OR ho.serial_patrimonio LIKE ? "
                      "OR ('HW-' || printf('%04d', ho.id)) LIKE ?)")
        params += [termo, termo, termo, termo]
    if filtros.get("registro_ini"):
        where.append("ho.data_registro >= ?")
        params.append(filtros["registro_ini"])
    if filtros.get("registro_fim"):
        where.append("ho.data_registro <= ?")
        params.append(filtros["registro_fim"] + " 23:59:59")
    if filtros.get("sem_atualizacao_dias"):
        limite = (datetime.now() - timedelta(days=int(filtros["sem_atualizacao_dias"]))).strftime("%Y-%m-%d %H:%M:%S")
        where.append("ho.atualizado_em <= ?")
        params.append(limite)
    if filtros.get("critico"):
        terminais = status_terminais()
        where.append(f"ho.prioridade = 'critica' AND ho.status NOT IN "
                      f"({','.join('?' * len(terminais))})")
        params += terminais
    if filtros.get("com_solucao") == "sim":
        where.append("ho.solucao_texto IS NOT NULL AND TRIM(ho.solucao_texto) <> ''")
    elif filtros.get("com_solucao") == "nao":
        where.append("(ho.solucao_texto IS NULL OR TRIM(ho.solucao_texto) = '')")

    sql = _query_base() + " WHERE " + " AND ".join(where) + " ORDER BY ho.atualizado_em DESC"
    with db() as conn:
        rows = conn.execute(sql, params).fetchall()
    return _com_calculos([dict(r) for r in rows])


def resumo() -> dict:
    base = "FROM hardware_ocorrencia ho WHERE ho.ativo = 1"
    terminais = status_terminais()
    marcas_terminais = ",".join("?" * len(terminais))
    limite_sem_atualizacao = (
        datetime.now() - timedelta(days=SEM_ATUALIZACAO_DIAS_PADRAO)
    ).strftime("%Y-%m-%d %H:%M:%S")
    with db() as conn:
        total = conn.execute(f"SELECT COUNT(*) {base}").fetchone()[0]
        por_status = conn.execute(f"SELECT status, COUNT(*) c {base} GROUP BY status").fetchall()
        por_aguardando = conn.execute(
            f"SELECT aguardando_de, COUNT(*) c {base} AND status NOT IN ({marcas_terminais}) "
            "GROUP BY aguardando_de", terminais).fetchall()
        criticas = conn.execute(
            f"SELECT COUNT(*) {base} AND prioridade = 'critica' AND status NOT IN ({marcas_terminais})",
            terminais).fetchone()[0]
        sem_atualizacao = conn.execute(
            f"SELECT COUNT(*) {base} AND status NOT IN ({marcas_terminais}) AND atualizado_em <= ?",
            (*terminais, limite_sem_atualizacao)).fetchone()[0]
        quantidade_total = conn.execute(
            f"SELECT COALESCE(SUM(quantidade), 0) {base} AND status NOT IN ({marcas_terminais})",
            terminais).fetchone()[0]
        criadas_hoje = conn.execute(
            f"SELECT COUNT(*) {base} AND date(criado_em) = date('now', 'localtime')").fetchone()[0]

    status_contagem = {r["status"]: r["c"] for r in por_status}
    aguardando_contagem = {r["aguardando_de"]: r["c"] for r in por_aguardando if r["aguardando_de"]}
    nao_iniciadas = status_contagem.get("nao_iniciado", 0)
    concluidas = sum(status_contagem.get(s, 0) for s in terminais)
    return {
        "total": total,
        "criadas_hoje": criadas_hoje,
        "nao_iniciadas": nao_iniciadas,
        "em_andamento": total - nao_iniciadas - concluidas,
        "concluidas": concluidas,
        "aguardando_brasil": aguardando_contagem.get("brasil", 0),
        "aguardando_suecia": aguardando_contagem.get("suecia", 0),
        "aguardando_fabricante": aguardando_contagem.get("fabricante", 0),
        "aguardando_cliente": aguardando_contagem.get("cliente", 0),
        "criticas": criticas,
        "sem_atualizacao": sem_atualizacao,
        "quantidade_total": quantidade_total,
    }


def resumo_por_cliente(filtros: dict | None = None, ordenar: str = "nome_asc") -> list[dict]:
    """Uma linha por cliente com a contagem de ocorrências por status —
    pro dashboard de entrada mostrar "como está cada cliente" antes de
    abrir a lista cheia de um deles. Reaproveita listar() (mesmos filtros
    de status/busca/etc., MENOS o de cliente, que não faz sentido pra
    quem está vendo todos), então o número do card bate com o que a lista
    detalhada mostra depois de clicar "Ver detalhes"."""
    filtros = dict(filtros or {})
    filtros.pop("cliente", None)
    itens = listar(filtros)
    terminais = status_terminais()
    mapa_status = opcoes_mapa("status")
    por_cliente: dict[str, dict] = {}
    for it in itens:
        c = por_cliente.setdefault(it["cliente"], {
            "cliente": it["cliente"], "total": 0,
            "nao_iniciadas": 0, "em_andamento": 0, "concluidas": 0,
            "_status_contagem": {},
        })
        c["total"] += 1
        if it["status"] == "nao_iniciado":
            c["nao_iniciadas"] += 1
        elif it["status"] in terminais:
            c["concluidas"] += 1
        else:
            c["em_andamento"] += 1
        c["_status_contagem"][it["status"]] = c["_status_contagem"].get(it["status"], 0) + 1
    for c in por_cliente.values():
        c["percentual_concluido"] = round(c["concluidas"] * 100 / c["total"]) if c["total"] else 0
        # Badge do card: o status mais comum entre as ocorrências AINDA
        # ABERTAS do cliente — é o que precisa de atenção agora. Só cai pra
        # "resolvido" (badge "Concluído") quando não sobra nenhuma aberta.
        ativos = {s: n for s, n in c["_status_contagem"].items() if s not in terminais}
        if ativos:
            status_pred = max(ativos.items(), key=lambda par: par[1])[0]
        else:
            status_pred = "resolvido"
        c["status_predominante"] = status_pred
        st = mapa_status.get(status_pred)
        c["status_predominante_texto"] = st["nome"] if st else status_pred
        c["status_predominante_cor"] = st["cor"] if st else _COR_PADRAO
        del c["_status_contagem"]

    clientes = list(por_cliente.values())
    if ordenar == "nome_desc":
        clientes.sort(key=lambda c: c["cliente"].lower(), reverse=True)
    elif ordenar == "qtd_desc":
        clientes.sort(key=lambda c: c["total"], reverse=True)
    else:
        clientes.sort(key=lambda c: c["cliente"].lower())
    return clientes


def buscar(ocorrencia_id: int) -> dict | None:
    with db() as conn:
        row = conn.execute(_query_base() + " WHERE ho.id = ?", (ocorrencia_id,)).fetchone()
    if not row:
        return None
    item = dict(row)
    _com_calculos([item])
    return item


def _tentar_vincular_patrimonio(serial: str) -> int | None:
    """Se o serial/patrimônio informado bate com um item_master já
    cadastrado, guarda o vínculo (dá link pro histórico de bipagem dele na
    tela de detalhe). Não bater não é erro — nem todo equipamento com
    defeito passou pelo controle de patrimônio por código de barras."""
    if not serial:
        return None
    with db() as conn:
        row = conn.execute("SELECT id FROM item_master WHERE codigo_barra = ?", (serial,)).fetchone()
    return row["id"] if row else None


# ── Eventos (timeline) ───────────────────────────────────────────────────────

def _evento(conn, ocorrencia_id: int, tipo: str, conteudo: str = "", situacao: str | None = None,
            usuario_id: int | None = None, quando: str | None = None) -> int:
    quando = quando or now_brt()
    cur = conn.execute(
        "INSERT INTO hardware_ocorrencia_evento (ocorrencia_id, tipo, conteudo, situacao, usuario_id, criado_em) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (ocorrencia_id, tipo, conteudo, situacao, usuario_id, quando))
    conn.execute("UPDATE hardware_ocorrencia SET atualizado_em = ? WHERE id = ?", (quando, ocorrencia_id))
    return cur.lastrowid


def listar_eventos(ocorrencia_id: int) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT e.*, u.nome AS usuario_nome FROM hardware_ocorrencia_evento e "
            "LEFT JOIN users u ON u.id = e.usuario_id "
            "WHERE e.ocorrencia_id = ? ORDER BY e.criado_em DESC, e.id DESC",
            (ocorrencia_id,)).fetchall()
    eventos = [dict(r) for r in rows]
    for e in eventos:
        e["tipo_texto"] = EVENTO_TEXTO.get(e["tipo"], e["tipo"])
    return eventos


def listar_eventos_area(ocorrencia_id: int, area: str) -> list[dict]:
    if area not in AREAS_ACAO:
        return []
    with db() as conn:
        rows = conn.execute(
            "SELECT e.*, u.nome AS usuario_nome FROM hardware_ocorrencia_evento e "
            "LEFT JOIN users u ON u.id = e.usuario_id "
            "WHERE e.ocorrencia_id = ? AND e.tipo = ? ORDER BY e.criado_em DESC, e.id DESC",
            (ocorrencia_id, f"acao_{area}")).fetchall()
    return [dict(r) for r in rows]


# ── Ciclo de vida da ocorrência ──────────────────────────────────────────────

def criar(dados: dict, usuario_id: int) -> int:
    cliente = (dados.get("cliente") or "").strip()
    if not cliente:
        raise ValueError("Informe o cliente.")
    prioridade = dados.get("prioridade") or prioridade_padrao()
    if prioridade not in opcoes_mapa("prioridade"):
        raise ValueError("Prioridade inválida.")

    produto_id = dados.get("hardware_produto_id") or None
    if produto_id:
        with db() as conn:
            p = conn.execute("SELECT nome FROM hardware_produto WHERE id = ?", (produto_id,)).fetchone()
        if not p:
            raise ValueError("Produto não encontrado.")
        produto_nome = p["nome"]
    else:
        produto_nome = (dados.get("produto_nome") or "").strip()

    quantidade = max(1, int(dados.get("quantidade") or 1))
    serial = (dados.get("serial_patrimonio") or "").strip()
    item_master_id = _tentar_vincular_patrimonio(serial)
    agora = now_brt()
    data_registro = (dados.get("data_registro") or "").strip() or agora

    with db() as conn:
        cur = conn.execute(
            "INSERT INTO hardware_ocorrencia ("
            "  status, prioridade, cliente, hardware_produto_id, produto_nome, serial_patrimonio,"
            "  item_master_id, quantidade, categoria_defeito, subcategoria_defeito, descricao_defeito,"
            "  localizacao, localizacao_detalhe, responsavel_id, data_registro,"
            "  criado_por, criado_em, atualizado_em, ativo"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
            (
                "nao_iniciado", prioridade, cliente, produto_id, produto_nome, serial, item_master_id,
                quantidade, (dados.get("categoria_defeito") or "").strip(),
                (dados.get("subcategoria_defeito") or "").strip(),
                (dados.get("descricao_defeito") or "").strip(),
                (dados.get("localizacao") or "").strip(),
                (dados.get("localizacao_detalhe") or "").strip(),
                dados.get("responsavel_id") or None, data_registro,
                usuario_id, agora, agora,
            ))
        ocorrencia_id = cur.lastrowid
        _evento(conn, ocorrencia_id, "criacao",
                f"Ocorrência registrada para {produto_nome or 'produto não informado'} "
                f"({cliente}).", usuario_id=usuario_id, quando=agora)
    return ocorrencia_id


def editar_basico(ocorrencia_id: int, dados: dict, usuario_id: int) -> None:
    """Resumo + diagnóstico + prioridade — sem regra própria de negócio,
    cabem numa função só (ao contrário de status/responsável, que geram
    evento de mudança com o valor antigo x novo)."""
    o = buscar(ocorrencia_id)
    if not o:
        raise ValueError("Ocorrência não encontrada.")
    prioridade = dados.get("prioridade") or o["prioridade"]
    mapa_prioridade = opcoes_mapa("prioridade")
    if prioridade not in mapa_prioridade:
        raise ValueError("Prioridade inválida.")

    produto_id = dados.get("hardware_produto_id") or None
    if produto_id:
        with db() as conn:
            p = conn.execute("SELECT nome FROM hardware_produto WHERE id = ?", (produto_id,)).fetchone()
        if not p:
            raise ValueError("Produto não encontrado.")
        produto_nome = p["nome"]
    else:
        produto_nome = (dados.get("produto_nome") or o["produto_nome"] or "").strip()

    serial = (dados.get("serial_patrimonio") or "").strip()
    item_master_id = _tentar_vincular_patrimonio(serial) if serial != (o["serial_patrimonio"] or "") \
        else o["item_master_id"]

    agora = now_brt()
    with db() as conn:
        conn.execute(
            "UPDATE hardware_ocorrencia SET cliente=?, hardware_produto_id=?, produto_nome=?, "
            "serial_patrimonio=?, item_master_id=?, quantidade=?, categoria_defeito=?, "
            "subcategoria_defeito=?, descricao_defeito=?, diagnostico_texto=?, localizacao=?, "
            "localizacao_detalhe=?, prioridade=?, atualizado_em=? WHERE id=?",
            (
                (dados.get("cliente") or o["cliente"]).strip(), produto_id, produto_nome,
                serial, item_master_id,
                max(1, int(dados.get("quantidade") or o["quantidade"] or 1)),
                (dados.get("categoria_defeito") or "").strip(),
                (dados.get("subcategoria_defeito") or "").strip(),
                (dados.get("descricao_defeito") or "").strip(),
                (dados.get("diagnostico_texto") or "").strip(),
                (dados.get("localizacao") or "").strip(),
                (dados.get("localizacao_detalhe") or "").strip(),
                prioridade, agora, ocorrencia_id,
            ))
        if prioridade != o["prioridade"]:
            de = mapa_prioridade.get(o["prioridade"])
            para = mapa_prioridade.get(prioridade)
            _evento(conn, ocorrencia_id, "prioridade",
                    f"Prioridade alterada de {de['nome'] if de else o['prioridade']} "
                    f"para {para['nome'] if para else prioridade}.", usuario_id=usuario_id, quando=agora)


def mudar_status(ocorrencia_id: int, novo_status: str, observacao: str, usuario_id: int) -> None:
    mapa_status = opcoes_mapa("status")
    if novo_status not in mapa_status:
        raise ValueError("Status inválido.")
    o = buscar(ocorrencia_id)
    if not o:
        raise ValueError("Ocorrência não encontrada.")
    agora = now_brt()
    de = mapa_status.get(o["status"])
    para = mapa_status.get(novo_status)
    conteudo = (f"Status alterado de {de['nome'] if de else o['status']} "
                f"para {para['nome'] if para else novo_status}.")
    if (observacao or "").strip():
        conteudo += f" {observacao.strip()}"
    with db() as conn:
        conn.execute("UPDATE hardware_ocorrencia SET status=?, atualizado_em=? WHERE id=?",
                     (novo_status, agora, ocorrencia_id))
        _evento(conn, ocorrencia_id, "status", conteudo, usuario_id=usuario_id, quando=agora)


def definir_responsavel(ocorrencia_id: int, responsavel_id: int | None, usuario_id: int) -> None:
    o = buscar(ocorrencia_id)
    if not o:
        raise ValueError("Ocorrência não encontrada.")
    agora = now_brt()
    with db() as conn:
        conn.execute("UPDATE hardware_ocorrencia SET responsavel_id=?, atualizado_em=? WHERE id=?",
                     (responsavel_id, agora, ocorrencia_id))
        nome_novo = "ninguém"
        if responsavel_id:
            r = conn.execute("SELECT nome FROM users WHERE id = ?", (responsavel_id,)).fetchone()
            nome_novo = r["nome"] if r else "ninguém"
        _evento(conn, ocorrencia_id, "responsavel",
                f"Responsável alterado para {nome_novo}.", usuario_id=usuario_id, quando=agora)


def definir_proxima_acao(ocorrencia_id: int, texto: str, data_prevista: str | None,
                          aguardando_de: str, usuario_id: int) -> None:
    if aguardando_de and aguardando_de not in AGUARDANDO_TEXTO:
        raise ValueError("Valor de 'aguardando de' inválido.")
    agora = now_brt()
    with db() as conn:
        conn.execute(
            "UPDATE hardware_ocorrencia SET proxima_acao=?, proxima_acao_data=?, aguardando_de=?, "
            "atualizado_em=? WHERE id=?",
            ((texto or "").strip(), (data_prevista or "").strip() or None, aguardando_de or "", agora, ocorrencia_id))


def registrar_atualizacao(ocorrencia_id: int, conteudo: str, usuario_id: int) -> int:
    conteudo = (conteudo or "").strip()
    if not conteudo:
        raise ValueError("Escreva o conteúdo da atualização.")
    with db() as conn:
        return _evento(conn, ocorrencia_id, "atualizacao", conteudo, usuario_id=usuario_id)


def registrar_acao(ocorrencia_id: int, area: str, conteudo: str, situacao: str, usuario_id: int) -> int:
    if area not in AREAS_ACAO:
        raise ValueError("Área inválida.")
    conteudo = (conteudo or "").strip()
    if not conteudo:
        raise ValueError("Escreva a ação realizada.")
    with db() as conn:
        return _evento(conn, ocorrencia_id, f"acao_{area}", conteudo, situacao=(situacao or "").strip() or None,
                        usuario_id=usuario_id)


def resolver(ocorrencia_id: int, resultado_final: str, solucao_texto: str, data_solucao: str,
             usuario_id: int) -> None:
    if resultado_final not in RESULTADOS_FINAIS:
        raise ValueError("Resultado final inválido.")
    agora = now_brt()
    data_solucao = (data_solucao or "").strip() or agora
    with db() as conn:
        conn.execute(
            "UPDATE hardware_ocorrencia SET status='resolvido', resultado_final=?, solucao_texto=?, "
            "data_solucao=?, aguardando_de='', atualizado_em=? WHERE id=?",
            (resultado_final, (solucao_texto or "").strip(), data_solucao, agora, ocorrencia_id))
        _evento(conn, ocorrencia_id, "resultado_final",
                f"Resultado: {resultado_final}. {(solucao_texto or '').strip()}".strip(),
                usuario_id=usuario_id, quando=agora)


def reabrir(ocorrencia_id: int, motivo: str, usuario_id: int) -> None:
    motivo = (motivo or "").strip()
    if not motivo:
        raise ValueError("Informe o motivo da reabertura.")
    o = buscar(ocorrencia_id)
    if not o:
        raise ValueError("Ocorrência não encontrada.")
    if o["status"] not in status_terminais():
        raise ValueError("Esta ocorrência não está encerrada/resolvida/cancelada.")
    agora = now_brt()
    with db() as conn:
        conn.execute("UPDATE hardware_ocorrencia SET status='em_andamento', atualizado_em=? WHERE id=?",
                     (agora, ocorrencia_id))
        _evento(conn, ocorrencia_id, "reabertura", motivo, usuario_id=usuario_id, quando=agora)


def arquivar(ocorrencia_id: int) -> None:
    """Soft delete — preserva histórico e evita duplicidade de patrimônio;
    nunca some do banco, só das listas de trabalho."""
    with db() as conn:
        conn.execute("UPDATE hardware_ocorrencia SET ativo = 0 WHERE id = ?", (ocorrencia_id,))


# ── Anexos ───────────────────────────────────────────────────────────────────

def _extensao_valida(nome_arquivo: str, tipo_mime: str) -> str:
    nome_arquivo = (nome_arquivo or "").lower()
    ext = os.path.splitext(nome_arquivo)[1]
    if ext not in EXTENSOES_ANEXO_PERMITIDAS:
        raise ValueError(
            f"Tipo de arquivo não permitido. Aceitos: {', '.join(EXTENSOES_ANEXO_PERMITIDAS)}.")
    aceitos = MIME_POR_EXTENSAO.get(ext, ())
    if tipo_mime and aceitos and tipo_mime not in aceitos:
        raise ValueError("O tipo do arquivo não bate com a extensão informada.")
    return ext


def salvar_anexo(ocorrencia_id: int, nome_arquivo: str, conteudo: bytes, tipo_mime: str,
                  usuario_id: int, evento_id: int | None = None) -> int:
    if len(conteudo) > MAX_ANEXO_BYTES:
        raise ValueError(f"Arquivo maior que o limite de {MAX_ANEXO_BYTES // (1024 * 1024)}MB.")
    ext = _extensao_valida(nome_arquivo, tipo_mime)
    pasta = os.path.join(PASTA_UPLOADS, str(ocorrencia_id))
    os.makedirs(pasta, exist_ok=True)
    nome_disco = f"{uuid.uuid4().hex}{ext}"
    caminho_disco = os.path.join(pasta, nome_disco)
    with open(caminho_disco, "wb") as f:
        f.write(conteudo)
    agora = now_brt()
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO hardware_anexo (ocorrencia_id, evento_id, nome_arquivo, caminho_disco, "
            "tamanho, tipo_mime, enviado_por, enviado_em) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (ocorrencia_id, evento_id, os.path.basename(nome_arquivo or nome_disco),
             caminho_disco, len(conteudo), tipo_mime, usuario_id, agora))
        anexo_id = cur.lastrowid
        if not evento_id:
            _evento(conn, ocorrencia_id, "anexo",
                    f"Arquivo anexado: {os.path.basename(nome_arquivo or nome_disco)}",
                    usuario_id=usuario_id, quando=agora)
    return anexo_id


def listar_anexos(ocorrencia_id: int) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT a.*, u.nome AS enviado_por_nome FROM hardware_anexo a "
            "LEFT JOIN users u ON u.id = a.enviado_por "
            "WHERE a.ocorrencia_id = ? ORDER BY a.enviado_em DESC",
            (ocorrencia_id,)).fetchall()
    return [dict(r) for r in rows]


def buscar_anexo(anexo_id: int) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM hardware_anexo WHERE id = ?", (anexo_id,)).fetchone()
    return dict(row) if row else None
