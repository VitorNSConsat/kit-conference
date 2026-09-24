"""Entrypoint do servidor (start.bat, servico do Windows e `python main.py`).

Duas portas, o MESMO app:
  - 8080 (HTTP): notebooks da rede interna e o Cloudflare Tunnel.
  - 8011 (HTTPS, so se existir certs/cert.pem + certs/key.pem): celulares na rede
    interna. A CAMERA dos leitores (Consultar Kit/Item, Ler RMA, Ler Sobressalentes,
    bipagem) so funciona em HTTPS (ou localhost) -- por HTTP o navegador bloqueia.
    Fora da rede interna, o HTTPS vem do dominio do Cloudflare (SERVIDOR_URL).
HTTPS_LOCAL=0 desliga a 8011. PORTA muda a porta HTTP.
"""
import asyncio
import os

import uvicorn
from dotenv import load_dotenv

# O .env precisa estar carregado ANTES de ler PORTA/HTTPS_LOCAL (o
# main.py so o carrega depois de importado).
load_dotenv()

PORTA = int(os.getenv("PORTA", "8080") or 8080)
PORTA_HTTPS = 8011


def _tem_certificado() -> bool:
    return os.path.exists("certs/cert.pem") and os.path.exists("certs/key.pem")


def _https_local() -> bool:
    quer = os.getenv("HTTPS_LOCAL", "1").strip().lower() not in ("0", "false", "nao", "não")
    return quer and _tem_certificado() and PORTA != PORTA_HTTPS


async def _servir() -> None:
    servidores = [uvicorn.Server(uvicorn.Config(
        "main:app", host="0.0.0.0", port=PORTA, reload=False, log_level="info")).serve()]
    if _https_local():
        servidores.append(uvicorn.Server(uvicorn.Config(
            "main:app", host="0.0.0.0", port=PORTA_HTTPS, reload=False, log_level="info",
            ssl_certfile="certs/cert.pem", ssl_keyfile="certs/key.pem")).serve())
    await asyncio.gather(*servidores)


def main() -> None:
    asyncio.run(_servir())


if __name__ == "__main__":
    main()
