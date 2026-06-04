"""Cache semântico de prompts/respostas no PostgreSQL."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Optional

import numpy as np

from app.config import settings
from app.database import get_pool
from app.ollama_client import ollama
from app.services import embeddings

logger = logging.getLogger("profinho.cache")

_SAUDACOES = frozenset(
    {
        "olá",
        "ola",
        "oi",
        "oie",
        "bom dia",
        "boa tarde",
        "boa noite",
        "tudo bem",
        "e aí",
        "e ai",
        "hey",
        "hello",
        "hi",
    }
)


@dataclass
class CacheHit:
    resposta: str
    categoria: str
    modelo: str
    fontes: list[str]
    similaridade: float
    motivo: str
    cache_id: str


def normalizar_prompt(texto: str) -> str:
    t = texto.strip().lower()
    t = re.sub(r"\s+", " ", t)
    return t


def eh_saudacao_simples(texto: str) -> bool:
    norm = normalizar_prompt(texto)
    if len(norm) > 40:
        return False
    if norm in _SAUDACOES:
        return True
    palavras = norm.split()
    return len(palavras) <= 3 and any(p in _SAUDACOES for p in palavras)


def _cosine(a: list[float], b: list[float]) -> float:
    va, vb = np.array(a, dtype=np.float32), np.array(b, dtype=np.float32)
    denom = float(np.linalg.norm(va) * np.linalg.norm(vb)) + 1e-9
    return float(np.dot(va, vb) / denom)


async def responder_saudacao(prompt: str) -> str:
    """Resposta curta via roteador leve (llama3.2:3b)."""
    return await ollama.generate(
        model=settings.model_router,
        prompt=(
            "Você é o Profinho, assistente educacional simpático. "
            f"O usuário disse: \"{prompt[:200]}\"\n"
            "Responda em português do Brasil, de forma breve e acolhedora (2-3 frases)."
        ),
        temperature=0.4,
        options={"num_predict": 120},
    )


async def adaptar_resposta_cache(pergunta: str, resposta_base: str) -> str:
    """Reformata resposta do cache para a pergunta atual (llama3.2:3b)."""
    return await ollama.generate(
        model=settings.model_router,
        prompt=(
            "Adapte a resposta abaixo para a pergunta do usuário. "
            "Mantenha os fatos; ajuste tom e foco. Responda em português do Brasil.\n\n"
            f"Pergunta: {pergunta[:1500]}\n\n"
            f"Resposta base:\n{resposta_base[:6000]}\n\n"
            "Resposta adaptada:"
        ),
        temperature=0.3,
        options={"num_predict": 1024},
    )


async def _buscar_exato(prompt_norm: str) -> Optional[dict[str, Any]]:
    pool = get_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, resposta, categoria, modelo, fontes
            FROM cache_respostas
            WHERE prompt_norm = $1
            LIMIT 1
            """,
            prompt_norm,
        )
    return dict(row) if row else None


async def _candidatos_semanticos(categoria: Optional[str], limite: int) -> list[dict[str, Any]]:
    pool = get_pool()
    if pool is None:
        return []
    async with pool.acquire() as conn:
        if categoria:
            rows = await conn.fetch(
                """
                SELECT id, prompt, resposta, categoria, modelo, fontes, embedding
                FROM cache_respostas
                WHERE categoria = $1
                ORDER BY criado_em DESC
                LIMIT $2
                """,
                categoria,
                limite,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT id, prompt, resposta, categoria, modelo, fontes, embedding
                FROM cache_respostas
                ORDER BY criado_em DESC
                LIMIT $1
                """,
                limite,
            )
    return [dict(r) for r in rows]


async def buscar(
    prompt: str,
    categoria: Optional[str] = None,
) -> Optional[CacheHit]:
    """Busca exata ou por similaridade de embedding."""
    if not settings.cache_enabled:
        return None

    norm = normalizar_prompt(prompt)
    exato = await _buscar_exato(norm)
    if exato:
        await _registrar_hit(str(exato["id"]))
        fontes = _parse_fontes(exato.get("fontes"))
        return CacheHit(
            resposta=exato["resposta"],
            categoria=exato.get("categoria") or "chat",
            modelo=exato.get("modelo") or settings.model_chat,
            fontes=fontes,
            similaridade=1.0,
            motivo="Cache: pergunta idêntica (normalizada).",
            cache_id=str(exato["id"]),
        )

    try:
        vetor = await embeddings.embed_texto(prompt)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Embedding falhou; cache semântico ignorado: %s", exc)
        return None

    melhor: Optional[CacheHit] = None
    for row in await _candidatos_semanticos(categoria, settings.cache_max_candidatos):
        emb = row.get("embedding")
        if isinstance(emb, str):
            emb = json.loads(emb)
        if not isinstance(emb, list):
            continue
        sim = _cosine(vetor, emb)
        if sim < settings.cache_similaridade_min:
            continue
        if melhor is None or sim > melhor.similaridade:
            fontes = _parse_fontes(row.get("fontes"))
            melhor = CacheHit(
                resposta=row["resposta"],
                categoria=row.get("categoria") or "chat",
                modelo=row.get("modelo") or settings.model_chat,
                fontes=fontes,
                similaridade=round(sim, 4),
                motivo=f"Cache semântico (similaridade {sim:.2f}).",
                cache_id=str(row["id"]),
            )

    if melhor:
        await _registrar_hit(melhor.cache_id)
    return melhor


async def salvar(
    prompt: str,
    resposta: str,
    categoria: str,
    modelo: str,
    usar_web: bool,
    fontes: list[str],
) -> None:
    if not settings.cache_enabled or len(resposta.strip()) < 20:
        return
    pool = get_pool()
    if pool is None:
        return

    norm = normalizar_prompt(prompt)
    try:
        vetor = await embeddings.embed_texto(prompt)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Não foi possível salvar cache (embedding): %s", exc)
        return

    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO cache_respostas
                    (prompt, prompt_norm, embedding, resposta, categoria, modelo, usar_web, fontes)
                VALUES ($1, $2, $3::jsonb, $4, $5, $6, $7, $8::jsonb)
                ON CONFLICT (prompt_norm) DO UPDATE SET
                    prompt = EXCLUDED.prompt,
                    embedding = EXCLUDED.embedding,
                    resposta = EXCLUDED.resposta,
                    categoria = EXCLUDED.categoria,
                    modelo = EXCLUDED.modelo,
                    usar_web = EXCLUDED.usar_web,
                    fontes = EXCLUDED.fontes
                """,
                prompt[:8000],
                norm,
                json.dumps(vetor),
                resposta[:50000],
                categoria,
                modelo,
                usar_web,
                json.dumps(fontes),
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Falha ao salvar cache: %s", exc)


async def _registrar_hit(cache_id: str) -> None:
    pool = get_pool()
    if pool is None:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE cache_respostas
            SET hits = hits + 1, ultimo_hit = now()
            WHERE id = $1::uuid
            """,
            cache_id,
        )


def _parse_fontes(val: Any) -> list[str]:
    if val is None:
        return []
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


async def sessao_tem_historico(sessao_id: Optional[str]) -> bool:
    if not sessao_id:
        return False
    from app import database as db

    msgs = await db.listar_mensagens(sessao_id, limite=1)
    return len(msgs) > 0
