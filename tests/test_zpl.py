import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime
from app.zpl import generate_zpl, generate_hardware_html_label, generate_hardware_html_labels_lote


def test_generate_zpl_contém_kit_id():
    zpl = generate_zpl(
        kit_id="abc-123",
        kit_nome="Kit Teste",
        cliente="Empresa X",
        operador="Joao",
        timestamp=datetime(2026, 7, 10, 14, 30),
        itens=[{"descricao": "Antena", "quantidade": 2}]
    )
    # ID do kit (upper) presente no QR e no rodapé
    assert "ABC-123" in zpl
    # Data e hora presentes
    assert "10/07/2026" in zpl
    assert "14:30" in zpl
    # URL do kit presente no QR
    assert "/kit/" in zpl
    assert "^XA" in zpl
    assert "^XZ" in zpl


def test_generate_zpl_estrutura_zebra():
    zpl = generate_zpl("id-1", "Kit", "Cliente", "Op", datetime.now(), [])
    assert "^BQN" in zpl   # QR code presente
    assert "^PW800" in zpl  # largura 100mm a 203 DPI
    assert "^LL1200" in zpl  # altura 150mm a 203 DPI


def test_generate_zpl_veiculo_garagem():
    zpl = generate_zpl(
        kit_id="xyz-999",
        kit_nome="Kit A",
        cliente="C",
        operador="Op",
        timestamp=datetime(2026, 7, 10, 9, 0),
        itens=[],
        veiculo="ABC-1234",
        garagem="G03",
    )
    assert "ABC-1234" in zpl
    assert "G03" in zpl


# ── Etiqueta de hardware/RMA ─────────────────────────────────────────────────
# QR sempre aponta pra URL pública de consulta (/hardware/{id}) -- nunca pro
# dado cru -- é o que permite o status mudar no banco sem reimprimir a
# etiqueta (ver main.py:hardware_qr / requisito de segurança do pedido).

def test_generate_hardware_html_label_nao_mostra_responsavel():
    html = generate_hardware_html_label(
        rotulo="RMA-0001", produto="CDT07", serial="S1",
        status_texto="Feito", status_cor="#18804b", url_qr="http://x/hardware/1",
    )
    assert "Responsável" not in html


def test_generate_hardware_html_label_sem_campos_extras_nao_quebra():
    html = generate_hardware_html_label(
        rotulo="RMA-0002", produto="Roteador", serial="SN-1",
        status_texto="Em análise", status_cor="#246b84",
        url_qr="http://x/hardware/2",
    )
    assert "RMA-0002" in html
    assert "<html" in html


def test_generate_hardware_html_label_nao_expoe_dados_crus_fora_do_qr():
    # O QR em si é uma imagem base64 (não dá pra "ler" texto nele no HTML),
    # mas a URL usada pra gerar o QR não pode ser a única coisa que muda --
    # ela precisa ser só a URL, sem parâmetros com os dados do registro.
    url = "http://localhost:8011/hardware/42"
    html = generate_hardware_html_label(
        rotulo="RMA-0042", produto="Roteador", serial="SN-1",
        status_texto="Em análise", status_cor="#246b84", url_qr=url,
    )
    assert "produto=" not in html
    assert "status=" not in html
    assert "cliente=" not in html


def test_generate_hardware_html_label_escapa_html():
    html = generate_hardware_html_label(
        rotulo="RMA-0001", produto="<script>alert(1)</script>", serial="S1",
        status_texto="Feito", status_cor="#18804b", url_qr="http://x/hardware/1",
    )
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_etiqueta_rma_usa_o_mesmo_qr_e_formato_da_etiqueta_de_kit():
    import re
    from app.zpl import generate_html_label
    from datetime import datetime
    url = "http://localhost:8011/hardware/34"
    rma = generate_hardware_html_label(rotulo="RMA-0034", produto="CDD", url_qr=url)
    kit = generate_html_label("11111111-2222-3333-4444-555555555555", "Kit", "Cli", "Op", datetime(2026, 1, 1), [])
    assert "100mm 150mm" in rma and "100mm 150mm" in kit
    # o QR sai da mesma função: PNG, 70mm fixos, pixelado, e o mesmo código de barras
    for html in (rma, kit):
        assert "width:70mm;height:70mm" in html and "image-rendering: pixelated" in html
        assert 'class="barcode-img"' in html
    png = lambda h: re.search(r'data:image/png;base64,([A-Za-z0-9+/=]+)', h).group(1)
    import base64, io, segno
    esperado = io.BytesIO(); segno.make(url, error="q").save(esperado, kind="png", scale=10, border=4)
    assert base64.b64decode(png(rma)) == esperado.getvalue()
    # só o RMA e o item na descrição
    assert "RMA-0034 CDD" in rma and "Serial" not in rma and "Cliente" not in rma


def test_etiqueta_rma_lote_uma_por_folha():
    etiquetas = [{"rotulo": f"RMA-000{n}", "produto": f"P{n}", "url_qr": f"http://x/hardware/{n}"} for n in (1, 2, 3)]
    html = generate_hardware_html_labels_lote(etiquetas)
    assert html.count('class="folha"') == 3 and "page-break-after: always" in html
    assert all(e["rotulo"] in html for e in etiquetas)


def test_etiqueta_rma_lote_vazio_nao_quebra():
    assert "<html" in generate_hardware_html_labels_lote([])

