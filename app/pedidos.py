import io
from database import db, now_brt
import app.kit_templates as templates_mod

_ALIASES = {
    "iccid": "iccid",
    "numero de telefone": "telefone", "número de telefone": "telefone",
    "telefone": "telefone", "numero de telefone ": "telefone",
    "cdt": "cdt",
    "id hardware": "id_hardware", "id_hardware": "id_hardware", "hardware": "id_hardware",
}


def _detectar_header(ws):
    """Procura a linha com o cabeçalho ICCID/Telefone/CDT/ID Hardware.
    Retorna (indice_da_linha, mapa_coluna->campo, candidato_numero_pedido).
    O candidato a número do pedido é o primeiro valor não vazio encontrado
    em alguma linha ANTES do cabeçalho (célula solta acima da tabela)."""
    candidato_numero = None
    for row in ws.iter_rows(values_only=True):
        cells = [str(c).strip().lower() if c is not None else "" for c in row]
        mapa = {}
        for i, c in enumerate(cells):
            if c in _ALIASES:
                mapa[_ALIASES[c]] = i
        if "iccid" in mapa:
            return mapa, candidato_numero
        if candidato_numero is None:
            for c in row:
                if c is not None and str(c).strip():
                    candidato_numero = str(c).strip()
                    break
    return None, candidato_numero


def importar_planilha(cliente: str, numero_pedido: str, criado_por: int,
                      conteudo: bytes) -> tuple[int, dict]:
    """Cria um Pedido a partir da planilha de unidades (ICCID, Número de
    Telefone, CDT, ID Hardware) — diferente do BOM do Kit: aqui não se
    criam itens do template (isso é feito manualmente depois, na tela de
    edição do pedido); as linhas só ficam guardadas para consulta."""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(conteudo), data_only=True)
    ws = wb.active

    mapa, candidato = _detectar_header(ws)
    if mapa is None:
        wb.close()
        raise ValueError("Cabeçalho com ICCID não encontrado na planilha.")

    numero = (numero_pedido or "").strip() or (candidato or "")
    if not numero:
        wb.close()
        raise ValueError(
            "Não foi possível identificar o número do pedido na planilha — "
            "informe manualmente no campo 'Número do Pedido'."
        )

    def _val(row, campo):
        idx = mapa.get(campo)
        if idx is None or idx >= len(row) or row[idx] is None:
            return None
        valor = str(row[idx]).strip()
        return valor or None

    unidades = []
    past_header = False
    for row in ws.iter_rows(values_only=True):
        cells = [str(c).strip().lower() if c is not None else "" for c in row]
        if not past_header:
            if "iccid" in cells:
                past_header = True
            continue
        u = {campo: _val(row, campo) for campo in ("iccid", "telefone", "cdt", "id_hardware")}
        if any(u.values()):
            unidades.append(u)

    wb.close()

    if not unidades:
        raise ValueError("Nenhuma linha de dados encontrada na planilha.")

    nome = f"Pedido {numero}"
    template_id = templates_mod.criar_template(nome, cliente, criado_por, [], tipo="pedido")

    with db() as conn:
        for u in unidades:
            conn.execute(
                "INSERT INTO pedido_unidades "
                "(kit_template_id, iccid, telefone, cdt, id_hardware, criado_em) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (template_id, u["iccid"], u["telefone"], u["cdt"], u["id_hardware"], now_brt())
            )

    return template_id, {"unidades": len(unidades), "numero": numero}


def listar_unidades(template_id: int) -> list:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM pedido_unidades WHERE kit_template_id = ? ORDER BY id",
            (template_id,)
        ).fetchall()
    return [dict(r) for r in rows]
