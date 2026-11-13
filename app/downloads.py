"""Andamento dos downloads: o que o servidor está fazendo enquanto monta um
arquivo grande.

A tela abre a janela de progresso, pede o arquivo com um cabeçalho
X-Download-Id e, enquanto a resposta não chega, consulta
/downloads/progresso/<id>. A rota que monta o arquivo anota cada etapa aqui
("Lendo os kits", "Montando a aba Detalhes: 40.000 de 100.000"...).

Fica em memória, de propósito: é informação de segundos, o servidor roda
num processo só e, se reiniciar no meio, o download também cai. Rota que
não anota nada continua funcionando, e a janela mostra só o tempo correndo.
"""
import threading
import time

_TRAVA = threading.Lock()
_ANDAMENTO: dict[str, dict] = {}
_VALIDADE = 15 * 60          # some da memória 15 min depois da última notícia
_MAX_LOG = 40


class Progresso:
    """O que a rota usa pra contar o que está fazendo. Sem id (download
    pedido por link direto, sem a janela), cada chamada não faz nada."""

    def __init__(self, ident: str = ""):
        self.ident = (ident or "")[:64]
        if self.ident:
            with _TRAVA:
                _limpar_velhos()
                _ANDAMENTO[self.ident] = {"etapa": "Começando", "feito": None, "total": None,
                                          "log": [], "atualizado": time.time()}

    def etapa(self, texto: str, feito: int | None = None, total: int | None = None,
              registrar: bool = True):
        """Atualiza a etapa atual. `registrar=False` só mexe na contagem
        (usado dentro de laços, pra não encher o log com uma linha por item)."""
        if not self.ident:
            return
        with _TRAVA:
            e = _ANDAMENTO.get(self.ident)
            if e is None:
                return
            e.update(etapa=texto, feito=feito, total=total, atualizado=time.time())
            if registrar:
                e["log"].append(texto)
                del e["log"][:-_MAX_LOG]


def de_request(request) -> Progresso:
    return Progresso(request.headers.get("x-download-id", ""))


def ler(ident: str) -> dict | None:
    with _TRAVA:
        e = _ANDAMENTO.get(ident)
        return None if e is None else {k: (list(v) if k == "log" else v)
                                       for k, v in e.items() if k != "atualizado"}


def _limpar_velhos():
    limite = time.time() - _VALIDADE
    for k in [k for k, v in _ANDAMENTO.items() if v["atualizado"] < limite]:
        del _ANDAMENTO[k]
