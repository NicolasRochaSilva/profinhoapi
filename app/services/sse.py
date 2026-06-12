"""Helpers Server-Sent Events (SSE) para streaming de chat."""

from __future__ import annotations

import json
from typing import Any, AsyncIterator


def sse_event(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def iter_texto_simulado(texto: str, tamanho: int = 24) -> AsyncIterator[str]:
    """Emite texto pré-pronto em pedaços (cache, moderação, etc.)."""
    if not texto:
        return
    i = 0
    while i < len(texto):
        yield texto[i : i + tamanho]
        i += tamanho
