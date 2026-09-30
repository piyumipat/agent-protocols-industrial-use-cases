from __future__ import annotations

import json
import ssl
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiohttp
from agent_protocols.anp import (
    ANPClient,
    DIDWbaAuthHeader,
    create_e1_identity,
    create_http_authenticator,
    create_signature_verifier,
)
from aiohttp import web
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from agent_protocols_industrial_use_cases.domain.models import Award, Bid, TransportRequest
from agent_protocols_industrial_use_cases.domain.workflow import InMemoryEventSink, WorkflowEvents
from agent_protocols_industrial_use_cases.adapters.anp import ANPAdapter, create_executor_app
from tests.support import build_executor


def _write_tls_material(directory: Path) -> tuple[ssl.SSLContext, ssl.SSLContext]:
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
    server_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    server_context.load_cert_chain(cert_path, key_path)
    client_context = ssl.create_default_context(cafile=str(cert_path))
    return server_context, client_context


def _jwt_keys() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_key = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_key = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_key.decode(), public_key.decode()


async def test_anp_adapter_uses_real_discovery_did_auth_and_https(
    tmp_path: Path, unused_tcp_port: int
) -> None:
    port = unused_tcp_port
    origin = f"https://localhost:{port}"
    domain = f"localhost:{port}"
    sink = InMemoryEventSink()
    events = WorkflowEvents(
        run_id="run-anp-https",
        task_id="task-anp-https",
        protocol="anp",
        sink=sink,
    )
    executor, simulator = build_executor(
        "executor-https", events=events, eta_seconds=7, energy_cost=3
    )
    caller = create_e1_identity(
        "localhost",
        port=port,
        path_segments=["agents", "welding-cell"],
    )
    caller_document_path = tmp_path / "caller-did.json"
    caller_key_path = tmp_path / "caller-key.pem"
    caller_document_path.write_text(json.dumps(caller.document))
    caller_key_path.write_bytes(caller.private_key_pem)
    authenticator = create_http_authenticator(
        caller_document_path, caller_key_path
    )

    async def resolve_caller(did: str) -> dict[str, object]:
        if did != caller.did:
            raise ValueError("unknown caller DID")
        return caller.document

    jwt_private, jwt_public = _jwt_keys()
    verifier = create_signature_verifier(
        jwt_private_key=jwt_private,
        jwt_public_key=jwt_public,
        did_resolver=resolve_caller,
    )
    app = create_executor_app(
        executor,
        origin=origin,
        verifier=verifier,
        authorize_did=lambda did: did == caller.did,
    )
    server_ssl, client_ssl = _write_tls_material(tmp_path)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "localhost", port, ssl_context=server_ssl)
    await site.start()

    try:
        connector = aiohttp.TCPConnector(ssl=client_ssl)
        async with aiohttp.ClientSession(connector=connector) as session:

            def client_factory(
                executor_id: str, supplied_authenticator: DIDWbaAuthHeader
            ) -> ANPClient:
                assert executor_id == "executor-https"
                assert supplied_authenticator is authenticator
                return ANPClient(authenticator, session=session)

            request = TransportRequest(
                "task-anp-https",
                "part-42",
                1,
                "zone-a",
                "welding-cell",
                "urgent",
            )
            async with ANPAdapter(
                {"executor-https": domain},
                authenticators={"executor-https": authenticator},
                events=events,
                client_factory=client_factory,
            ) as adapter:
                bid = await adapter.request_bid("executor-https", request)
                delivery = await adapter.award(
                    "executor-https",
                    Award("task-anp-https", "executor-https"),
                )
    finally:
        await runner.cleanup()

    assert bid == Bid("task-anp-https", "executor-https", 7, 3)
    assert delivery.executor_id == "executor-https"
    assert simulator.count == 1
    event_types = [event.event_type for event in sink.events]
    assert "anp.discovery.completed" in event_types
    assert "anp.did.verified" in event_types
    assert event_types.count("anp.caller.authenticated") == 2
    assert event_types.count("anp.caller.authorized") == 2
