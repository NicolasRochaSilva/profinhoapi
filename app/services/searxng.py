"""Integração com SearXNG (busca na internet)."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger("profinho.searxng")


async def buscar(query: str, max_resultados: int = 5) -> list[dict[str, Any]]:
    """Busca no SearXNG e retorna [{titulo, url, resumo}]."""
    params = {
        "q": query,
        "format": "json",
        "language": "pt-BR",
        "safesearch": 1,
    }
    url = f"{settings.searxng_base_url.rstrip('/')}/search"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, params=params, headers={"Accept": "application/json"})
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Falha na busca SearXNG: %s", exc)
        return []

    resultados: list[dict[str, Any]] = []
    for item in data.get("results", [])[:max_resultados]:
        resultados.append(
            {
                "titulo": item.get("title", ""),
                "url": item.get("url", ""),
                "resumo": item.get("content", ""),
            }
        )
    return resultados
