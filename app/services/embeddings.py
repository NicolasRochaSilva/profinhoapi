"""Embeddings locais com all-MiniLM-L6-v2 (384 dimensões) via FastEmbed."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

logger = logging.getLogger("profinho.embeddings")

_model = None
_model_lock = asyncio.Lock()


def _carregar_modelo():
    from fastembed import TextEmbedding

    return TextEmbedding(model_name="sentence-transformers/all-MiniLM-L6-v2")


def _embed_sync(texto: str) -> list[float]:
    global _model
    if _model is None:
        logger.info("Carregando modelo de embeddings all-MiniLM-L6-v2...")
        _model = _carregar_modelo()
    vetor = next(_model.embed([texto]))
    return vetor.tolist() if hasattr(vetor, "tolist") else list(vetor)


async def embed_texto(texto: str) -> list[float]:
    """Gera embedding 384-d do texto (não bloqueia o event loop)."""
    return await asyncio.to_thread(_embed_sync, texto.strip()[:4000])
