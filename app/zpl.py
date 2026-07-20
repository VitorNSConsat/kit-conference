import io
import os
import base64
import unicodedata
from datetime import datetime

EMPRESA_NOME = os.getenv("EMPRESA_NOME", "Sua Empresa")
SERVIDOR_URL = os.getenv("SERVIDOR_URL", "http://localhost:8011")


def _ascii(s: str) -> str:
    """Remove acentos para compatibilidade com Zebra sem ^CI28."""
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def _kit_url(kit_id: str) -> str:
    return f"{SERVIDOR_URL}/kit/{kit_id}"


def _qr_img(url: str, size_mm: int = 70) -> str:
    """Gera QR code como PNG base64 — renderização confiável tanto em tela quanto na impressão."""
    try:
        import segno
        qr = segno.make(url, error="l")
        buf = io.BytesIO()
        qr.save(buf, kind="png", scale=10, border=4)
        b64 = base64.b64encode(buf.getvalue()).decode()
        return (
            f'<img src="data:image/png;base64,{b64}" '
            f'style="display:block;width:{size_mm}mm;height:{size_mm}mm;" alt="QR">'
        )
    except Exception:
        return f'<p style="font-size:9px;color:#aaa;word-break:break-all;">{url}</p>'


def _qr_svg(url: str) -> str:
    """SVG — usado apenas para exibição em tela (página /rede). Para impressão use _qr_img."""
    try:
        import segno, re
        qr = segno.make(url, error="l")
        buf = io.BytesIO()
        qr.save(buf, kind="svg", scale=5, border=2, xmldecl=False, nl=False)
        svg = buf.getvalue().decode("utf-8")
        svg = re.sub(r'\s(width|height)="[^"]*"', '', svg, count=2)
        svg = svg.replace("<svg ", '<svg style="display:block;max-width:100%;height:auto;" ', 1)
        return svg
    except Exception:
        return f'<p style="font-size:9px;color:#aaa;word-break:break-all;">{url}</p>'


def _logo_base64() -> str | None:
    logo = os.path.join(os.path.dirname(__file__), "..", "static", "logo.png")
    if os.path.exists(logo):
        with open(logo, "rb") as f:
            return base64.b64encode(f.read()).decode()
    return None


# ── ZPL para Zebra ZD220 ──────────────────────────────────────────────────────
# 100x150mm a 203 DPI = 800x1200 dots. QR aponta para URL LAN do kit.

def generate_zpl(kit_id: str, kit_nome: str, cliente: str,
                 operador: str, timestamp: datetime,
                 itens: list[dict],
                 veiculo: str = "", garagem: str = "",
                 servidor_url: str = "") -> str:

    data_str = timestamp.strftime("%d/%m/%Y")
    hora_str = timestamp.strftime("%H:%M")
    kit_id_curto = kit_id[:8].upper()
    url_qr = _kit_url(kit_id)

    empresa  = _ascii(EMPRESA_NOME)[:38]
    veiculo_ = _ascii(veiculo)[:40]
    garagem_ = _ascii(garagem)[:40]

    linhas_vg = ""
    y_after_vg = 200
    if veiculo_ or garagem_:
        y = y_after_vg + 10
        if veiculo_:
            linhas_vg += f"^FO0,{y}^FB800,1,0,C^A0N,28,28^FDVeiculo: {veiculo_}^FS\n"
            y += 38
        if garagem_:
            linhas_vg += f"^FO0,{y}^FB800,1,0,C^A0N,28,28^FDGaragem: {garagem_}^FS\n"
            y += 38
        linhas_vg += f"^FO25,{y}^GB750,3,3^FS\n"
        y_after_vg = y + 8

    y_qr   = y_after_vg + 12
    x_qr   = 312
    y_hint = y_qr + 185
    y_id   = y_hint + 28

    return f"""^XA
^PW800
^LL1200
^LH0,0

^FO0,30^FB800,1,0,C^A0N,32,32^FD{data_str}  {hora_str}^FS

^FO25,75^GB750,4,4^FS

^FO0,88^FB800,1,0,C^A0N,52,52^FD{empresa}^FS
^FO0,150^FB800,1,0,C^A0N,22,22^FDConferencia de Kits^FS

^FO25,185^GB750,3,3^FS

{linhas_vg}
^FO{x_qr},{y_qr}^BQN,2,4^FDMA,{url_qr}^FS

^FO0,{y_hint}^FB800,1,0,C^A0N,18,18^FDEscaneie para ver os itens do kit^FS
^FO0,{y_id}^FB800,1,0,C^A0N,16,16^FDID: {kit_id_curto}^FS

^FO25,1175^GB750,3,3^FS

^XZ"""


# ── HTML para impressora normal ───────────────────────────────────────────────

def generate_estoque_html_label(tipo_nome: str, codigo_barra: str, url_qr: str) -> str:
    """Etiqueta HTML 60×60mm: descrição em cima, QR no meio, ESTOQUE|LOGO embaixo."""
    logo_b64 = _logo_base64()

    try:
        import segno
        qr = segno.make(url_qr, error="l")
        buf = io.BytesIO()
        qr.save(buf, kind="png", scale=12, border=3)
        qr_src = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        qr_src = ""

    logo_html = (
        f'<img class="logo-img" src="data:image/png;base64,{logo_b64}" alt="logo">'
        if logo_b64
        else f'<span class="empresa-txt">{EMPRESA_NOME}</span>'
    )

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<title>Etiqueta — {tipo_nome}</title>
<style>
  @page {{ size: 60mm 60mm; margin: 0; }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}

  body {{
    font-family: Arial, Helvetica, sans-serif;
    background: #e8e8e8;
    display: flex; flex-direction: column; align-items: center;
    padding: 20px;
  }}

  .label {{
    background: #fff;
    width: 60mm; height: 60mm;
    overflow: hidden;
    display: flex; flex-direction: column;
    align-items: center;
    padding: 2.5mm 2mm 1.5mm 2mm;
    gap: 1.5mm;
    box-shadow: 0 4px 18px rgba(0,0,0,.2);
    border: 1px solid #ccc;
  }}

  .descricao {{
    font-size: 11px; font-weight: 900; color: #000;
    text-align: center; line-height: 1.3;
    word-break: break-word; width: 100%;
    text-transform: uppercase;
    letter-spacing: .3px;
    flex-shrink: 0;
  }}

  .qr-wrap {{
    flex: 1; min-height: 0;
    display: flex; align-items: center; justify-content: center;
    width: 100%;
  }}

  .qr-wrap img.qr {{
    display: block;
    max-width: 100%; max-height: 100%;
    image-rendering: pixelated;
  }}

  .rodape {{
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 5px;
    flex-shrink: 0;
    width: 100%;
    padding-top: 1.5mm;
    border-top: 0.4mm solid #000;
  }}

  .rodape-txt {{
    font-size: 9px; font-weight: 900; color: #000;
    letter-spacing: 2px; text-transform: uppercase;
  }}

  .sep {{ font-size: 10px; color: #555; }}

  .logo-img {{
    max-height: 5.5mm; max-width: 16mm;
    object-fit: contain;
  }}

  .empresa-txt {{
    font-size: 9px; font-weight: 900; color: #000;
    text-transform: uppercase; letter-spacing: 1px;
  }}

  @media print {{
    body {{ background: white; padding: 0; margin: 0; }}
    .label {{ box-shadow: none; border: none; }}
    .actions {{ display: none !important; }}
  }}
</style>
</head>
<body>
<div class="label">
  <div class="descricao">{tipo_nome}</div>
  <div class="qr-wrap">
    <img class="qr" src="{qr_src}" alt="QR">
  </div>
  <div class="rodape">
    <span class="rodape-txt">Estoque</span>
    <span class="sep">|</span>
    {logo_html}
  </div>
</div>
<div class="actions" style="display:flex;margin-top:14px;width:60mm;">
  <button style="flex:1;padding:10px;background:#1a3a5c;color:#fff;border:none;
                 border-radius:6px;cursor:pointer;font-size:14px;font-weight:bold;"
          onclick="window.print();setTimeout(()=>window.close(),800);">
    🖨️ Imprimir e Fechar
  </button>
</div>
<script>window.onload = () => setTimeout(() => window.print(), 500);</script>
</body>
</html>"""


def generate_html_label(kit_id: str, kit_nome: str, cliente: str,
                        operador: str, timestamp: datetime,
                        itens: list[dict],
                        veiculo: str = "", garagem: str = "",
                        servidor_url: str = "") -> str:

    data_str  = timestamp.strftime("%d/%m/%Y")
    hora_str  = timestamp.strftime("%H:%M")
    kit_id_curto = kit_id[:8].upper()
    url_qr = _kit_url(kit_id)
    qr_svg = _qr_img(url_qr, size_mm=70)

    logo_b64 = _logo_base64()
    empresa_html = (
        f'<img src="data:image/png;base64,{logo_b64}" alt="{EMPRESA_NOME}" class="logo-img">'
        if logo_b64
        else f'<div class="empresa-nome">{EMPRESA_NOME}</div>'
    )

    vg_html = ""
    if veiculo or garagem:
        vg_html = '<div class="vg-block">'
        if veiculo:
            vg_html += f'<div class="vg-linha"><span class="vg-label">Veículo</span><span class="vg-val">{veiculo}</span></div>'
        if garagem:
            vg_html += f'<div class="vg-linha"><span class="vg-label">Garagem</span><span class="vg-val">{garagem}</span></div>'
        vg_html += '</div>'

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Etiqueta</title>
<style>
  @page {{ size: 100mm 150mm; margin: 0; }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}

  body {{
    font-family: Arial, Helvetica, sans-serif;
    background: #e8e8e8;
    display: flex; flex-direction: column; align-items: center;
    padding: 20px;
  }}

  /* Altura FIXA — nunca cresce além de 150mm */
  .label {{
    background: #fff;
    width: 100mm; height: 150mm;
    overflow: hidden;
    display: flex; flex-direction: column;
    box-shadow: 0 4px 18px rgba(0,0,0,.2);
    border: 1px solid #ccc;
  }}

  .topo {{
    background: #000; color: #fff; text-align: center;
    padding: 5px 8px; font-size: 11px; font-weight: bold;
    letter-spacing: 1.5px; flex-shrink: 0;
  }}
  .empresa {{
    text-align: center; padding: 6px 8px 4px;
    border-bottom: 1.5px solid #ccc; flex-shrink: 0;
  }}
  .logo-img {{ max-height: 38px; max-width: 160px; object-fit: contain; filter: grayscale(100%); }}
  .empresa-nome {{
    font-size: 14px; font-weight: 900; color: #000;
    letter-spacing: 1px; text-transform: uppercase;
  }}
  .subtitulo {{
    font-size: 7px; color: #666; margin-top: 2px;
    letter-spacing: 2px; text-transform: uppercase;
  }}

  .vg-block {{
    padding: 3px 8px; border-bottom: 2px solid #000;
    background: #f0f0f0; flex-shrink: 0;
  }}
  .vg-linha {{
    display: flex; justify-content: space-between; align-items: center; padding: 2px 0;
  }}
  .vg-label {{
    font-weight: 700; color: #000; text-transform: uppercase;
    font-size: 8px; letter-spacing: 1px;
  }}
  .vg-val {{ font-size: 12px; font-weight: 700; color: #000; }}

  /* QR — ocupa o espaço restante, PNG com tamanho fixo em mm */
  .qr-section {{
    flex: 1; min-height: 0;
    display: flex; flex-direction: column;
    align-items: center; justify-content: center;
    padding: 4px 6px;
  }}
  .qr-wrap {{
    display: flex; align-items: center; justify-content: center;
    flex-shrink: 0;
  }}
  .qr-wrap img {{
    display: block;
    width: 70mm; height: 70mm;
    image-rendering: pixelated;
  }}
  .qr-hint {{
    font-size: 7px; color: #999; margin-top: 3px;
    text-align: center; flex-shrink: 0;
  }}

  .check-box {{
    display: flex; align-items: center; justify-content: center; gap: 10px;
    padding: 4px 8px; border-top: 1px solid #ccc; flex-shrink: 0;
  }}
  .check-item {{
    display: flex; align-items: center; gap: 5px;
  }}
  .check-square {{
    width: 12mm; height: 5mm;
    border: 1.5px solid #000; border-radius: 1px; flex-shrink: 0;
  }}
  .check-label {{
    font-size: 8px; font-weight: 700; color: #000;
    text-transform: uppercase; letter-spacing: 1px;
  }}
  .check-sep {{
    width: 1px; height: 8mm; background: #ccc; flex-shrink: 0;
  }}
  .rodape {{
    text-align: center; padding: 2px 8px; font-size: 7px;
    color: #888; letter-spacing: 1px;
    border-top: 1px solid #e0e0e0; flex-shrink: 0;
  }}

  .actions {{ display: flex; gap: 10px; margin-top: 14px; width: 100mm; }}
  .btn {{
    flex: 1; padding: 9px; border: none; border-radius: 6px;
    cursor: pointer; font-size: 13px; font-weight: bold;
  }}
  .btn-print {{ background: #000; color: white; }}
  .btn-close  {{ background: #888; color: white; }}

  @media print {{
    body {{ background: white; padding: 0; margin: 0; }}
    .label {{
      width: 100mm; height: 150mm;
      box-shadow: none; border: none; border-radius: 0;
    }}
    .actions {{ display: none; }}
  }}
</style>
</head>
<body>
<div class="label">
  <div class="topo" id="label-time">{data_str} &nbsp;&nbsp; {hora_str}</div>
  <div class="empresa">
    {empresa_html}
    <div class="subtitulo">Conferência de Kits</div>
  </div>
  {vg_html}
  <div class="qr-section">
    <div class="qr-wrap">{qr_svg}</div>
    <div class="qr-hint">Escaneie na rede Wi-Fi para ver os itens do kit</div>
  </div>
  <div class="check-box">
    <div class="check-item">
      <div class="check-square"></div>
      <span class="check-label">Verificado</span>
    </div>
    <div class="check-sep"></div>
    <div class="check-item">
      <div class="check-square"></div>
      <span class="check-label">Verificado</span>
    </div>
  </div>
  <div class="rodape">ID: {kit_id_curto}</div>
</div>
<div class="actions">
  <button class="btn btn-print" onclick="window.print()">🖨️ Imprimir</button>
  <button class="btn btn-close" onclick="window.close()">Fechar</button>
</div>
<script>window.onload = () => setTimeout(() => window.print(), 500);</script>
</body>
</html>"""
