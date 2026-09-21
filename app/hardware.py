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


def _normalizar(texto) -> str:
    """minúsculo, sem acento, sem pontuação -- usado pra casar cabeçalho de
    planilha e texto de status/catálogo sem depender de escrita exata.
    Bandeiras de país (🇧🇷/🇸🇪) viram a palavra do país ANTES da conversão pra
    ascii, senão o emoji simplesmente some e "Ação Corretiva 🇧🇷" e
    "Ação Corretiva 🇸🇪" ficariam idênticos depois de normalizados."""
    texto = str(texto or "").replace("🇧🇷", " brasil ").replace("🇸🇪", " suecia ")
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", texto.lower()).strip()

# Status, prioridade, categoria de defeito e localização são catálogos
# editáveis no banco (hardware_*_opcao) — ver seção "Catálogos" abaixo.
# "Aguardando de" continua fixo: é a mesma estrutura das 4 frentes de ação
# (Brasil/Suécia/Fabricante/Cliente), amarrada ao resto do código
# (AREAS_ACAO, quem pode registrar uma ação), não um simples rótulo de lista.
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

# Vocabulário alternativo pra coluna Status de planilha antiga (ex.: "FEITO"
# em vez de "Resolvido") -- chave já normalizada por _normalizar(). Só entra
# em jogo quando o texto não bate com o NOME de nenhum status do catálogo
# atual (que já cobre o caso comum de re-importar com o nome exato).
_STATUS_SINONIMOS_IMPORTACAO = {
    "nao iniciado":          "nao_iniciado",
    "em analise":            "em_analise",
    "em andamento":          "em_andamento",
    "aguardando informacao": "aguardando_informacao",
    "aguardando brasil":     "aguardando_brasil",
    "aguardando suecia":     "aguardando_suecia",
    "aguardando fabricante": "aguardando_fabricante",
    "aguardando cliente":    "aguardando_cliente",
    "aguardando peca":       "aguardando_peca",
    "aguardando devolucao":  "aguardando_devolucao",
    "resolvido":             "resolvido",
    "feito":                 "resolvido",
    "concluido":             "resolvido",
    "pronto":                "resolvido",
    "encerrado":             "encerrado",
    "cancelado":             "cancelado",
}

# "cliente" entrou como 4ª frente de ação (junto de Brasil/Suécia/Fabricante)
# a pedido do usuário -- registrar uma ação virou um formulário só com "quem
# realizou" em vez de 3 (agora 4) botões fixos separados. Reaproveita o
# mesmo mecanismo (evento tipo 'acao_<area>'), sem precisar de coluna nova
# nem migração -- e já bate com AGUARDANDO_DE, que já tinha "cliente".
AREAS_ACAO = ("brasil", "suecia", "fabricante", "cliente")
AREA_TEXTO = {"brasil": "Brasil", "suecia": "Suécia", "fabricante": "Fabricante", "cliente": "Cliente"}
# Cor por frente -- badge da timeline unificada de ações (ver admin_hardware_detalhe.html).
AREA_COR = {"brasil": "#2668a8", "suecia": "#6b3fb5", "fabricante": "#b45309", "cliente": "#18804b"}

EVENTO_TEXTO = {
    "criacao":         "Ocorrência criada",
    "atualizacao":     "Anotação",
    "acao_brasil":     "Ação — Brasil",
    "acao_suecia":     "Ação — Suécia",
    "acao_fabricante": "Ação — Fabricante",
    "acao_cliente":    "Ação — Cliente",
    "status":          "Status alterado",
    "responsavel":     "Responsável alterado",
    "prioridade":      "Prioridade alterada",
    "reabertura":      "Reaberta",
    "resultado_final": "Resultado final registrado",
    "anexo":           "Arquivo anexado",
    "arquivado":       "Arquivada",
    "reativado":       "Reativada",
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
    """"RMA-0001" — derivado do id, nunca guardado (nada pra congelar aqui,
    diferente do nome de produto/cliente, que pode mudar depois)."""
    return f"RMA-{ocorrencia_id:04d}"


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
    "responsavel": {"tabela": "hardware_responsavel_opcao", "tem_chave": False, "tem_cor": False, "tem_terminal": False},
}
# Colunas de hardware_ocorrencia que guardam o NOME direto (sem chave
# própria) -- editar_opcao() propaga renomear pra essas ocorrências já
# gravadas, senão elas ficariam com um valor que não existe mais em
# catálogo nenhum.
_COLUNA_PROPAGACAO_RENOMEAR = {"categoria": "categoria_defeito", "responsavel": "responsavel_nome"}


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
    coluna_propagacao = _COLUNA_PROPAGACAO_RENOMEAR.get(tipo)
    with db() as conn:
        nome_antigo = None
        if coluna_propagacao:
            row = conn.execute(f"SELECT nome FROM {cfg['tabela']} WHERE id = ?", (opcao_id,)).fetchone()
            nome_antigo = row["nome"] if row else None
        try:
            conn.execute(f"UPDATE {cfg['tabela']} SET {', '.join(campos)} WHERE id = ?", (*valores, opcao_id))
        except Exception:
            raise ValueError(f'Já existe um item chamado "{nome}".')
        # Categoria e responsável não têm chave própria — o nome É o valor
        # gravado na ocorrência, então renomear precisa propagar pro que já
        # foi salvo, senão a ocorrência antiga fica com um rótulo que não
        # existe mais em lugar nenhum da tela.
        if nome_antigo and nome_antigo != nome:
            conn.execute(f"UPDATE hardware_ocorrencia SET {coluna_propagacao} = ? WHERE {coluna_propagacao} = ?",
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
    "uc.nome AS criado_por_nome"
)


def _query_base() -> str:
    # responsavel_nome já vem de ho.* (texto livre, não é mais FK pra
    # users) -- só criado_por continua sendo um login de verdade, então só
    # ele precisa de JOIN.
    return (
        f"SELECT {_CAMPOS} FROM hardware_ocorrencia ho "
        "LEFT JOIN hardware_produto hp ON hp.id = ho.hardware_produto_id "
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
    """{ocorrencia_id: {"brasil": {...}, "suecia": {...}, "fabricante": {...}, "cliente": {...}}}
    com a AÇÃO MAIS RECENTE de cada frente — nunca todas, só a última, que é
    o que a tela de resumo e os cards precisam. O histórico completo por
    área vive em listar_eventos_area()."""
    sql = (
        "SELECT e.ocorrencia_id, e.tipo, e.conteudo, e.situacao, e.criado_em, u.nome AS usuario_nome "
        "FROM hardware_ocorrencia_evento e LEFT JOIN users u ON u.id = e.usuario_id "
        "WHERE e.tipo IN ('acao_brasil','acao_suecia','acao_fabricante','acao_cliente') "
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
    # incluir_arquivadas: sem isso, uma ocorrência arquivada nunca mais
    # aparecia em lugar nenhum -- "arquivar" tinha virado "excluir" na
    # prática, mesmo com o dado preservado no banco. Com o filtro marcado,
    # mostra as duas (arquivada some do resumo/KPI de qualquer forma,
    # porque resumo() e resumo_por_cliente() chamam listar() sem passar
    # esse filtro). somente_arquivadas é o extremo oposto -- só as
    # arquivadas, pra tela de "itens arquivados" (ver o que tem, reativar).
    where = [] if (filtros.get("incluir_arquivadas") or filtros.get("somente_arquivadas")) else ["ho.ativo = 1"]
    if filtros.get("somente_arquivadas"):
        where.append("ho.ativo = 0")
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
    if filtros.get("responsavel"):
        where.append("ho.responsavel_nome = ?")
        params.append(filtros["responsavel"])
    if filtros.get("aguardando_de"):
        marcas = ",".join("?" * len(filtros["aguardando_de"]))
        where.append(f"ho.aguardando_de IN ({marcas})")
        params += filtros["aguardando_de"]
    if filtros.get("busca"):
        termo = f"%{filtros['busca'].strip()}%"
        where.append("(ho.cliente LIKE ? OR ho.produto_nome LIKE ? OR ho.serial_patrimonio LIKE ? "
                      "OR ('RMA-' || printf('%04d', ho.id)) LIKE ?)")
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

    sql = _query_base() + (f" WHERE {' AND '.join(where)}" if where else "") + " ORDER BY ho.atualizado_em DESC"
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
    # O painel por cliente é sempre sobre trabalho ATIVO -- arquivada nunca
    # entra na conta aqui, mesmo que o parâmetro tenha vindo de algum jeito.
    filtros.pop("incluir_arquivadas", None)
    filtros.pop("somente_arquivadas", None)
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


def data_br(valor: str | None, com_hora: bool = True) -> str:
    """'2026-09-18 14:30:05' -> '18/09/2026 14:30' (ou só a data)."""
    valor = (valor or "").strip()
    if len(valor) < 10 or valor[4] != "-":
        return valor
    texto = f"{valor[8:10]}/{valor[5:7]}/{valor[:4]}"
    return f"{texto} {valor[11:16]}" if com_hora and len(valor) >= 16 else texto


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
        e["data_br"] = data_br(e["criado_em"])
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
            "  localizacao, localizacao_detalhe, responsavel_nome, data_registro,"
            "  criado_por, criado_em, atualizado_em, ativo"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
            (
                "nao_iniciado", prioridade, cliente, produto_id, produto_nome, serial, item_master_id,
                quantidade, (dados.get("categoria_defeito") or "").strip(),
                (dados.get("subcategoria_defeito") or "").strip(),
                (dados.get("descricao_defeito") or "").strip(),
                (dados.get("localizacao") or "").strip(),
                (dados.get("localizacao_detalhe") or "").strip(),
                (dados.get("responsavel_nome") or "").strip(), data_registro,
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


def definir_responsavel(ocorrencia_id: int, responsavel_nome: str, usuario_id: int) -> None:
    o = buscar(ocorrencia_id)
    if not o:
        raise ValueError("Ocorrência não encontrada.")
    responsavel_nome = (responsavel_nome or "").strip()
    agora = now_brt()
    with db() as conn:
        conn.execute("UPDATE hardware_ocorrencia SET responsavel_nome=?, atualizado_em=? WHERE id=?",
                     (responsavel_nome, agora, ocorrencia_id))
        _evento(conn, ocorrencia_id, "responsavel",
                f"Responsável alterado para {responsavel_nome or 'ninguém'}.", usuario_id=usuario_id, quando=agora)


def definir_aguardando(ocorrencia_id: int, aguardando_de: str, usuario_id: int) -> None:
    """Quem a ocorrência está esperando (Brasil/Suécia/Fabricante/Cliente ou
    ninguém). Só mexe nisso -- o RMA é um histórico do caso, não um fluxo de
    "próximas ações" (as colunas proxima_acao* continuam no banco só pra não
    perder dado antigo, mas nada mais lê nem grava elas)."""
    aguardando_de = (aguardando_de or "").strip()
    if aguardando_de and aguardando_de not in AGUARDANDO_TEXTO:
        raise ValueError("Valor de 'aguardando de' inválido.")
    with db() as conn:
        atual = conn.execute("SELECT aguardando_de FROM hardware_ocorrencia WHERE id = ?",
                             (ocorrencia_id,)).fetchone()
        if atual is None or (atual["aguardando_de"] or "") == aguardando_de:
            return
        conn.execute("UPDATE hardware_ocorrencia SET aguardando_de = ?, atualizado_em = ? WHERE id = ?",
                     (aguardando_de, now_brt(), ocorrencia_id))


def registrar_atualizacao(ocorrencia_id: int, conteudo: str, usuario_id: int) -> int:
    """Anotação do caso (aparece como "Anotação" na tela; o tipo de evento
    continua 'atualizacao' pra não separar as já registradas das novas)."""
    conteudo = (conteudo or "").strip()
    if not conteudo:
        raise ValueError("Escreva o texto da anotação.")
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


def arquivar(ocorrencia_id: int, usuario_id: int | None = None) -> None:
    """Soft delete — preserva histórico e evita duplicidade de patrimônio;
    nunca some do banco, só das listas de trabalho. Reversível: ver
    reativar()."""
    agora = now_brt()
    with db() as conn:
        conn.execute("UPDATE hardware_ocorrencia SET ativo = 0, atualizado_em = ? WHERE id = ?",
                     (agora, ocorrencia_id))
        _evento(conn, ocorrencia_id, "arquivado", usuario_id=usuario_id, quando=agora)


def reativar(ocorrencia_id: int, usuario_id: int | None = None) -> None:
    """Desfaz arquivar() — a ocorrência volta a aparecer nas listas de
    trabalho normalmente, sem perder nada do histórico."""
    agora = now_brt()
    with db() as conn:
        conn.execute("UPDATE hardware_ocorrencia SET ativo = 1, atualizado_em = ? WHERE id = ?",
                     (agora, ocorrencia_id))
        _evento(conn, ocorrencia_id, "reativado", usuario_id=usuario_id, quando=agora)


def excluir(ocorrencia_id: int) -> None:
    """Exclusão DE VERDADE — ao contrário de arquivar(), não dá pra desfazer.
    Some a ocorrência, o histórico (timeline) e os anexos (arquivo em disco
    incluído). A única coisa preservada é a conferência de uma importação
    antiga que tenha criado esta ocorrência: a linha continua lá com
    cliente/produto/status em texto, só o link pra ocorrência (que não
    existe mais) é solto -- mesmo princípio da exclusão de veículo, que
    preserva o texto e solta só o vínculo."""
    with db() as conn:
        anexos = conn.execute(
            "SELECT caminho_disco FROM hardware_anexo WHERE ocorrencia_id = ?", (ocorrencia_id,)
        ).fetchall()
        conn.execute("DELETE FROM hardware_anexo WHERE ocorrencia_id = ?", (ocorrencia_id,))
        conn.execute("DELETE FROM hardware_ocorrencia_evento WHERE ocorrencia_id = ?", (ocorrencia_id,))
        conn.execute(
            "UPDATE hardware_importacao_item SET ocorrencia_id = NULL WHERE ocorrencia_id = ?",
            (ocorrencia_id,))
        conn.execute("DELETE FROM hardware_ocorrencia WHERE id = ?", (ocorrencia_id,))
    for a in anexos:
        try:
            os.remove(a["caminho_disco"])
        except OSError:
            pass  # arquivo já sumiu do disco -- não impede a exclusão do cadastro


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


# ── Importação por planilha ──────────────────────────────────────────────────

def _linha_cabecalho(ws) -> int:
    """Acha a linha de cabeçalho procurando "produto" e "cliente" juntos nas
    10 primeiras linhas -- cobre tanto o modelo baixado (cabeçalho na linha 1)
    quanto uma planilha antiga com linhas de título/instrução antes dele."""
    limite = min(10, ws.max_row or 1)
    for r in range(1, limite + 1):
        celulas = [_normalizar(c.value) for c in next(ws.iter_rows(min_row=r, max_row=r))]
        texto_linha = " ".join(celulas)
        if "produto" in texto_linha and "cliente" in texto_linha:
            return r
    return 1


def importar_excel(file_bytes: bytes, usuario_id: int) -> dict:
    """Importa ocorrências de uma planilha -- o modelo baixado em
    /admin/hardware/modelo.xlsx, ou uma planilha de controle de defeito já em
    uso antes deste sistema. Cabeçalho é reconhecido por palavra-chave (não
    por posição), então ordem de coluna e variação de texto no cabeçalho
    (com ou sem acento, "Ação Corretiva 🇧🇷" ou "Ação Corretiva Brasil") não
    atrapalham.

    Reimportar a MESMA linha não duplica só quando ela carrega um valor na
    coluna de identificador do RMA -- o "nome"/código que o próprio operador
    já usa pra se referir àquela ocorrência (ex.: "RMA1", "RMA1-E"), igual o
    Número já funciona pra veículo. É a única chave que sobrevive a uma troca
    de serial/patrimônio, que é opcional e não é único (o mesmo equipamento
    pode ter várias ocorrências ao longo do tempo, cada reparo é uma linha
    nova). Linha sem essa coluna preenchida é SEMPRE uma ocorrência nova.

    Célula preenchida sobrescreve o cadastro já existente (produto, defeito,
    responsável, status, quantidade, descrição, data de solução); célula
    vazia preserva -- mesma regra da importação de veículos (planilha manda,
    mas só no que ela de fato preencheu). Ação Brasil/Suécia/Fabricante só é
    registrada na timeline na CRIAÇÃO -- reimportar uma linha já existente
    não duplica a ação (ela é histórico append-only, não um campo comum).

    Categoria de defeito, produto e responsável ausentes do catálogo são
    criados automaticamente (a mesma lista editável pela tela) -- um aviso
    na linha avisa quando isso acontece. Status sem correspondência
    reconhecida vira "Não iniciado" com aviso, em vez de erro -- a linha
    sempre entra."""
    import openpyxl
    import io as _io
    wb = openpyxl.load_workbook(_io.BytesIO(file_bytes), data_only=True)
    ws = wb.active
    linha_cab = _linha_cabecalho(ws)
    headers_raw = [str(c.value or "") for c in next(ws.iter_rows(min_row=linha_cab, max_row=linha_cab))]
    headers = [_normalizar(h) for h in headers_raw]

    def achar(*grupos: tuple[str, ...]) -> int | None:
        for grupo in grupos:
            for i, h in enumerate(headers):
                if all(p in h for p in grupo):
                    return i
        return None

    col_cliente = achar(("cliente",))
    col_produto = achar(("produto",))
    if col_cliente is None or col_produto is None:
        return {"inseridos": 0, "atualizados": 0, "ignorados": 0, "itens": [],
                "erros": ["Cabeçalhos não encontrados. Use o modelo baixado em "
                          '"Baixar planilha modelo" -- é preciso ter ao menos '
                          'as colunas "Cliente" e "Produto".']}
    col_serial      = achar(("serial",), ("patrimonio",))
    col_defeito     = achar(("categoria", "defeito"), ("defeito",))
    col_responsavel = achar(("responsavel",))
    col_status      = achar(("status",))
    col_quantidade  = achar(("quantidade",))
    col_registro    = achar(("registro",))
    col_solucao     = achar(("solucao",))
    col_observacoes = achar(("observ",))
    # Ordem de prioridade: "Identificador" (nosso modelo) > "Name" (planilha
    # antiga, onde já vem "RMA1", "RMA2"...) > "Referência" (nome antigo
    # desta mesma coluna) > "ID do elemento" (o id interno do Monday.com --
    # existe na planilha antiga, mas ninguém digita/lê esse número, então só
    # entra como último recurso). ("elemento",) sozinho bateria com
    # "Subelementos" (contém "elemento" como substring) -- exige "id" junto.
    col_ref         = achar(("identificador",), ("name",), ("referencia",), ("id", "elemento"))
    col_acao_br     = achar(("corretiva", "brasil"), ("acao", "brasil"))
    col_acao_se     = achar(("corretiva", "suecia"), ("acao", "suecia"))
    col_acao_fab    = achar(("corretiva", "fab"), ("acao", "fab"))

    def valor(row: tuple, col: int | None) -> str:
        if col is None or col >= len(row):
            return ""
        v = row[col]
        if v is None:
            return ""
        if isinstance(v, datetime):
            return v.strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(v, float) and v.is_integer():
            return str(int(v))
        return str(v).strip()

    mapa_categoria     = {r["nome"].lower(): r for r in listar_opcoes("categoria", incluir_inativas=True)}
    mapa_responsavel    = {r["nome"].lower(): r for r in listar_opcoes("responsavel", incluir_inativas=True)}
    opcoes_status        = listar_opcoes("status", incluir_inativas=True)
    mapa_status_por_nome  = {r["nome"].lower(): r for r in opcoes_status}
    mapa_status_por_chave = {r["chave"]: r for r in opcoes_status}
    produtos_existentes  = {p["nome"].lower(): p for p in listar_produtos(incluir_inativos=True)}
    ordem_categoria   = max([r["ordem"] for r in mapa_categoria.values()], default=-1)
    ordem_responsavel = max([r["ordem"] for r in mapa_responsavel.values()], default=-1)
    prioridade = prioridade_padrao()

    itens: list[dict] = []
    inseridos = atualizados = ignorados = 0

    def anota(linha, rotulo_txt, situacao, ocorrencia_id=None,
              antes=None, depois=None, erro=""):
        antes = antes or {}
        depois = depois or {}
        itens.append({
            "linha": linha, "rotulo": rotulo_txt, "situacao": situacao,
            "ocorrencia_id": ocorrencia_id, "erro": erro,
            "cliente_antes": antes.get("cliente", ""), "cliente_depois": depois.get("cliente", ""),
            "produto_antes": antes.get("produto", ""), "produto_depois": depois.get("produto", ""),
            "status_antes": antes.get("status", ""), "status_depois": depois.get("status", ""),
        })

    with db() as conn:
        for row_idx, row in enumerate(ws.iter_rows(min_row=linha_cab + 1, values_only=True), linha_cab + 1):
            cliente = valor(row, col_cliente)
            produto_digitado = valor(row, col_produto)
            if not cliente and not produto_digitado and not valor(row, col_serial):
                continue  # linha em branco no fim da planilha -- não é erro de ninguém
            if not cliente:
                ignorados += 1
                anota(row_idx, "", "erro", erro="Linha ignorada -- sem cliente.")
                continue

            avisos_linha: list[str] = []

            # Produto: casa por nome sem diferenciar maiúscula/minúscula; não
            # bate com nenhum cadastrado -> cria (mesma regra de
            # criar_produto(), mas na MESMA conexão/transação -- abrir uma
            # segunda conexão aqui travaria o banco, igual ao comentário da
            # importação de veículos).
            produto_id = None
            produto_nome = produto_digitado
            if produto_digitado:
                existente_p = produtos_existentes.get(produto_digitado.lower())
                if existente_p:
                    produto_id = existente_p["id"]
                    produto_nome = existente_p["nome"]
                else:
                    cur = conn.execute(
                        "INSERT INTO hardware_produto (nome, criado_em) VALUES (?, ?)",
                        (produto_digitado, now_brt()))
                    produto_id = cur.lastrowid
                    produtos_existentes[produto_digitado.lower()] = {"id": produto_id, "nome": produto_digitado}
                    avisos_linha.append(f'Produto "{produto_digitado}" criado automaticamente.')

            categoria_digitada = valor(row, col_defeito)
            categoria = categoria_digitada
            if categoria_digitada:
                existente_c = mapa_categoria.get(categoria_digitada.lower())
                if existente_c:
                    categoria = existente_c["nome"]
                else:
                    ordem_categoria += 1
                    conn.execute(
                        "INSERT INTO hardware_categoria_opcao (nome, ordem, criado_em) VALUES (?, ?, ?)",
                        (categoria_digitada, ordem_categoria, now_brt()))
                    mapa_categoria[categoria_digitada.lower()] = {"nome": categoria_digitada}
                    avisos_linha.append(f'Categoria de defeito "{categoria_digitada}" criada automaticamente.')

            responsavel_digitado = valor(row, col_responsavel)
            responsavel = responsavel_digitado
            if responsavel_digitado:
                existente_r = mapa_responsavel.get(responsavel_digitado.lower())
                if existente_r:
                    responsavel = existente_r["nome"]
                else:
                    ordem_responsavel += 1
                    conn.execute(
                        "INSERT INTO hardware_responsavel_opcao (nome, ordem, criado_em) VALUES (?, ?, ?)",
                        (responsavel_digitado, ordem_responsavel, now_brt()))
                    mapa_responsavel[responsavel_digitado.lower()] = {"nome": responsavel_digitado}
                    avisos_linha.append(f'Responsável "{responsavel_digitado}" criado automaticamente.')

            status_digitado = valor(row, col_status)
            status_chave = "nao_iniciado"
            if status_digitado:
                por_nome = mapa_status_por_nome.get(status_digitado.lower())
                if por_nome:
                    status_chave = por_nome["chave"]
                else:
                    status_chave = _STATUS_SINONIMOS_IMPORTACAO.get(_normalizar(status_digitado), "")
                    if not status_chave:
                        status_chave = "nao_iniciado"
                        avisos_linha.append(
                            f'Status "{status_digitado}" não reconhecido -- '
                            'importado como "Não iniciado".')

            quantidade_raw = valor(row, col_quantidade)
            try:
                quantidade = max(1, int(float(quantidade_raw))) if quantidade_raw else 1
            except ValueError:
                quantidade = 1

            serial = valor(row, col_serial)
            data_registro = valor(row, col_registro) or now_brt()
            data_solucao = valor(row, col_solucao)
            descricao = valor(row, col_observacoes)
            ref_externo = valor(row, col_ref)
            status_nome = mapa_status_por_chave.get(status_chave, {}).get("nome", status_chave)

            existe = None
            if ref_externo:
                existe = conn.execute(
                    "SELECT * FROM hardware_ocorrencia WHERE ref_externo = ?", (ref_externo,)).fetchone()

            agora = now_brt()
            if existe:
                antes = {"cliente": existe["cliente"] or "", "produto": existe["produto_nome"] or "",
                         "status": mapa_status_por_chave.get(existe["status"], {}).get("nome", existe["status"])}
                campos_set, valores_set = [], []
                if cliente and cliente != antes["cliente"]:
                    campos_set.append("cliente=?"); valores_set.append(cliente)
                if produto_nome and produto_nome != antes["produto"]:
                    campos_set += ["hardware_produto_id=?", "produto_nome=?"]
                    valores_set += [produto_id, produto_nome]
                if categoria and categoria != (existe["categoria_defeito"] or ""):
                    campos_set.append("categoria_defeito=?"); valores_set.append(categoria)
                if serial and serial != (existe["serial_patrimonio"] or ""):
                    campos_set += ["serial_patrimonio=?", "item_master_id=?"]
                    valores_set += [serial, _tentar_vincular_patrimonio(serial)]
                if responsavel and responsavel != (existe["responsavel_nome"] or ""):
                    campos_set.append("responsavel_nome=?"); valores_set.append(responsavel)
                if descricao and descricao != (existe["descricao_defeito"] or ""):
                    campos_set.append("descricao_defeito=?"); valores_set.append(descricao)
                if data_solucao and data_solucao != (existe["data_solucao"] or ""):
                    campos_set.append("data_solucao=?"); valores_set.append(data_solucao)
                if quantidade != existe["quantidade"]:
                    campos_set.append("quantidade=?"); valores_set.append(quantidade)

                if campos_set:
                    campos_set.append("atualizado_em=?"); valores_set.append(agora)
                    conn.execute(f"UPDATE hardware_ocorrencia SET {', '.join(campos_set)} WHERE id=?",
                                 (*valores_set, existe["id"]))

                status_mudou = status_chave != existe["status"]
                if status_mudou:
                    de = mapa_status_por_chave.get(existe["status"])
                    conn.execute("UPDATE hardware_ocorrencia SET status=?, atualizado_em=? WHERE id=?",
                                 (status_chave, agora, existe["id"]))
                    _evento(conn, existe["id"], "status",
                            f"Status alterado de {de['nome'] if de else existe['status']} "
                            f"para {status_nome}. (reimportação)", usuario_id=usuario_id, quando=agora)

                mudou = bool(campos_set) or status_mudou
                depois = {"cliente": cliente or antes["cliente"], "produto": produto_nome or antes["produto"],
                          "status": status_nome if status_mudou else antes["status"]}
                if mudou:
                    atualizados += 1
                else:
                    ignorados += 1
                anota(row_idx, rotulo(existe["id"]), "alterado" if mudou else "igual",
                      existe["id"], antes, depois, "; ".join(avisos_linha))
                continue

            # Linha nova -- INSERT direto (não chama criar(), que abriria uma
            # segunda conexão SQLite enquanto esta ainda está com transação
            # aberta e travaria o banco).
            cur = conn.execute(
                "INSERT INTO hardware_ocorrencia ("
                "  status, prioridade, cliente, hardware_produto_id, produto_nome, serial_patrimonio,"
                "  item_master_id, quantidade, categoria_defeito, descricao_defeito, responsavel_nome,"
                "  data_registro, data_solucao, criado_por, criado_em, atualizado_em, ativo, ref_externo"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
                (status_chave, prioridade, cliente, produto_id, produto_nome, serial,
                 _tentar_vincular_patrimonio(serial), quantidade, categoria, descricao, responsavel,
                 data_registro, data_solucao or None, usuario_id, agora, agora, ref_externo))
            nova_id = cur.lastrowid
            _evento(conn, nova_id, "criacao",
                    f"Ocorrência importada da planilha para {produto_nome or 'produto não informado'} "
                    f"({cliente}).", usuario_id=usuario_id, quando=data_registro)
            if status_chave != "nao_iniciado":
                _evento(conn, nova_id, "status", f"Status inicial da importação: {status_nome}.",
                        usuario_id=usuario_id, quando=data_registro)
            for area, texto_acao in (("brasil", valor(row, col_acao_br)),
                                      ("suecia", valor(row, col_acao_se)),
                                      ("fabricante", valor(row, col_acao_fab))):
                if texto_acao:
                    _evento(conn, nova_id, f"acao_{area}", texto_acao,
                            usuario_id=usuario_id, quando=data_registro)

            inseridos += 1
            anota(row_idx, rotulo(nova_id), "novo", nova_id, {},
                  {"cliente": cliente, "produto": produto_nome, "status": status_nome},
                  "; ".join(avisos_linha))

    return {"inseridos": inseridos, "atualizados": atualizados, "ignorados": ignorados, "itens": itens}
