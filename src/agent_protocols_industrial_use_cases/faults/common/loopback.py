"""Run one real HTTP endpoint for an isolated fault attempt."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import uvicorn
from starlette.applications import Starlette

from .anp_loopback import LocalTls


@asynccontextmanager
async def loopback_server(app_factory: Callable[[str], Starlette]) -> AsyncIterator[str]:
    """Start an ASGI app after reserving its port; close it on every exit path."""

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    base_url = f"http://127.0.0.1:{listener.getsockname()[1]}"
    try:
        app = app_factory(base_url)
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on"))
        task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(5):
                while not server.started:
                    if task.done():
                        await task
                        raise RuntimeError("fault endpoint stopped during startup")
                    await asyncio.sleep(0.01)
            yield base_url
        finally:
            server.should_exit = True
            async with asyncio.timeout(5):
                await task
    finally:
        listener.close()


@asynccontextmanager
async def https_loopback_server(
    app_factory: Callable[[str], Starlette], tls: LocalTls
) -> AsyncIterator[str]:
    """Run a Starlette endpoint with a locally trusted HTTPS certificate."""

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    base_url = f"https://localhost:{listener.getsockname()[1]}"
    try:
        app = app_factory(base_url)
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                log_level="error",
                lifespan="on",
                ssl_certfile=str(tls.certificate_path),
                ssl_keyfile=str(tls.key_path),
            )
        )
        task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(5):
                while not server.started:
                    if task.done():
                        await task
                        raise RuntimeError("fault endpoint stopped during startup")
                    await asyncio.sleep(0.01)
            yield base_url
        finally:
            server.should_exit = True
            async with asyncio.timeout(5):
                await task
    finally:
        listener.close()
