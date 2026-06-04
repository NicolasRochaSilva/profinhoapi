"""Integração com Crawl4AI (leitura/extração de páginas -> markdown limpo)."""

from __future__ import annotations

import logging
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger("profinho.crawl4ai")


def _headers() -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if settings.crawl4ai_token:
        h["Authorization"] = f"Bearer {settings.crawl4ai_token}"
    return h


async def ler_pagina(url: str, max_chars: int = 12000) -> Optional[str]:
    """Lê uma URL via Crawl4AI e retorna o conteúdo em markdown limpo."""
    base = settings.crawl4ai_base_url.rstrip("/")
    payload = {"urls": [url], "crawler_config": {"cache_mode": "bypass"}}
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(f"{base}/crawl", json=payload, headers=_headers())
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Falha ao ler página com Crawl4AI (%s): %s", url, exc)
        return None

    # Crawl4AI retorna estruturas levemente diferentes entre versões; tratamos as comuns.
    resultados = data.get("results") or data.get("result") or []
    if isinstance(resultados, dict):
        resultados = [resultados]
    if not resultados:
        return None

    primeiro = resultados[0]
    markdown = primeiro.get("markdown")
    if isinstance(markdown, dict):
        texto = markdown.get("fit_markdown") or markdown.get("raw_markdown") or ""
    else:
        texto = markdown or primeiro.get("cleaned_html") or primeiro.get("html") or ""

    return (texto or "")[:max_chars] if texto else None


async def ler_varias(urls: list[str], max_chars_total: int = 24000) -> dict[str, str]:
    """Lê várias URLs e retorna {url: conteudo}."""
    resultado: dict[str, str] = {}
    restante = max_chars_total
    for url in urls:
        if restante <= 0:
            break
        conteudo = await ler_pagina(url, max_chars=min(12000, restante))
        if conteudo:
            resultado[url] = conteudo
            restante -= len(conteudo)
    return resultado
