"""Local HTTPS and DID credentials for ANP fault attempts."""

from __future__ import annotations

import socket
import ssl
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from aiohttp import web
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


@dataclass(frozen=True, slots=True)
class LocalTls:
    server: ssl.SSLContext
    client: ssl.SSLContext
    certificate_path: Path
    key_path: Path


def create_local_tls(directory: Path) -> LocalTls:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
        .sign(key, hashes.SHA256())
    )
    cert_path = directory / "localhost.crt"
    key_path = directory / "localhost.key"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    server = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    server.load_cert_chain(cert_path, key_path)
    client = ssl.create_default_context(cafile=str(cert_path))
    return LocalTls(server, client, cert_path, key_path)


def reserved_listener() -> socket.socket:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    return listener


@asynccontextmanager
async def anp_server(
    listener: socket.socket,
    app_factory: Callable[[str], web.Application],
    tls: LocalTls,
) -> AsyncIterator[str]:
    origin = f"https://localhost:{listener.getsockname()[1]}"
    runner = web.AppRunner(app_factory(origin))
    try:
        await runner.setup()
        site = web.SockSite(runner, listener, ssl_context=tls.server)
        await site.start()
        yield origin
    finally:
        await runner.cleanup()
        listener.close()


def create_jwt_keys() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private.decode(), public.decode()
