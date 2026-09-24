"""Entrypoint do servidor (start.bat, servico do Windows e `python main.py`).

Uma porta só: HTTP na 8080 (ou a definida em PORTA no .env). Notebooks da
rede interna e o acesso pela nuvem (Cloudflare Tunnel, que entrega o HTTPS)
usam a mesma porta.

Legado: aparelhos que ainda abrem https://IP:8011 (atalho salvo, etiqueta
antiga com QR apontando pra la) ganhavam tela branca. Se existir
certs/cert.pem + certs/key.pem, a 8011 continua no ar SO pra redirecionar
para o endereco novo (http://IP:8080/mesmo-caminho) -- assim ninguem fica sem
acesso enquanto os atalhos sao trocados. REDIRECIONAR_8011=0 desliga isso.
"""
import asyncio
import os

import uvicorn
from dotenv import load_dotenv

# O .env precisa estar carregado ANTES de ler PORTA/REDIRECIONAR_8011 (o
# main.py so o carrega depois de importado).
load_dotenv()

PORTA = int(os.getenv("PORTA", "8080") or 8080)
PORTA_LEGADA = 8011


def _tem_certificado() -> bool:
    return os.path.exists("certs/cert.pem") and os.path.exists("certs/key.pem")


def _redirecionar_legado() -> bool:
    quer = os.getenv("REDIRECIONAR_8011", "1").strip().lower() not in ("0", "false", "nao", "não")
    return quer and _tem_certificado() and PORTA != PORTA_LEGADA


async def _app_redirecionador(scope, receive, send):
    """ASGI minimo: qualquer pedido na porta legada vai pro endereco atual."""
    if scope["type"] != "http":
        return
    cabecalhos = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
    base = (os.getenv("SERVIDOR_URL") or "").strip().rstrip("/")
    if not base:
        host = cabecalhos.get("host", "localhost").rsplit(":", 1)[0]
        base = f"http://{host}:{PORTA}"
    destino = base + scope["path"]
    if scope.get("query_string"):
        destino += "?" + scope["query_string"].decode()
    await send({"type": "http.response.start", "status": 307,
                "headers": [(b"location", destino.encode()), (b"content-length", b"0")]})
    await send({"type": "http.response.body", "body": b""})


async def _servir() -> None:
    principal = uvicorn.Server(uvicorn.Config(
        "main:app", host="0.0.0.0", port=PORTA, reload=False, log_level="info"))
    servidores = [principal.serve()]
    if _redirecionar_legado():
        legado = uvicorn.Server(uvicorn.Config(
            _app_redirecionador, host="0.0.0.0", port=PORTA_LEGADA, reload=False,
            log_level="warning", ssl_certfile="certs/cert.pem", ssl_keyfile="certs/key.pem"))
        servidores.append(legado.serve())
    await asyncio.gather(*servidores)


def main() -> None:
    asyncio.run(_servir())


if __name__ == "__main__":
    main()
