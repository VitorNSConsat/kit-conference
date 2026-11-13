"""Planilhas grandes, rápidas, no mesmo visual dos outros relatórios
(cabeçalho azul, linhas zebradas, topo congelado).

Por que não o openpyxl aqui: ele guarda cada célula como objeto e confere o
estilo de cada uma contra todos os estilos já usados. Com "Todos os kits
finalizados" (~100 mil itens bipados) isso levava 3 minutos — e, como a
rota rodava na linha principal do servidor, o sistema inteiro parava junto.
O xlsxwriter escreve linha a linha e faz o mesmo arquivo em segundos.
"""
import io

import xlsxwriter

AZUL = "#1A3A5C"
CINZA = "#F4F7FB"
# A cada quantas linhas a aba avisa o andamento (ver app/downloads.py).
_PASSO_AVISO = 2000


class Planilha:
    def __init__(self):
        self._buf = io.BytesIO()
        self.wb = xlsxwriter.Workbook(self._buf, {
            "in_memory": True,
            # Texto é texto: "=SOMA", "0012" e "http://..." vindos do banco não
            # viram fórmula, número ou link no Excel.
            "strings_to_formulas": False,
            "strings_to_numbers": False,
            "strings_to_urls": False,
        })
        self._cab = self.wb.add_format({"bold": True, "font_color": "#FFFFFF", "bg_color": AZUL,
                                        "align": "center", "valign": "vcenter"})
        self._zebra = self.wb.add_format({"bg_color": CINZA, "valign": "top"})
        self._normal = self.wb.add_format({"valign": "top"})
        self._zebra_quebra = self.wb.add_format({"bg_color": CINZA, "valign": "top", "text_wrap": True})
        self._normal_quebra = self.wb.add_format({"valign": "top", "text_wrap": True})

    def aba(self, titulo: str, cabecalhos: list, larguras: list, linhas,
            progresso=None, total: int | None = None, quebrar: set | None = None):
        """Escreve uma aba. `linhas` pode ser lista ou gerador; `quebrar` são
        os índices de coluna com texto longo (quebra a linha dentro da célula)."""
        ws = self.wb.add_worksheet(titulo[:31])
        for c, (h, w) in enumerate(zip(cabecalhos, larguras)):
            ws.write_string(0, c, h, self._cab)
            ws.set_column(c, c, w)
        ws.freeze_panes(1, 0)
        quebrar = quebrar or set()
        n = 0
        for n, linha in enumerate(linhas, 1):
            zebra = n % 2 == 0
            fmt = self._zebra if zebra else self._normal
            for c, valor in enumerate(linha):
                f = (self._zebra_quebra if zebra else self._normal_quebra) if c in quebrar else fmt
                if valor is None or valor == "":
                    ws.write_blank(n, c, None, f)
                elif isinstance(valor, (int, float)) and not isinstance(valor, bool):
                    ws.write_number(n, c, valor, f)
                else:
                    ws.write_string(n, c, str(valor), f)
            if progresso and n % _PASSO_AVISO == 0:
                progresso.etapa(f"Montando a aba {titulo}", n, total, registrar=False)
        if progresso:
            progresso.etapa(f"Aba {titulo}: {n:,} linha(s)".replace(",", "."), n, total or n)
        return ws

    def conteudo(self) -> bytes:
        self.wb.close()
        return self._buf.getvalue()
