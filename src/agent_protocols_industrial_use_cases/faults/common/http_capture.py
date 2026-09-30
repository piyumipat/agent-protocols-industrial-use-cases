"""Exact HTTP request evidence for replay faults."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import httpx


@dataclass(frozen=True, slots=True)
class CapturedRequest:
    method: str
    url: str
    headers: tuple[tuple[bytes, bytes], ...]
    body: bytes

    @classmethod
    async def from_httpx(cls, request: httpx.Request) -> CapturedRequest:
        return cls(
            request.method,
            str(request.url),
            tuple(request.headers.raw),
            await request.aread(),
        )

    def to_httpx(self) -> httpx.Request:
        return httpx.Request(
            self.method,
            self.url,
            headers=list(self.headers),
            content=self.body,
        )

    def sha256(self) -> str:
        digest = hashlib.sha256()
        digest.update(self.method.encode("ascii") + b"\0" + self.url.encode() + b"\0")
        for name, value in self.headers:
            digest.update(name + b":" + value + b"\n")
        digest.update(b"\0" + self.body)
        return digest.hexdigest()
