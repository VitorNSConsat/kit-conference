from datetime import datetime


def generate_zpl(kit_id: str, kit_nome: str, cliente: str,
                 operador: str, timestamp: datetime,
                 itens: list[dict]) -> str:
    """
    Gera ZPL para etiqueta 4x6" (101.6x152.4mm) a 203 DPI.
    Parâmetro itens: [{'descricao': str, 'quantidade': int}]
    Ajuste ^PW (largura) e ^LL (comprimento) conforme sua impressora.
    """
    data_str = timestamp.strftime("%d/%m/%Y %H:%M")
    kit_id_curto = kit_id[:16]  # exibe só os primeiros 16 chars na etiqueta

    # Linhas de itens (max 10 para caber na etiqueta)
    linhas_itens = ""
    y = 620
    for item in itens[:10]:
        linha = f"  {item['quantidade']}x {item['descricao']}"[:48]
        linhas_itens += f"^FO40,{y}^A0N,22,22^FD{linha}^FS\n"
        y += 28

    zpl = f"""^XA
^PW812
^LL1218
^CI28

^FO40,30^A0N,32,32^FD{kit_nome}^FS
^FO40,70^A0N,24,24^FDCliente: {cliente}^FS
^FO40,100^A0N,22,22^FDData: {data_str}^FS
^FO40,128^A0N,22,22^FDOperador: {operador}^FS

^FO40,170^BQN,2,5^FDQA,{kit_id}^FS

^FO320,170^A0N,22,22^FDKit ID:^FS
^FO320,196^A0N,18,18^FD{kit_id_curto}^FS

^FO40,580^A0N,24,24^FDComposição:^FS
{linhas_itens}
^FO40,1170^A0N,20,20^FD----------------------------------------^FS

^XZ"""
    return zpl
