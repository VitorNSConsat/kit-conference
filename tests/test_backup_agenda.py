"""Backup: cópia diária no horário, cópias de grande mudança que não se apagam."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import sqlite3
import uuid
from datetime import datetime

import pytest

os.environ["DB_PATH"] = ":memory:"
os.environ.setdefault("SECRET_KEY", "chave-de-teste-nao-usar-em-producao")

from database import init_db, db
import app.backup as backup


@pytest.fixture
def limpo():
    init_db()
    with db() as conn:
        conn.execute("DELETE FROM backup_registro")
        conn.execute("DELETE FROM backup_config")
    yield
    with db() as conn:
        conn.execute("DELETE FROM backup_registro")
        conn.execute("DELETE FROM backup_config")


def _ultima(quando, motivo="automatico"):
    with db() as conn:
        conn.execute("INSERT INTO backup_registro (criado_em, arquivo, caminho, tamanho, motivo) VALUES (?, 'x.bak', 'x', 1, ?)",
                     (quando, motivo))


def _agora(monkeypatch, texto):
    monkeypatch.setattr(backup, "_agora", lambda: datetime.strptime(texto, "%Y-%m-%d %H:%M"))


def test_diaria_sai_as_23_e_recupera_se_o_servidor_estava_desligado(limpo, monkeypatch):
    _agora(monkeypatch, "2026-11-10 10:00")
    assert backup.precisa_agora()                       # nunca houve cópia
    _ultima("2026-11-09 23:00:20")
    assert not backup.precisa_agora()
    assert backup.proximo_previsto() == datetime(2026, 11, 10, 23, 0)
    _agora(monkeypatch, "2026-11-10 22:59")
    assert not backup.precisa_agora()
    _agora(monkeypatch, "2026-11-10 23:00")
    assert backup.precisa_agora()
    # Desligado às 23:00 de ontem: faz ao voltar, e só de novo às 23:00.
    _agora(monkeypatch, "2026-11-12 10:00")
    assert backup.precisa_agora()
    _ultima("2026-11-12 10:01:00")
    assert not backup.precisa_agora()
    assert backup.proximo_previsto() == datetime(2026, 11, 12, 23, 0)


def test_outras_copias_nao_contam_como_a_diaria(limpo, monkeypatch):
    _agora(monkeypatch, "2026-11-10 23:05")
    _ultima("2026-11-10 22:00:00")
    _ultima("2026-11-10 23:01:00", "grande_antes")
    _ultima("2026-11-10 23:02:00", "manual")
    assert backup.precisa_agora()


def test_grande_mudanca_nao_entra_no_rodizio_e_nomes_nao_colidem(limpo, monkeypatch, tmp_path):
    arquivo = tmp_path / "banco.db"
    sqlite3.connect(arquivo).close()
    monkeypatch.setattr(backup, "_get_db_path", lambda: str(arquivo))
    backup.salvar_config({"manter": "1"})
    backup.backup_de_grande_mudanca("antes", "importacao", 500, "Importação X", None)
    backup.backup_de_grande_mudanca("depois", "importacao", 500, "Importação X", None)
    for _ in range(3):
        backup.criar_backup("automatico")
    nomes = sorted(os.listdir(tmp_path / "backups"))
    assert len(nomes) == 3 and len(set(nomes)) == 3     # 2 protegidas + só 1 diária
    with db() as conn:
        d = conn.execute("SELECT descricao, motivo FROM backup_registro WHERE motivo = 'grande_antes'").fetchone()
    assert d["descricao"] == "Importação X — 500 linhas"
    assert all(os.path.exists(c["caminho"]) for c in backup.listar() if c["protegida"])
    assert backup.uso_protegidas()["copias"] == 2


def test_falha_na_copia_nao_impede_e_fica_registrada(limpo, monkeypatch):
    monkeypatch.setattr(backup, "criar_backup", lambda *a, **k: (_ for _ in ()).throw(OSError("disco cheio")))
    backup.backup_de_grande_mudanca("antes", "lote", 30, "Lote", None)
    with db() as conn:
        r = conn.execute("SELECT erro, arquivo FROM backup_registro").fetchone()
    assert "disco cheio" in r["erro"] and r["arquivo"] == ""
    assert backup.ultimo() is None


def test_limites_e_horario_invalido(limpo):
    assert backup.get_config()["horario"] == "23:00"
    assert backup.eh_grande("sobressalente", 100) and not backup.eh_grande("sobressalente", 99)
    backup.salvar_config({"limite_lote": "5"})
    assert backup.eh_grande("lote", 5)
    with pytest.raises(ValueError):
        backup.salvar_config({"horario": "25:99"})


def test_rotas_so_fazem_copia_quando_a_mudanca_e_grande(limpo, monkeypatch):
    from fastapi.testclient import TestClient
    import main
    from app import usuarios
    chamadas = []
    monkeypatch.setattr(backup, "backup_de_grande_mudanca",
                        lambda momento, tipo, qtd, descricao, uid: chamadas.append((momento, tipo, qtd)))
    s = uuid.uuid4().hex[:6]
    nome = f"adm_bk_{s}"
    usuarios.criar("Adm Bk", nome, "Teste#Backup26", True)
    try:
        with TestClient(main.app) as c:
            c.post("/login", data={"username": nome, "password": "Teste#Backup26"})
            c.post("/admin/producao/transito", data={"kit_ids": [f"x{i}" for i in range(19)]})
            assert chamadas == []
            c.post("/admin/producao/transito", data={"kit_ids": [f"x{i}" for i in range(20)]})
            assert chamadas == [("antes", "lote", 20), ("depois", "lote", 20)]
            chamadas.clear()
            c.post("/admin/sobressalente", data={"cliente": "", "estoque_id": "999999", "quantidade": "150"})
            assert chamadas == [("antes", "sobressalente", 150)]     # recusou: sem cópia de depois
            assert c.get("/admin/backup").status_code == 200
    finally:
        with db() as conn:
            conn.execute("DELETE FROM users WHERE username = ?", (nome,))
