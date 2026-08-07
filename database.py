import sqlite3
import os
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

_BRT = timezone(timedelta(hours=-3))


def now_brt() -> str:
    """Retorna o horário atual em BRT (UTC-3) formatado para armazenar no banco."""
    return datetime.now(tz=_BRT).strftime('%Y-%m-%d %H:%M:%S')

load_dotenv()

DB_PATH = os.getenv("DB_PATH", "kit_conference.db")

# Anchor connection keeps the shared in-memory DB alive during tests.
# Only used when DB_PATH == ":memory:".
_memory_anchor: sqlite3.Connection | None = None


def _get_db_path():
    """Re-reads DB_PATH from env to support test overrides set before import."""
    return os.getenv("DB_PATH", DB_PATH)


def _ensure_memory_anchor(path: str):
    """When using :memory:, keep one persistent connection so the DB survives."""
    global _memory_anchor
    if path == ":memory:" and _memory_anchor is None:
        _memory_anchor = sqlite3.connect(
            "file::memory:?cache=shared", uri=True, check_same_thread=False
        )


def get_connection():
    path = _get_db_path()
    _ensure_memory_anchor(path)
    if path == ":memory:":
        conn = sqlite3.connect(
            "file::memory:?cache=shared", uri=True, check_same_thread=False
        )
    else:
        conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


@contextmanager
def db():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _backup_antes_de_migrar() -> str | None:
    """Copia o banco antes de aplicar mudanças de estrutura.

    Roda só quando há migração pendente (não a cada startup), e nunca no
    banco em memória dos testes. Se o backup falhar por qualquer motivo,
    a migração é abortada — é preferível o sistema não subir a alterar um
    banco de produção sem cópia de segurança.
    """
    import shutil
    from datetime import datetime as _dt

    path = _get_db_path()
    if path == ":memory:" or not os.path.exists(path):
        return None

    with db() as conn:
        colunas_users = {r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
        colunas_kit_record = {r["name"] for r in conn.execute("PRAGMA table_info(kit_record)").fetchall()}
        colunas_estoque = {r["name"] for r in conn.execute("PRAGMA table_info(estoque)").fetchall()}
        colunas_scan_session = {r["name"] for r in conn.execute("PRAGMA table_info(scan_session)").fetchall()}
        colunas_veiculos = {r["name"] for r in conn.execute("PRAGMA table_info(veiculos)").fetchall()}
        colunas_scan_session_items = {r["name"] for r in conn.execute("PRAGMA table_info(scan_session_items)").fetchall()}
        colunas_estoque_movimentos = {r["name"] for r in conn.execute("PRAGMA table_info(estoque_movimentos)").fetchall()}
        tabelas = {
            r["name"] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    pendente = (
        "admin" not in colunas_users
        or "auditoria" not in tabelas
        or "status_producao" not in colunas_kit_record
        or "nota_fiscal" not in colunas_kit_record
        or "user_permissoes_negadas" not in tabelas
        or "status_compra" not in colunas_estoque
        or "garagem" not in colunas_scan_session
        or "sequencia" not in colunas_scan_session
        or "liberado_em" not in colunas_veiculos
        or "producao_sequencia" not in tabelas
        or "operador_id" not in colunas_scan_session_items
        or "cliente" not in colunas_estoque_movimentos
    )
    if not pendente:
        return None

    destino_dir = os.path.join(os.path.dirname(os.path.abspath(path)) or ".", "backups")
    os.makedirs(destino_dir, exist_ok=True)
    carimbo = _dt.now().strftime("%Y%m%d_%H%M%S")
    base = os.path.basename(path)
    destino = os.path.join(destino_dir, f"{base}.{carimbo}.bak")
    shutil.copy2(path, destino)
    print(f"[KIT] Backup do banco criado antes da migracao: {destino}")
    return destino


def init_db():
    _backup_antes_de_migrar()
    with db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                criado_em DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            -- Tipos de item pré-configurados (ex: "Antena 5dBi", "Roteador TP-Link")
            CREATE TABLE IF NOT EXISTS item_tipo (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL UNIQUE,
                ativo BOOLEAN DEFAULT 1,
                criado_em DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            -- Patrimônios registrados (cada peça física tem código único)
            CREATE TABLE IF NOT EXISTS item_master (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                codigo_barra TEXT UNIQUE NOT NULL,
                item_tipo_id INTEGER NOT NULL REFERENCES item_tipo(id),
                ativo BOOLEAN DEFAULT 1,
                criado_por INTEGER REFERENCES users(id),
                criado_em DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS kit_template (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                cliente TEXT NOT NULL,
                versao INTEGER NOT NULL DEFAULT 1,
                ativo BOOLEAN DEFAULT 1,
                criado_por INTEGER REFERENCES users(id),
                criado_em DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            -- Itens do template referenciam TIPO, não código de barras específico
            CREATE TABLE IF NOT EXISTS kit_template_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kit_template_id INTEGER NOT NULL REFERENCES kit_template(id),
                item_tipo_id INTEGER NOT NULL REFERENCES item_tipo(id),
                quantidade_exigida INTEGER NOT NULL,
                obrigatorio BOOLEAN DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS scan_session (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kit_template_id INTEGER NOT NULL REFERENCES kit_template(id),
                kit_template_versao INTEGER NOT NULL,
                operador_id INTEGER NOT NULL REFERENCES users(id),
                status TEXT NOT NULL DEFAULT 'em_andamento',
                iniciado_em DATETIME DEFAULT CURRENT_TIMESTAMP,
                finalizado_em DATETIME
            );

            -- Cada bip registra o código de patrimônio e seu tipo
            CREATE TABLE IF NOT EXISTS scan_session_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sessao_id INTEGER NOT NULL REFERENCES scan_session(id),
                codigo_barra TEXT NOT NULL,
                item_tipo_id INTEGER NOT NULL REFERENCES item_tipo(id),
                bipado_em DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS kit_record (
                kit_id TEXT PRIMARY KEY,
                sessao_id INTEGER NOT NULL REFERENCES scan_session(id),
                kit_template_id INTEGER NOT NULL REFERENCES kit_template(id),
                kit_template_versao INTEGER NOT NULL,
                operador_id INTEGER NOT NULL REFERENCES users(id),
                veiculo TEXT DEFAULT '',
                garagem TEXT DEFAULT '',
                finalizado_em DATETIME DEFAULT CURRENT_TIMESTAMP,
                status TEXT NOT NULL DEFAULT 'ativo'
            );

            CREATE TABLE IF NOT EXISTS print_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kit_id TEXT NOT NULL REFERENCES kit_record(kit_id),
                zpl TEXT NOT NULL,
                html_label TEXT,
                solicitado_por INTEGER NOT NULL REFERENCES users(id),
                solicitado_em DATETIME DEFAULT CURRENT_TIMESTAMP,
                status TEXT NOT NULL DEFAULT 'aguardando',
                impresso_em DATETIME
            );
        """)
        # Tabelas de estoque (criadas apenas se não existirem)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS estoque (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_tipo_id INTEGER UNIQUE NOT NULL REFERENCES item_tipo(id),
                codigo_barra TEXT UNIQUE NOT NULL,
                quantidade_atual INTEGER NOT NULL DEFAULT 0,
                quantidade_minima INTEGER NOT NULL DEFAULT 0,
                criado_em DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS estoque_movimentos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                estoque_id INTEGER NOT NULL REFERENCES estoque(id),
                tipo TEXT NOT NULL,
                quantidade INTEGER NOT NULL,
                sessao_id INTEGER REFERENCES scan_session(id),
                observacao TEXT,
                criado_por INTEGER REFERENCES users(id),
                criado_em DATETIME DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Tabela de validações de kits
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS kit_validacoes (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                kit_id       TEXT    NOT NULL REFERENCES kit_record(kit_id),
                validado_por INTEGER NOT NULL REFERENCES users(id),
                validado_em  TEXT    NOT NULL,
                observacao   TEXT
            );
        """)

        conn.executescript("""
            CREATE TABLE IF NOT EXISTS veiculos (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                numero    TEXT NOT NULL,
                cliente   TEXT NOT NULL,
                garagem   TEXT NOT NULL DEFAULT '',
                ativo     INTEGER NOT NULL DEFAULT 1,
                criado_em TEXT NOT NULL
            );
        """)

        conn.executescript("""
            CREATE TABLE IF NOT EXISTS clientes (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                nome      TEXT NOT NULL UNIQUE,
                criado_em TEXT NOT NULL
            );
        """)

        conn.executescript("""
            CREATE TABLE IF NOT EXISTS garagens (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                nome      TEXT NOT NULL UNIQUE,
                criado_em TEXT NOT NULL
            );
        """)

        # Trilha de auditoria: toda ação que altera dados, de admin ou não.
        # user_id sem FOREIGN KEY de propósito — o log precisa sobreviver à
        # exclusão do usuário, senão some justamente o rastro de quem saiu.
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS auditoria (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER,
                user_nome   TEXT,
                acao        TEXT NOT NULL,
                metodo      TEXT NOT NULL,
                caminho     TEXT NOT NULL,
                detalhe     TEXT,
                ip          TEXT,
                status      INTEGER,
                criado_em   TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_auditoria_criado_em ON auditoria(criado_em);
            CREATE INDEX IF NOT EXISTS idx_auditoria_user ON auditoria(user_id);

            -- Lista de bloqueios por usuário comum: a PRESENÇA de uma linha
            -- nega aquela permissão. Ausência = permitido — assim nenhum
            -- usuário existente perde acesso quando essa tabela nasce vazia.
            -- Admin nunca é consultado aqui (sempre passa).
            CREATE TABLE IF NOT EXISTS user_permissoes_negadas (
                user_id INTEGER NOT NULL REFERENCES users(id),
                chave   TEXT NOT NULL,
                PRIMARY KEY (user_id, chave)
            );
        """)

        conn.executescript("""
            CREATE TABLE IF NOT EXISTS codigo_gerado (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                texto      TEXT NOT NULL UNIQUE,
                reciclavel BOOLEAN NOT NULL DEFAULT 0,
                criado_por INTEGER REFERENCES users(id),
                criado_em  TEXT NOT NULL
            );
        """)

        conn.executescript("""
            CREATE TABLE IF NOT EXISTS prateleira_layout (
                id      INTEGER PRIMARY KEY CHECK (id = 1),
                linhas  INTEGER NOT NULL DEFAULT 4,
                colunas INTEGER NOT NULL DEFAULT 3
            );

            CREATE TABLE IF NOT EXISTS prateleira_colunas (
                coluna INTEGER PRIMARY KEY,
                nome   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS prateleira_blocos (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                linha_ini  INTEGER NOT NULL,
                linha_fim  INTEGER NOT NULL,
                coluna_ini INTEGER NOT NULL,
                coluna_fim INTEGER NOT NULL,
                estoque_id INTEGER NOT NULL REFERENCES estoque(id),
                criado_em  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS prateleira_livre (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                estoque_id INTEGER NOT NULL REFERENCES estoque(id),
                criado_em  TEXT NOT NULL
            );
        """)

        conn.execute("INSERT OR IGNORE INTO prateleira_layout (id, linhas, colunas) VALUES (1, 4, 3)")
        for _col_idx, _col_nome in enumerate(["Porta", "Meio", "Lab"], 1):
            conn.execute(
                "INSERT OR IGNORE INTO prateleira_colunas (coluna, nome) VALUES (?, ?)",
                (_col_idx, _col_nome)
            )

        # Migração: prateleira_posicoes (1 posição fixa por item) virou
        # prateleira_blocos (região com linha/coluna início e fim — um item
        # pode ter vários blocos, e um bloco pode cobrir várias células).
        tem_posicoes_antiga = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='prateleira_posicoes'"
        ).fetchone()
        if tem_posicoes_antiga:
            conn.executescript("""
                INSERT INTO prateleira_blocos
                    (linha_ini, linha_fim, coluna_ini, coluna_fim, estoque_id, criado_em)
                    SELECT linha, linha, coluna, coluna, estoque_id, criado_em
                    FROM prateleira_posicoes;
                DROP TABLE prateleira_posicoes;
            """)

        conn.executescript("""
            CREATE TABLE IF NOT EXISTS pedido_unidades (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                kit_template_id INTEGER NOT NULL REFERENCES kit_template(id),
                iccid           TEXT,
                telefone        TEXT,
                cdt             TEXT,
                id_hardware     TEXT,
                criado_em       TEXT NOT NULL
            );

            -- Contador global do numero de sequencia impresso na etiqueta
            -- "Em Andamento" (linha unica, tipo singleton — mesmo padrao de
            -- prateleira_layout). Continuo por padrao; admin pode zerar.
            CREATE TABLE IF NOT EXISTS producao_sequencia (
                id    INTEGER PRIMARY KEY CHECK (id = 1),
                valor INTEGER NOT NULL DEFAULT 0
            );

            -- Fila de impressao das etiquetas "Em Andamento" (kit ainda sendo
            -- bipado, nao existe kit_record ainda) — separada de print_queue
            -- (que exige kit_id NOT NULL referenciando um kit ja finalizado)
            -- pra nao precisar reconstruir aquela tabela. As duas aparecem
            -- juntas na tela de Fila de Impressao.
            CREATE TABLE IF NOT EXISTS print_queue_pausa (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                sessao_id      INTEGER NOT NULL REFERENCES scan_session(id),
                html_label     TEXT NOT NULL,
                solicitado_por INTEGER NOT NULL REFERENCES users(id),
                solicitado_em  DATETIME DEFAULT CURRENT_TIMESTAMP,
                status         TEXT NOT NULL DEFAULT 'aguardando',
                impresso_em    DATETIME
            );
        """)
        conn.execute("INSERT OR IGNORE INTO producao_sequencia (id, valor) VALUES (1, 0)")

        # Migrations (no-op when column already exists)
        for stmt in [
            "ALTER TABLE kit_template_items ADD COLUMN componente_codigo TEXT",
            "ALTER TABLE kit_template_items ADD COLUMN requer_serial BOOLEAN DEFAULT 0",
            "ALTER TABLE scan_session_items ADD COLUMN serial_number TEXT",
            "ALTER TABLE scan_session_items ADD COLUMN status TEXT DEFAULT 'completo'",
            "ALTER TABLE kit_record ADD COLUMN veiculo_id INTEGER REFERENCES veiculos(id)",
            "ALTER TABLE scan_session_items ADD COLUMN observacao TEXT",
            "ALTER TABLE scan_session_items ADD COLUMN quantidade INTEGER DEFAULT 1",
            "ALTER TABLE item_tipo ADD COLUMN unidade TEXT DEFAULT 'un'",
            "ALTER TABLE item_tipo ADD COLUMN reutilizavel BOOLEAN DEFAULT 0",
            "ALTER TABLE item_tipo ADD COLUMN codigo_fixo TEXT",
            "ALTER TABLE item_tipo ADD COLUMN controle_externo BOOLEAN DEFAULT 0",
            "ALTER TABLE item_tipo ADD COLUMN requer_serial BOOLEAN DEFAULT 0",
            "ALTER TABLE kit_template ADD COLUMN tipo TEXT NOT NULL DEFAULT 'kit'",
            "ALTER TABLE kit_template ADD COLUMN concluido BOOLEAN DEFAULT 0",
            "ALTER TABLE kit_record ADD COLUMN modelo TEXT DEFAULT ''",
            "ALTER TABLE users ADD COLUMN admin BOOLEAN NOT NULL DEFAULT 0",
            "ALTER TABLE users ADD COLUMN ativo BOOLEAN NOT NULL DEFAULT 1",
            # Esteira Consat -> Transito -> Cliente. 'produzido' e o estado
            # inicial de todo kit_record — a etapa "em producao" (Consat)
            # nao mora aqui, vem de scan_session.status='em_andamento'.
            "ALTER TABLE kit_record ADD COLUMN status_producao TEXT NOT NULL DEFAULT 'produzido'",
            "ALTER TABLE kit_record ADD COLUMN transito_em TEXT",
            "ALTER TABLE kit_record ADD COLUMN cliente_instalando_em TEXT",
            "ALTER TABLE kit_record ADD COLUMN cliente_concluido_em TEXT",
            # Controle interno — nao afeta a esteira, so registro manual.
            "ALTER TABLE kit_record ADD COLUMN nota_fiscal TEXT DEFAULT ''",
            "ALTER TABLE kit_record ADD COLUMN nota_fiscal_data TEXT",
            # Status de compra do item — independente do status de quantidade
            # (abaixo/proximo/ok). Vazio = sem pendencia.
            "ALTER TABLE estoque ADD COLUMN status_compra TEXT NOT NULL DEFAULT ''",
            # Destino (veiculo/garagem/modelo) escolhido ANTES de comecar a bipar
            # — nao mais perguntado so na finalizacao. garagem vazia = destino
            # ainda nao definido (session_page redireciona pra /destino nesse caso).
            "ALTER TABLE scan_session ADD COLUMN veiculo_id INTEGER REFERENCES veiculos(id)",
            "ALTER TABLE scan_session ADD COLUMN veiculo TEXT DEFAULT ''",
            "ALTER TABLE scan_session ADD COLUMN garagem TEXT DEFAULT ''",
            "ALTER TABLE scan_session ADD COLUMN modelo TEXT DEFAULT ''",
            # Numero de sequencia da etiqueta "Em Andamento" — atribuido na
            # 1a impressao, fica fixo depois (reimprimir nao muda o numero).
            "ALTER TABLE scan_session ADD COLUMN sequencia INTEGER",
            # Marca que um admin/operador liberou esse veiculo pra uma nova
            # bipagem mesmo já tendo kit(s) associados. É consumido (volta
            # pra NULL) assim que uma nova sessão/kit é criado pra ele.
            "ALTER TABLE veiculos ADD COLUMN liberado_em TEXT",
            # Quem bipou cada item — permite que mais de um operador
            # continue a mesma sessão e o sistema saiba atribuir por item.
            # Nullable: linhas bipadas antes desta coluna existir ficam sem
            # atribuição (aparecem como "Sem operador registrado").
            "ALTER TABLE scan_session_items ADD COLUMN operador_id INTEGER REFERENCES users(id)",
            # Cliente vinculado a uma baixa de estoque do tipo 'sobressalente'
            # — peça extra enviada numa instalação, fora do que o kit já
            # bipado contabiliza. Nula pra todo o resto dos tipos de
            # movimento (entrada, saida, correcao, etc).
            "ALTER TABLE estoque_movimentos ADD COLUMN cliente TEXT",
        ]:
            try:
                conn.execute(stmt)
            except Exception:
                pass

    # ── Backfill único de admin ────────────────────────────────────────────
    # A coluna `admin` nasce com default 0. Sem isto, ao atualizar um sistema
    # já em uso TODO MUNDO perderia de uma vez a permissão de excluir —
    # inclusive quem administra. Então, na primeira vez (e só nela), quem já
    # existia vira admin: ninguém perde acesso que tinha ontem, e a partir
    # daí o rebaixamento é feito conscientemente na tela de usuários.
    #
    # Controlado por PRAGMA user_version, não por "existe algum admin?" —
    # senão, no dia em que sobrasse zero admin, o startup silenciosamente
    # promoveria todo mundo de novo e desfaria a configuração.
    with db() as conn:
        versao = conn.execute("PRAGMA user_version").fetchone()[0]
        if versao < 1:
            n = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            if n:
                conn.execute("UPDATE users SET admin = 1")
                print(f"[KIT] {n} usuario(s) existente(s) marcado(s) como ADMIN para nao "
                      f"perder acesso. Revise em /admin/usuarios e rebaixe quem for comum.")
            conn.execute("PRAGMA user_version = 1")

    # Backfill clientes from existing free-text data (no-op when already present)
    ts = now_brt()
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO clientes (nome, criado_em) "
            "SELECT DISTINCT cliente, ? FROM kit_template WHERE cliente != ''",
            (ts,)
        )
        conn.execute(
            "INSERT OR IGNORE INTO clientes (nome, criado_em) "
            "SELECT DISTINCT cliente, ? FROM veiculos WHERE cliente != ''",
            (ts,)
        )
        conn.execute(
            "INSERT OR IGNORE INTO garagens (nome, criado_em) "
            "SELECT DISTINCT garagem, ? FROM veiculos WHERE garagem != ''",
            (ts,)
        )
