import sqlite3
import os
from contextlib import contextmanager
from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.getenv("DB_PATH", "kit_conference.db")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
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

            CREATE TABLE IF NOT EXISTS item_master (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                codigo_barra TEXT UNIQUE NOT NULL,
                descricao TEXT NOT NULL,
                unidade TEXT NOT NULL DEFAULT 'UN',
                categoria TEXT,
                controla_serial BOOLEAN DEFAULT 0,
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

            CREATE TABLE IF NOT EXISTS kit_template_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kit_template_id INTEGER NOT NULL REFERENCES kit_template(id),
                codigo_barra TEXT NOT NULL,
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

            CREATE TABLE IF NOT EXISTS scan_session_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sessao_id INTEGER NOT NULL REFERENCES scan_session(id),
                codigo_barra TEXT NOT NULL,
                serial TEXT,
                bipado_em DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS kit_record (
                kit_id TEXT PRIMARY KEY,
                sessao_id INTEGER NOT NULL REFERENCES scan_session(id),
                kit_template_id INTEGER NOT NULL REFERENCES kit_template(id),
                kit_template_versao INTEGER NOT NULL,
                operador_id INTEGER NOT NULL REFERENCES users(id),
                finalizado_em DATETIME DEFAULT CURRENT_TIMESTAMP,
                status TEXT NOT NULL DEFAULT 'ativo'
            );

            CREATE TABLE IF NOT EXISTS print_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kit_id TEXT NOT NULL REFERENCES kit_record(kit_id),
                zpl TEXT NOT NULL,
                solicitado_por INTEGER NOT NULL REFERENCES users(id),
                solicitado_em DATETIME DEFAULT CURRENT_TIMESTAMP,
                status TEXT NOT NULL DEFAULT 'aguardando',
                impresso_em DATETIME
            );
        """)
