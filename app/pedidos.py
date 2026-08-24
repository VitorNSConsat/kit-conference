import io
from database import db, now_brt
import app.kit_templates as templates_mod


def _campo_do_cabecalho(texto: str) -> str | None:
    """Identifica a que campo uma célula de cabeçalho corresponde, usando
    correspondência flexível (substring) — planilhas reais variam bastante
    na escrita exata (ex: 'ICCIDs', 'CDT07', 'Números Telefonicos')."""
    t = texto.strip().lower()
    if not t:
        return None
    if "iccid" in t:
        return "iccid"
    if "telefon" in t:
        return "telefone"
    if t.startswith("cdt"):
        return "cdt"
    if "hardware" in t:
        return "id_hardware"
    if "pedido" in t:
        return "numero_pedido"
    return None


def _mapear_cabecalho(row) -> dict:
    mapa = {}
    for i, c in enumerate(row):
        if c is None:
            continue
        campo = _campo_do_cabecalho(str(c))
        if campo and campo not in mapa:
            mapa[campo] = i
    return mapa


def _val(row, mapa, campo):
    idx = mapa.get(campo)
    if idx is None or idx >= len(row) or row[idx] is None:
        return None
    valor = str(row[idx]).strip()
    return valor or None


def _buscar_ou_criar_template(nome: str, cliente: str, criado_por: int) -> int:
    """Reaproveita o Pedido se já existir um com o mesmo nome/cliente (ex:
    reimportação da mesma planilha, ou o mesmo número de pedido aparecendo
    em mais de uma aba) em vez de duplicar o template."""
    with db() as conn:
        existente = conn.execute(
            "SELECT id FROM kit_template WHERE nome = ? AND cliente = ? AND tipo = 'pedido'",
            (nome, cliente)
        ).fetchone()
    if existente:
        return existente["id"]
    return templates_mod.criar_template(nome, cliente, criado_por, [], tipo="pedido")


def importar_planilha(cliente: str, numero_pedido: str, criado_por: int,
                      conteudo: bytes) -> tuple[int, dict]:
    """Cria um ou mais Pedidos a partir da planilha de unidades (ICCID,
    Número de Telefone, CDT, ID Hardware).

    Percorre TODAS as abas do arquivo. Se uma aba tiver uma coluna
    'Pedido', agrupa as linhas dela por valor dessa coluna e cria um
    Pedido para cada número encontrado — uma planilha real pode conter
    vários pedidos ao mesmo tempo, cada um com várias linhas/ID Hardware.
    Abas sem essa coluna usam o número informado manualmente no formulário
    (ou uma célula solta acima do cabeçalho) para todas as suas linhas.
    Linhas sem nenhum número de pedido identificável são ignoradas (e
    contadas) em vez de travar a importação inteira.

    Não cria itens do template — isso é feito manualmente depois, na
    tela de edição de cada pedido.
    """
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(conteudo), data_only=True)

    grupos: dict[str, list[dict]] = {}
    total_ignoradas = 0
    algum_cabecalho_encontrado = False

    for ws in wb.worksheets:
        mapa = None
        candidato = None
        past_header = False
        for row in ws.iter_rows(values_only=True):
            if not past_header:
                m = _mapear_cabecalho(row)
                if "iccid" in m or "id_hardware" in m:
                    mapa = m
                    past_header = True
                    algum_cabecalho_encontrado = True
                    continue
                if candidato is None:
                    for c in row:
                        if c is not None and str(c).strip():
                            candidato = str(c).strip()
                            break
                continue

            u = {
                "iccid": _val(row, mapa, "iccid"),
                "telefone": _val(row, mapa, "telefone"),
                "cdt": _val(row, mapa, "cdt"),
                "id_hardware": _val(row, mapa, "id_hardware"),
            }
            if not any(u.values()):
                continue

            numero_linha = _val(row, mapa, "numero_pedido") if "numero_pedido" in mapa else None
            numero = numero_linha or (numero_pedido or "").strip() or candidato
            if not numero:
                total_ignoradas += 1
                continue
            grupos.setdefault(numero, []).append(u)

    wb.close()

    if not algum_cabecalho_encontrado:
        raise ValueError("Cabeçalho com ICCID/ID Hardware não encontrado na planilha.")
    if not grupos:
        raise ValueError(
            "Nenhuma linha pôde ser associada a um número de pedido — "
            "informe manualmente no campo 'Número do Pedido' ou inclua "
            "uma coluna 'Pedido' na planilha."
        )

    template_ids = []
    total_unidades = 0
    for numero, unidades in grupos.items():
        nome = f"Pedido {numero}"
        template_id = _buscar_ou_criar_template(nome, cliente, criado_por)
        template_ids.append(template_id)
        with db() as conn:
            existentes_hw = {
                r["id_hardware"] for r in conn.execute(
                    "SELECT id_hardware FROM pedido_unidades "
                    "WHERE kit_template_id = ? AND id_hardware IS NOT NULL",
                    (template_id,)
                ).fetchall()
            }
            for u in unidades:
                # Reimportar a mesma planilha não duplica unidade — se o
                # ID Hardware já está neste pedido, pula (mas unidades sem
                # ID Hardware, ex: só CDT, sempre entram, já que não dá
                # pra identificar duplicata com segurança nesse caso).
                if u["id_hardware"] and u["id_hardware"] in existentes_hw:
                    continue
                conn.execute(
                    "INSERT INTO pedido_unidades "
                    "(kit_template_id, iccid, telefone, cdt, id_hardware, criado_em) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (template_id, u["iccid"], u["telefone"], u["cdt"], u["id_hardware"], now_brt())
                )
                if u["id_hardware"]:
                    existentes_hw.add(u["id_hardware"])
                total_unidades += 1

    return template_ids[0], {
        "unidades": total_unidades,
        "pedidos": len(grupos),
        "numeros": list(grupos.keys()),
        "ignoradas": total_ignoradas,
    }


def listar_unidades(template_id: int) -> list:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM pedido_unidades WHERE kit_template_id = ? ORDER BY id",
            (template_id,)
        ).fetchall()
    return [dict(r) for r in rows]


_CAMPOS_UNIDADE = ("iccid", "telefone", "cdt", "id_hardware")


def _limpar(valor) -> str | None:
    """Célula vazia vira NULL, não string vazia — as consultas e a
    exportação já tratam NULL como '—', e ter os dois representando 'sem
    valor' faria a mesma unidade parecer diferente de si mesma."""
    v = str(valor if valor is not None else "").strip()
    return v or None


def atualizar_unidade(unidade_id: int, campos: dict) -> bool:
    """Corrige os dados de uma unidade já cadastrada.

    É correção CADASTRAL: não mexe no template, na bipagem nem em kit
    nenhum — a unidade é só a ficha do aparelho que vai naquele pedido.
    Só os quatro campos da ficha podem mudar; template_id não entra, pra
    não existir caminho que mova a unidade de pedido por engano."""
    valores = {c: _limpar(campos.get(c)) for c in _CAMPOS_UNIDADE if c in campos}
    if not valores:
        return False
    sets = ", ".join(f"{c} = ?" for c in valores)
    with db() as conn:
        cur = conn.execute(f"UPDATE pedido_unidades SET {sets} WHERE id = ?",
                           list(valores.values()) + [unidade_id])
        return cur.rowcount > 0


def adicionar_unidades(template_id: int, linhas: list[dict]) -> dict:
    """Cadastra uma ou várias unidades à mão, sem depender da planilha.

    Linha totalmente vazia é ignorada em silêncio: o formulário nasce com
    algumas linhas em branco, e sobrar uma sem preencher é o caso normal —
    não é erro que mereça travar o envio.

    Não recusa repetido: dois aparelhos podem legitimamente chegar sem
    ICCID informado, e bloquear aqui obrigaria a inventar um valor. O que
    existe é o aviso de duplicado na tela, pra o operador decidir."""
    novas = []
    for l in linhas:
        valores = {c: _limpar(l.get(c)) for c in _CAMPOS_UNIDADE}
        if not any(valores.values()):
            continue
        novas.append(valores)
    if not novas:
        return {"inseridas": 0}
    with db() as conn:
        for v in novas:
            conn.execute(
                "INSERT INTO pedido_unidades (kit_template_id, iccid, telefone, cdt, "
                "id_hardware, criado_em) VALUES (?, ?, ?, ?, ?, ?)",
                (template_id, v["iccid"], v["telefone"], v["cdt"],
                 v["id_hardware"], now_brt()))
    return {"inseridas": len(novas)}


def remover_unidade(unidade_id: int) -> bool:
    with db() as conn:
        cur = conn.execute("DELETE FROM pedido_unidades WHERE id = ?", (unidade_id,))
        return cur.rowcount > 0


def duplicados_do_pedido(template_id: int) -> dict[str, set]:
    """Valores repetidos entre as unidades DESTE pedido, por campo.

    ICCID e ID Hardware são identificadores de aparelho: repetir quase
    sempre é erro de digitação ou linha colada duas vezes. A tela marca em
    vez de bloquear — pode haver motivo real, e travar o cadastro por causa
    disso atrapalharia mais do que ajuda."""
    repetidos = {}
    with db() as conn:
        for campo in ("iccid", "id_hardware"):
            rows = conn.execute(
                f"SELECT {campo} AS v FROM pedido_unidades "
                f"WHERE kit_template_id = ? AND TRIM(COALESCE({campo}, '')) != '' "
                f"GROUP BY {campo} HAVING COUNT(*) > 1", (template_id,)).fetchall()
            repetidos[campo] = {r["v"] for r in rows}
    return repetidos


def buscar_por_id_hardware(id_hardware: str) -> list:
    """Localiza em qual(is) pedido(s) um determinado ID Hardware está —
    usado pra permitir achar rápido qual pedido tem um equipamento."""
    with db() as conn:
        rows = conn.execute(
            "SELECT pu.*, kt.nome AS pedido_nome, kt.cliente "
            "FROM pedido_unidades pu "
            "JOIN kit_template kt ON kt.id = pu.kit_template_id "
            "WHERE pu.id_hardware LIKE ? "
            "ORDER BY kt.nome",
            (f"%{id_hardware.strip()}%",)
        ).fetchall()
    return [dict(r) for r in rows]
