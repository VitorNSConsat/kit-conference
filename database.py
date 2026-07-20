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


def init_db():
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
        ]:
            try:
                conn.execute(stmt)
            except Exception:
                pass

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
