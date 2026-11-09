"""Auditoria: nada de campo repetido perdido na gravação, e a leitura em
português (título, resumo, observação, janela de detalhes)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import uuid

os.environ["DB_PATH"] = ":memory:"
os.environ.setdefault("SECRET_KEY", "chave-de-teste-nao-usar-em-producao")

from starlette.datastructures import FormData

from database import init_db, db, now_brt
import app.auditoria as aud


def _reg(caminho, detalhe, status=302, acao="ALTERACAO"):
    return {"id": 1, "acao": acao, "metodo": "POST", "caminho": caminho, "detalhe": detalhe,
            "status": status, "criado_em": "2026-10-22 19:58:00"}


def test_gravacao_guarda_todos_os_valores_de_campo_repetido():
    form = FormData([("cliente", "REDEMOB"), ("estoque_id", "1"), ("observacao", "primeira"),
                     ("estoque_id", "2"), ("observacao", ""), ("password", "segredo")])
    d = aud._resumir_form(form)
    assert d == "cliente=REDEMOB | estoque_id=1 | observacao=primeira | estoque_id=2 | observacao= | password=***"


def test_observacao_vazia_e_preenchida_aparecem_no_indicador():
    init_db()
    sem = aud.interpretar([_reg("/admin/sobressalente", "cliente=X | nome= | estoque_id=999999 | quantidade=5 | observacao=")])[0]
    assert sem["titulo"] == "Pacote de sobressalentes" and sem["obs"] == "sem"
    assert sem["resumo"] == "Cliente: X · Item do estoque: 999999 · Quantidade: 5"
    com = aud.interpretar([_reg("/admin/sobressalente", "cliente=X | observacao=Caixa 3")])[0]
    assert com["obs"] == "com"
    # O formulário nem tinha o campo: nada de "sem observação".
    assert aud.interpretar([_reg("/admin/estoque/5/ajustar", "quantidade=10 | motivo=")])[0]["obs"] is None


def test_campos_repetidos_e_numerados_viram_tabela():
    init_db()
    r = aud.interpretar([_reg("/admin/sobressalente",
                              "cliente=X | estoque_id=1 | quantidade=2 | observacao=a | estoque_id=2 | quantidade=1 | observacao=")])[0]
    assert r["tabela"]["colunas"] == ["Item do estoque", "Quantidade", "Observação"]
    assert [l[1:] for l in r["tabela"]["linhas"]] == [["2", "a"], ["1", ""]]
    assert r["obs"] == "com" and r["resumo"] == "Cliente: X · 2 itens"
    k = aud.interpretar([_reg("/admin/templates/8/edit", "nome=K | qtd_0=1 | obrigatorio_0=1 | qtd_1=3")])[0]
    assert k["titulo"] == "Edição de kit/pedido"
    assert k["tabela"]["linhas"] == [["1", "Sim"], ["3", ""]]


def test_frase_livre_e_barra_dentro_do_valor():
    init_db()
    f = aud.interpretar([_reg("/admin/producao/nota-lote", "6 kit(s): nota fiscal definida para 230")])[0]
    assert f["texto_livre"] and f["resumo"].startswith("6 kit(s)") and f["campos"] == []
    b = aud.interpretar([_reg("/x", "nome=A | B | quantidade=1")])[0]
    assert [c["valor"] for c in b["campos"]] == ["A | B", "1"]


def test_tela_e_janela_mostram_o_pacote_com_a_observacao():
    from fastapi.testclient import TestClient
    import main
    from app import usuarios
    init_db()
    s = uuid.uuid4().hex[:6]
    nome = f"adm_aud_{s}"
    usuarios.criar("Adm Aud", nome, "Teste#Audit26", True)
    agora = now_brt()
    with db() as conn:
        uid = conn.execute("SELECT id FROM users WHERE username = ?", (nome,)).fetchone()[0]
        conn.execute("INSERT INTO item_tipo (nome, criado_em) VALUES (?, '2026-01-01')", (f"Fusivel {s}",))
        tid = conn.execute("SELECT id FROM item_tipo WHERE nome = ?", (f"Fusivel {s}",)).fetchone()[0]
        conn.execute("INSERT INTO estoque (item_tipo_id, codigo_barra, quantidade_atual, quantidade_minima, criado_em) "
                     "VALUES (?, ?, 10, 1, '2026-01-01')", (tid, f"F-{s}"))
        eid = conn.execute("SELECT id FROM estoque WHERE item_tipo_id = ?", (tid,)).fetchone()[0]
        conn.execute("INSERT INTO sobressalente_pacote (cliente, nome, criado_em, criado_por) VALUES (?, 'Caixa', ?, ?)",
                     (f"Cli{s}", agora, uid))
        pid = conn.execute("SELECT id FROM sobressalente_pacote WHERE cliente = ?", (f"Cli{s}",)).fetchone()[0]
        conn.execute("INSERT INTO sobressalente_pacote_item (pacote_id, estoque_id, quantidade, observacao) VALUES (?, ?, 2, ?)",
                     (pid, eid, f"obs-{s}"))
    aud.registrar(uid, "Adm Aud", "ALTERACAO", "POST", "/admin/sobressalente",
                  f"cliente=Cli{s} | nome=Caixa | estoque_id={eid} | quantidade=2 | observacao=", "10.0.0.9", 302)
    aud.registrar(uid, "Adm Aud", "ALTERACAO", "POST", "/admin/sobressalente",
                  "cliente=OutroCli | estoque_id=999999 | quantidade=2 | observacao=", "10.0.0.9", 302)
    try:
        with db() as conn:
            rid_ok, rid_sem = [r[0] for r in conn.execute(
                "SELECT id FROM auditoria WHERE user_id = ? AND caminho = '/admin/sobressalente' ORDER BY id", (uid,))]
        with TestClient(main.app) as c:
            c.post("/login", data={"username": nome, "password": "Teste#Audit26"})
            h = c.get("/admin/auditoria", params={"busca": f"Cli{s}"}).text
            assert "Pacote de sobressalentes" in h and "Sem observação" in h and "abrirDetalheAuditoria" in h
            # Busca pelo que a tela mostra: nome do item (o registro só tem o id) e título.
            assert aud.contar(busca=f"Fusivel {s}") == 1
            assert aud.contar(busca="pacote sobressalentes OutroCli") == 1
            j = c.get(f"/admin/auditoria/{rid_ok}/painel").text
            assert f"obs-{s}" in j and f"Fusivel {s}" in j and "10.0.0.9" in j
            n = c.get(f"/admin/auditoria/{rid_sem}/painel").text
            assert "Não há pacote criado" in n
            assert c.get("/admin/auditoria/999999999/painel").status_code == 404
    finally:
        with db() as conn:
            conn.execute("DELETE FROM sobressalente_pacote_item WHERE pacote_id = ?", (pid,))
            conn.execute("DELETE FROM sobressalente_pacote WHERE id = ?", (pid,))
            conn.execute("DELETE FROM estoque WHERE id = ?", (eid,))
            conn.execute("DELETE FROM item_tipo WHERE id = ?", (tid,))
            conn.execute("DELETE FROM auditoria WHERE user_id = ?", (uid,))
            conn.execute("DELETE FROM users WHERE id = ?", (uid,))



def test_importacao_grande_lista_tudo_em_paginas():
    from fastapi.testclient import TestClient
    import main
    from app import usuarios
    init_db()
    s = uuid.uuid4().hex[:6]
    nome = f"adm_imp_{s}"
    usuarios.criar("Adm Imp", nome, "Teste#Import26", True)
    agora = now_brt()
    with db() as conn:
        uid = conn.execute("SELECT id FROM users WHERE username = ?", (nome,)).fetchone()[0]
        conn.execute("INSERT INTO importacao (arquivo, criada_em, criada_por, total, novos, iguais, alterados, erros, avisos) "
                     "VALUES ('v.xlsx', ?, ?, 60, 60, 0, 0, 0, 0)", (agora, uid))
        imp = conn.execute("SELECT id FROM importacao WHERE criada_por = ?", (uid,)).fetchone()[0]
        conn.executemany("INSERT INTO importacao_item (importacao_id, linha, numero, situacao, cliente_depois) VALUES (?, ?, ?, 'novo', 'C')",
                         [(imp, i, f"V{s}-{i:03d}") for i in range(1, 61)])
    aud.registrar(uid, "Adm Imp", "IMPORTACAO", "POST", "/admin/veiculos/import/previa/abc/confirmar", "60 novos", "", 302)
    aud.registrar(uid, "Adm Imp", "ALTERACAO", "POST", "/admin/producao/transito", "20 kit(s) movido(s) para Em trânsito", "", 302)
    aud.registrar(uid, "Adm Imp", "ALTERACAO", "POST", "/admin/producao/nota-lote", "2 kit(s): nota 5", "", 302,
                  dados="nota_fiscal=5 | kit_ids=aaa | kit_ids=bbb")
    try:
        with db() as conn:
            r_imp, r_tot, r_lote = [x[0] for x in conn.execute("SELECT id FROM auditoria WHERE user_id = ? ORDER BY id", (uid,))]
        with TestClient(main.app) as c:
            c.post("/login", data={"username": nome, "password": "Teste#Import26"})
            p1 = c.get(f"/admin/auditoria/{r_imp}/painel").text
            assert f"V{s}-001" in p1 and f"V{s}-026" not in p1 and "1–25 de 60" in p1 and "página 1 de 3" in p1
            p3 = c.get(f"/admin/auditoria/{r_imp}/painel", params={"pagina": 3}).text
            assert f"V{s}-060" in p3 and "51–60 de 60" in p3
            assert "guardou só o total" in c.get(f"/admin/auditoria/{r_tot}/painel").text
            l = c.get(f"/admin/auditoria/{r_lote}/painel").text
            assert "aaa" in l and "bbb" in l and "Itens enviados" in l and "guardou só o total" not in l
    finally:
        with db() as conn:
            conn.execute("DELETE FROM importacao_item WHERE importacao_id = ?", (imp,))
            conn.execute("DELETE FROM importacao WHERE id = ?", (imp,))
            conn.execute("DELETE FROM auditoria WHERE user_id = ?", (uid,))
            conn.execute("DELETE FROM users WHERE id = ?", (uid,))
