"""自签名证书生成与内网域名判断。

Lucky 的 SSL 模块支持上传证书；内网域名无法用公网 ACME 签发，
这里用 cryptography 现场生成一张自签名证书再交给 Lucky 托管。
"""

import datetime
import ipaddress

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


SELF_SIGNED_VALID_DAYS = 825

# 常见内网后缀；单标签域名和 IP 也按内网处理
INTERNAL_SUFFIXES = (
    ".lan",
    ".local",
    ".localdomain",
    ".internal",
    ".home",
    ".home.arpa",
    ".localhost",
    ".test",
    ".invalid",
)


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value.strip().strip("[]"))
    except ValueError:
        return False
    return True


def is_internal_domain(domain: str) -> bool:
    """判断域名是否只能内网访问（无法用公网 ACME 签发）。"""
    value = (domain or "").strip().lower().strip("[]")
    if not value:
        return True
    if value.startswith("*."):
        value = value[2:]
    if _is_ip(value):
        return True
    if "." not in value:
        return True
    return value.endswith(INTERNAL_SUFFIXES)


def _san_entry(domain: str) -> x509.GeneralName:
    value = domain.strip()
    try:
        return x509.IPAddress(ipaddress.ip_address(value.strip("[]")))
    except ValueError:
        return x509.DNSName(value)


def build_self_signed(domains: list[str]) -> tuple[str, str]:
    """为域名列表生成自签名证书，返回 (cert_pem, key_pem)。"""
    values = list(dict.fromkeys(item.strip() for item in domains if item.strip()))
    if not values:
        raise ValueError("至少需要一个域名")

    key = ec.generate_private_key(ec.SECP256R1())
    common_name = values[0][:64]
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=SELF_SIGNED_VALID_DAYS))
        .add_extension(
            x509.SubjectAlternativeName([_san_entry(item) for item in values]),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_encipherment=False,
                key_agreement=False,
                content_commitment=False,
                data_encipherment=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_pem = certificate.public_bytes(serialization.Encoding.PEM).decode()
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()
    return cert_pem, key_pem
