"""
Gera certificado SSL auto-assinado para o servidor rodar em HTTPS na LAN.
Execute uma vez: python gerar_cert.py
O certificado fica em certs/cert.pem e certs/key.pem (válido por 10 anos).

Para iOS confiar no certificado:
  1. Abra http://<IP>:8011/cert no Safari do iPhone (com HTTP ainda)
  2. Baixe o perfil → Ajustes > Perfil Baixado > Instalar
  3. Ajustes > Geral > Sobre > Configurar Confiança de Certificado → ative o cert
  Pronto — todos os kits abrem em HTTPS sem aviso.
"""
import os
import ipaddress
from pathlib import Path
from datetime import datetime, timedelta, timezone

import socket

def detectar_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(("192.168.1.1", 80))
    ip = s.getsockname()[0]
    s.close()
    return ip


def gerar(ip: str):
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    Path("certs").mkdir(exist_ok=True)
    cert_path = Path("certs/cert.pem")
    key_path  = Path("certs/key.pem")

    # Gera chave privada 2048-bit
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, f"KitConference-{ip}"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Kit Conference LAN"),
    ])

    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.IPAddress(ipaddress.IPv4Address(ip)),
                x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
                x509.DNSName("localhost"),
            ]),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )

    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    print(f"Certificado gerado para IP: {ip}")
    print(f"  cert: {cert_path.resolve()}")
    print(f"  key:  {key_path.resolve()}")
    print()
    print("Próximos passos:")
    print(f"  1. Inicie o servidor: python main.py")
    print(f"  2. No iPhone: abra http://{ip}:8011/cert no Safari")
    print(f"  3. Instale o perfil > Ajustes > Geral > Sobre > Conf. Confianca de Cert > ative")


if __name__ == "__main__":
    ip = detectar_ip()
    gerar(ip)
