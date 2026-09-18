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

def test_generate_hardware_html_label_contem_campos():
    html = generate_hardware_html_label(
        rotulo="RMA-0001", produto="CDT07", serial="TSCE91001019/000963",
        status_texto="Feito", status_cor="#18804b",
        url_qr="http://localhost:8011/hardware/1",
    )
    assert "RMA-0001" in html
    assert "CDT07" in html
    assert "TSCE91001019/000963" in html
    assert "Feito" in html
    assert "#18804b" in html
    assert "@page" in html and "80mm 120mm" in html


def test_generate_hardware_html_label_campos_extras_opcionais():
    # cliente, defeito, quantidade e data de registro entram quando
    # informados, sem quebrar quem não passa nenhum deles. Responsável
    # NÃO vai pra etiqueta de propósito -- pode mudar sem reimpressão, e
    # já aparece na consulta pelo QR (ver test_hardware_qr).
    html = generate_hardware_html_label(
        rotulo="RMA-0001", produto="CDT07", serial="TSCE91001019/000963",
        status_texto="Feito", status_cor="#18804b",
        url_qr="http://localhost:8011/hardware/1",
        cliente="Galpão", categoria_defeito="GPS", quantidade=2,
        data_registro="2026-01-26 10:00:00",
    )
    assert "Galpão" in html
    assert "GPS" in html
    assert ">2<" in html
    assert "2026-01-26" in html


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


def test_generate_hardware_html_labels_lote_uma_por_pagina():
    etiquetas = [
        {"rotulo": "RMA-0001", "produto": "CDT07", "serial": "S1",
         "status_texto": "Feito", "status_cor": "#18804b", "url_qr": "http://x/hardware/1"},
        {"rotulo": "RMA-0002", "produto": "CDT08", "serial": "S2",
         "status_texto": "Em análise", "status_cor": "#246b84", "url_qr": "http://x/hardware/2"},
        {"rotulo": "RMA-0003", "produto": "CDT09", "serial": "S3",
         "status_texto": "Aguardando peça", "status_cor": "#b45309", "url_qr": "http://x/hardware/3"},
    ]
    html = generate_hardware_html_labels_lote(etiquetas)
    assert html.count('class="hw-pagina"') == 3
    assert "page-break-after: always" in html
    assert "80mm 120mm" in html
    for e in etiquetas:
        assert e["rotulo"] in html
        assert e["produto"] in html


def test_generate_hardware_html_labels_lote_vazio_nao_quebra():
    html = generate_hardware_html_labels_lote([])
    assert html.count('class="hw-pagina"') == 0
    assert "<html" in html
