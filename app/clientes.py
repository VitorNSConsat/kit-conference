import hashlib
import sqlite3

from database import db, now_brt

# ── Cor de cada cliente ──────────────────────────────────────────────────
# Matiz calculada, não uma lista de cores fixa: com dez cores prontas, dois
# clientes caírem na mesma é questão de tempo — e duas frotas da mesma cor é
# pior do que cor nenhuma.
#
# A posição no círculo vem do ID do cadastro multiplicado pelo ângulo áureo
# (137,5°), que é a forma clássica de espalhar N pontos num círculo sem
# aglomerar: clientes criados em sequência caem BEM longe um do outro, em vez
# de vizinhos de 2° que o olho não separa. Como o id nunca muda, a cor do
# cliente também não — nem quando outro cliente é criado ou apagado.
_ANGULO_OURO = 137.508
_SATURACAO, _LUMINOSIDADE = 72, 38   # fixos: toda matiz sai legível no branco
# 0°–24° e 336°–360° são o vermelho dos avisos. Nesta interface vermelho quer
# dizer problema; um cliente vermelho pareceria um alerta.
_MATIZ_INICIAL, _MATIZ_FAIXA = 24, 312
_CINZA_SEM_CLIENTE = "#8a97a4"

_ids_por_nome: dict[str, int] = {}


def _posicao(chave: str) -> int:
    """O id do cliente no cadastro — a "vez" dele na roda de cores.

    Em cache porque isto é chamado uma vez por LINHA de lista (centenas), e
    uma consulta por linha seria a diferença entre abrir na hora e travar.
    Nome que não está no cadastro (cliente apagado que ficou no veículo) cai
    no hash do nome: cor estável do mesmo jeito, sem depender de id."""
    if chave not in _ids_por_nome:
        _recarregar_ids()
    pos = _ids_por_nome.get(chave)
    if pos is not None:
        return pos
    return int(hashlib.md5(chave.encode("utf-8")).hexdigest()[:8], 16)


def _recarregar_ids() -> None:
    global _ids_por_nome
    try:
        with db() as conn:
            _ids_por_nome = {
                (r["nome"] or "").strip().upper(): r["id"]
                for r in conn.execute("SELECT id, nome FROM clientes").fetchall()
            }
    except Exception:
        _ids_por_nome = {}


def cor_do_cliente(nome: str) -> str:
    """Sempre a MESMA cor pro mesmo cliente, sem guardar nada no banco.

    Cliente novo já nasce com cor; ninguém escolhe nada. A garagem usa a cor
    do CLIENTE dela: as duas são texto livre e parecidas na tela, e cores
    diferentes fariam parecer mundos separados."""
    chave = (nome or "").strip().upper()
    if not chave:
        return _CINZA_SEM_CLIENTE     # sem cliente, sem identidade
    matiz = _MATIZ_INICIAL + int(_posicao(chave) * _ANGULO_OURO) % _MATIZ_FAIXA
    return f"hsl({matiz}, {_SATURACAO}%, {_LUMINOSIDADE}%)"


def listar() -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT id, nome, criado_em FROM clientes ORDER BY nome"
        ).fetchall()
    return [dict(r) for r in rows]


def buscar(cliente_id: int) -> dict | None:
    with db() as conn:
        row = conn.execute(
            "SELECT id, nome, prefixo, criado_em FROM clientes WHERE id = ?", (cliente_id,)
        ).fetchone()
    return dict(row) if row else None


def buscar_prefixo(nome: str) -> str:
    """Prefixo de numeração cadastrado pra este cliente (vazio se não tiver
    ou não existir). Comparação sem caixa/espaço, igual ao resto do
    cadastro de cliente."""
    nome = (nome or "").strip()
    if not nome:
        return ""
    with db() as conn:
        row = conn.execute(
            "SELECT prefixo FROM clientes WHERE UPPER(TRIM(nome)) = UPPER(?)", (nome,)
        ).fetchone()
    return (row["prefixo"] or "").strip() if row else ""


def formatar_numero(numero: str, prefixo: str) -> str:
    """Monta PREFIXO-00001 a partir do número cru (ex: "01") — só quando o
    cliente tem prefixo configurado E o número digitado é só dígitos.
    Número que já vem formatado (com traço, letra, espaço) não é tocado:
    é o que permite reimportar uma planilha já no padrão novo sem
    prefixar de novo, e continuar aceitando número fora do padrão."""
    numero = (numero or "").strip()
    prefixo = (prefixo or "").strip()
    if not prefixo or not numero or not numero.isdigit():
        return numero
    return f"{prefixo}-{int(numero):05d}"


def atualizar_prefixo(cliente_id: int, prefixo: str) -> None:
    with db() as conn:
        conn.execute(
            "UPDATE clientes SET prefixo = ? WHERE id = ?",
            ((prefixo or "").strip().upper(), cliente_id)
        )


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
    _recarregar_ids()   # o cache de cores não pode ficar com quem não existe mais


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
            novo = cur.lastrowid
        _recarregar_ids()   # cliente novo já sai colorido na primeira tela
        return novo
    except sqlite3.IntegrityError:
        return None
