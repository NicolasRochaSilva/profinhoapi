"""Cache semântico de prompts/respostas no PostgreSQL."""

from __future__ import annotations

import json
import logging
import re
import asyncio
from dataclasses import dataclass
from typing import Any, Optional

import numpy as np

from app.config import settings
from app.database import get_pool
from app.ollama_client import ollama
from app.services import embeddings

logger = logging.getLogger("profinho.cache")

SCOPE_SHARED = "SHARED"

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
    t = re.sub(r"[^\w\sáàâãéêíóôõúç]", " ", t, flags=re.UNICODE)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def eh_saudacao_simples(texto: str) -> bool:
    norm = normalizar_prompt(texto)
    if len(norm) > 40:
        return False
    if norm in _SAUDACOES:
        return True
    palavras = norm.split()
    if len(palavras) <= 3 and any(p in _SAUDACOES for p in palavras):
        return True
    # "olá profinho", "oi tudo bem"
    return len(palavras) <= 4 and palavras[0] in _SAUDACOES


_PERSONAL_INDICIOS = (
    "minha turma",
    "meu aluno",
    "meus alunos",
    "minha classe",
    "plano de aula",
    "crie um",
    "crie uma",
    "monte um",
    "monte uma",
    "faça um",
    "faca um",
    "elabore um",
    "elabore uma",
    "gabarito",
    "prova sobre",
    "exercício sobre",
    "exercicio sobre",
    "atividade sobre",
    "simulado sobre",
)


def eh_pergunta_generica(texto: str) -> bool:
    """Pergunta factual/educacional reutilizável entre professores (ex.: fotossíntese)."""
    if eh_saudacao_simples(texto):
        return False

    norm = normalizar_prompt(texto)
    if len(norm) < 10:
        return False

    for indicio in _PERSONAL_INDICIOS:
        if indicio in norm:
            return False

    from app.keywords import WEB_NAO, detectar_categoria

    cat, score = detectar_categoria(texto)
    if cat in ("programacao", "imagem") and score > 0:
        return False

    texto_low = texto.lower()
    for palavra in WEB_NAO:
        if re.search(r"(?<!\w)" + re.escape(palavra) + r"(?!\w)", texto_low):
            return True

    if cat == "educacao" and score >= 1:
        return True

    return bool(
        re.search(
            r"^(o que|como|explique|explica|defina|qual|quais|por que|porque)\b",
            norm,
        )
    )


def _cosine(a: list[float], b: list[float]) -> float:
    va, vb = np.array(a, dtype=np.float32), np.array(b, dtype=np.float32)
    denom = float(np.linalg.norm(va) * np.linalg.norm(vb)) + 1e-9
    return float(np.dot(va, vb) / denom)


async def responder_pergunta_generica(
    prompt: str,
    tipo_usuario: str,
    contexto_token: str = "",
    categoria: str = "educacao",
) -> tuple[str, str]:
    """Perguntas factuais ('o que é…') via roteador 3b — muito mais rápido que 8b/7b."""
    from app.services.perfil_usuario import system_prompt

    modelo = settings.model_router
    ctx = f"\n\n{contexto_token}" if contexto_token else ""
    resposta = await ollama.generate(
        model=modelo,
        system=system_prompt(categoria, tipo_usuario),
        prompt=(
            f"Pergunta: {prompt[:2000]}{ctx}\n\n"
            "Responda de forma clara, didática e objetiva em português do Brasil."
        ),
        temperature=0.5,
        options={"num_predict": settings.chat_num_predict_rapido},
        exclusivo=False,
    )
    return resposta, modelo


async def iter_pergunta_generica(
    prompt: str,
    tipo_usuario: str,
    contexto_token: str = "",
    categoria: str = "educacao",
):
    from app.services.perfil_usuario import system_prompt

    modelo = settings.model_router
    ctx = f"\n\n{contexto_token}" if contexto_token else ""
    async for parte in ollama.generate_stream(
        model=modelo,
        system=system_prompt(categoria, tipo_usuario),
        prompt=(
            f"Pergunta: {prompt[:2000]}{ctx}\n\n"
            "Responda de forma clara, didática e objetiva em português do Brasil."
        ),
        temperature=0.5,
        options={"num_predict": settings.chat_num_predict_rapido},
        exclusivo=False,
    ):
        yield parte


async def responder_saudacao(
    prompt: str, contexto_token: str = "", tipo_usuario: str = "professor"
) -> str:
    """Resposta curta via modelo ultra-leve (ex.: llama3.2:1b)."""
    from app.services.perfil_usuario import instrucao_saudacao

    ctx = f"\n\n{contexto_token}" if contexto_token else ""
    return await ollama.generate(
        model=settings.model_light,
        prompt=(
            f"{instrucao_saudacao(tipo_usuario)} "
            f"O usuário disse: \"{prompt[:200]}\""
            f"{ctx}\n"
            "Responda em português do Brasil. "
            "Se souber o nome do usuário pelo contexto, use-o."
        ),
        temperature=0.4,
        options={"num_predict": 120},
    )


async def iter_saudacao(
    prompt: str, contexto_token: str = "", tipo_usuario: str = "professor"
):
    from app.services.perfil_usuario import instrucao_saudacao

    ctx = f"\n\n{contexto_token}" if contexto_token else ""
    async for parte in ollama.generate_stream(
        model=settings.model_light,
        prompt=(
            f"{instrucao_saudacao(tipo_usuario)} "
            f"O usuário disse: \"{prompt[:200]}\""
            f"{ctx}\n"
            "Responda em português do Brasil. "
            "Se souber o nome do usuário pelo contexto, use-o."
        ),
        temperature=0.4,
        options={"num_predict": 120},
        exclusivo=False,
    ):
        yield parte


async def adaptar_resposta_cache(
    pergunta: str, resposta_base: str, contexto_token: str = ""
) -> str:
    """Reformata resposta do cache para a pergunta atual (modelo leve)."""
    ctx = f"\n\nContexto deste usuário (privado, use se relevante):\n{contexto_token}" if contexto_token else ""
    return await ollama.generate(
        model=settings.model_light,
        prompt=(
            "Adapte a resposta abaixo para a pergunta do usuário. "
            "Mantenha os fatos; ajuste tom e foco. Responda em português do Brasil."
            f"{ctx}\n\n"
            f"Pergunta: {pergunta[:1500]}\n\n"
            f"Resposta base:\n{resposta_base[:6000]}\n\n"
            "Resposta adaptada:"
        ),
        temperature=0.3,
        options={"num_predict": 1024},
    )


def _token_scope(token_id: Optional[str]) -> str:
    return str(token_id) if token_id else "GLOBAL"


async def _buscar_exato(prompt_norm: str, token_scope: str) -> Optional[dict[str, Any]]:
    pool = get_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, resposta, categoria, modelo, fontes
            FROM cache_respostas
            WHERE prompt_norm = $1
              AND token_scope = $2
            LIMIT 1
            """,
            prompt_norm,
            token_scope,
        )
    return dict(row) if row else None


async def _buscar_exato_outros(prompt_norm: str, excluir_scope: str) -> Optional[dict[str, Any]]:
    pool = get_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, resposta, categoria, modelo, fontes
            FROM cache_respostas
            WHERE prompt_norm = $1
              AND token_scope NOT IN ($2, $3)
            ORDER BY hits DESC, criado_em DESC
            LIMIT 1
            """,
            prompt_norm,
            excluir_scope,
            SCOPE_SHARED,
        )
    return dict(row) if row else None


def _hit_de_exato(row: dict[str, Any], motivo: str) -> CacheHit:
    fontes = _parse_fontes(row.get("fontes"))
    return CacheHit(
        resposta=row["resposta"],
        categoria=row.get("categoria") or "chat",
        modelo=row.get("modelo") or settings.model_chat,
        fontes=fontes,
        similaridade=1.0,
        motivo=motivo,
        cache_id=str(row["id"]),
    )


async def _candidatos_semanticos(
    token_scope: str,
    categoria: Optional[str],
    limite: int,
) -> list[dict[str, Any]]:
    pool = get_pool()
    if pool is None:
        return []
    async with pool.acquire() as conn:
        if categoria:
            rows = await conn.fetch(
                """
                SELECT id, prompt, resposta, categoria, modelo, fontes, embedding
                FROM cache_respostas
                WHERE token_scope = $1
                  AND categoria = $2
                ORDER BY criado_em DESC
                LIMIT $3
                """,
                token_scope,
                categoria,
                limite,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT id, prompt, resposta, categoria, modelo, fontes, embedding
                FROM cache_respostas
                WHERE token_scope = $1
                ORDER BY criado_em DESC
                LIMIT $2
                """,
                token_scope,
                limite,
            )
    return [dict(r) for r in rows]


async def _candidatos_semanticos_outros(
    excluir_scope: str,
    categoria: Optional[str],
    limite: int,
) -> list[dict[str, Any]]:
    pool = get_pool()
    if pool is None:
        return []
    async with pool.acquire() as conn:
        if categoria:
            rows = await conn.fetch(
                """
                SELECT id, prompt, resposta, categoria, modelo, fontes, embedding
                FROM cache_respostas
                WHERE token_scope NOT IN ($1, $2)
                  AND categoria = $3
                ORDER BY hits DESC, criado_em DESC
                LIMIT $4
                """,
                excluir_scope,
                SCOPE_SHARED,
                categoria,
                limite,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT id, prompt, resposta, categoria, modelo, fontes, embedding
                FROM cache_respostas
                WHERE token_scope NOT IN ($1, $2)
                ORDER BY hits DESC, criado_em DESC
                LIMIT $3
                """,
                excluir_scope,
                SCOPE_SHARED,
                limite,
            )
    return [dict(r) for r in rows]


def _melhor_semantico(
    vetor: list[float],
    candidatos: list[dict[str, Any]],
    motivo_base: str,
) -> Optional[CacheHit]:
    melhor: Optional[CacheHit] = None
    for row in candidatos:
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
                motivo=f"{motivo_base} (similaridade {sim:.2f}).",
                cache_id=str(row["id"]),
            )
    return melhor


async def buscar(
    prompt: str,
    categoria: Optional[str] = None,
    token_id: Optional[str] = None,
) -> Optional[CacheHit]:
    """Busca exata ou por similaridade de embedding (token → compartilhado → outros)."""
    if not settings.cache_enabled:
        return None

    norm = normalizar_prompt(prompt)
    scope = _token_scope(token_id)
    from app.services.contexto_usuario import eh_consulta_pessoal

    generica = eh_pergunta_generica(prompt) and not eh_consulta_pessoal(prompt)

    exato = await _buscar_exato(norm, scope)
    if exato:
        hit = _hit_de_exato(exato, "Cache: pergunta idêntica (seu histórico).")
        await _registrar_hit(hit.cache_id)
        return hit

    if generica:
        exato = await _buscar_exato(norm, SCOPE_SHARED)
        if exato:
            hit = _hit_de_exato(exato, "Cache compartilhado: pergunta idêntica.")
            await _registrar_hit(hit.cache_id)
            return hit

        exato = await _buscar_exato_outros(norm, scope)
        if exato:
            hit = _hit_de_exato(
                exato, "Cache de outro professor: pergunta idêntica."
            )
            await _registrar_hit(hit.cache_id)
            return hit

    try:
        vetor = await asyncio.wait_for(
            embeddings.embed_texto(prompt),
            timeout=45.0,
        )
    except TimeoutError:
        logger.warning("Timeout no embedding; cache semântico ignorado.")
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Embedding falhou; cache semântico ignorado: %s", exc)
        return None

    melhor = _melhor_semantico(
        vetor,
        await _candidatos_semanticos(scope, categoria, settings.cache_max_candidatos),
        "Cache semântico",
    )
    if melhor:
        await _registrar_hit(melhor.cache_id)
        return melhor

    if not generica:
        return None

    melhor = _melhor_semantico(
        vetor,
        await _candidatos_semanticos(
            SCOPE_SHARED, categoria, settings.cache_max_candidatos
        ),
        "Cache compartilhado",
    )
    if melhor:
        await _registrar_hit(melhor.cache_id)
        return melhor

    melhor = _melhor_semantico(
        vetor,
        await _candidatos_semanticos_outros(
            scope, categoria, settings.cache_max_candidatos
        ),
        "Cache de outro professor",
    )
    if melhor:
        await _registrar_hit(melhor.cache_id)
    return melhor


async def _upsert_entrada(
    *,
    token_id: Optional[str],
    token_scope: str,
    prompt: str,
    norm: str,
    vetor: list[float],
    resposta: str,
    categoria: str,
    modelo: str,
    usar_web: bool,
    fontes: list[str],
) -> None:
    pool = get_pool()
    if pool is None:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO cache_respostas
                (token_id, token_scope, prompt, prompt_norm, embedding, resposta,
                 categoria, modelo, usar_web, fontes)
            VALUES ($1::uuid, $2, $3, $4, $5::jsonb, $6, $7, $8, $9, $10::jsonb)
            ON CONFLICT (token_scope, prompt_norm) DO UPDATE SET
                token_id = EXCLUDED.token_id,
                prompt = EXCLUDED.prompt,
                embedding = EXCLUDED.embedding,
                resposta = EXCLUDED.resposta,
                categoria = EXCLUDED.categoria,
                modelo = EXCLUDED.modelo,
                usar_web = EXCLUDED.usar_web,
                fontes = EXCLUDED.fontes
            """,
            token_id,
            token_scope,
            prompt[:8000],
            norm,
            json.dumps(vetor),
            resposta[:50000],
            categoria,
            modelo,
            usar_web,
            json.dumps(fontes),
        )


async def salvar(
    prompt: str,
    resposta: str,
    categoria: str,
    modelo: str,
    usar_web: bool,
    fontes: list[str],
    token_id: Optional[str] = None,
) -> None:
    if not settings.cache_enabled or len(resposta.strip()) < 20:
        return

    norm = normalizar_prompt(prompt)
    try:
        vetor = await embeddings.embed_texto(prompt)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Não foi possível salvar cache (embedding): %s", exc)
        return

    scope = _token_scope(token_id)
    try:
        await _upsert_entrada(
            token_id=token_id,
            token_scope=scope,
            prompt=prompt,
            norm=norm,
            vetor=vetor,
            resposta=resposta,
            categoria=categoria,
            modelo=modelo,
            usar_web=usar_web,
            fontes=fontes,
        )
        from app.services.contexto_usuario import eh_consulta_pessoal

        if eh_pergunta_generica(prompt) and not eh_consulta_pessoal(prompt):
            await _upsert_entrada(
                token_id=None,
                token_scope=SCOPE_SHARED,
                prompt=prompt,
                norm=norm,
                vetor=vetor,
                resposta=resposta,
                categoria=categoria,
                modelo=modelo,
                usar_web=usar_web,
                fontes=fontes,
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
