"""Pool de conexões PostgreSQL (asyncpg) e helpers de persistência.

Inclui o sistema de memória (estilo ChatGPT/Claude/Cursor):
  - sessoes   : threads de conversa
  - mensagens : turnos (memória de curto prazo / contexto)
  - memorias  : fatos persistentes por token (memória de longo prazo)
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

import asyncpg

from app.config import settings

logger = logging.getLogger("profinho.db")

_pool: Optional[asyncpg.Pool] = None


async def init_pool() -> None:
    """Cria o pool de conexões. Não derruba a API se o banco estiver fora do ar."""
    global _pool
    if _pool is not None:
        return
    try:
        _pool = await asyncpg.create_pool(
            host=settings.postgres_host,
            port=settings.postgres_port,
            database=settings.postgres_db,
            user=settings.postgres_user,
            password=settings.postgres_password,
            min_size=1,
            max_size=10,
            command_timeout=30,
        )
        logger.info("Pool PostgreSQL inicializado.")
    except Exception as exc:  # noqa: BLE001
        logger.error("Falha ao conectar no PostgreSQL: %s", exc)
        _pool = None


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> Optional[asyncpg.Pool]:
    return _pool


async def fetch_token(token: str) -> Optional[dict[str, Any]]:
    """Busca um token na tabela. Retorna dict ou None."""
    pool = get_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, token, ativo, professor, dominio, tipo_usuario FROM tokens WHERE token = $1",
            token,
        )
    return dict(row) if row else None


# ==========================================================
# SESSÕES (threads de conversa)
# ==========================================================


async def criar_sessao(
    token_id: Optional[str],
    tipo: str = "chat",
    titulo: Optional[str] = None,
    modelo: Optional[str] = None,
    categoria: Optional[str] = None,
) -> Optional[str]:
    """Cria uma sessão e retorna o id (UUID em texto) ou None se o banco estiver fora."""
    pool = get_pool()
    if pool is None:
        return None
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO sessoes (token_id, tipo, titulo, modelo, categoria)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING id
                """,
                token_id,
                tipo,
                (titulo or "Nova conversa")[:120],
                modelo,
                categoria,
            )
        return str(row["id"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("Não foi possível criar sessão: %s", exc)
        return None


async def get_sessao(sessao_id: str, token_id: Optional[str]) -> Optional[dict[str, Any]]:
    """Busca uma sessão, garantindo que pertence ao token (ou master = token_id None)."""
    pool = get_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        if token_id is None:
            row = await conn.fetchrow("SELECT * FROM sessoes WHERE id = $1", sessao_id)
        else:
            row = await conn.fetchrow(
                "SELECT * FROM sessoes WHERE id = $1 AND token_id = $2",
                sessao_id,
                token_id,
            )
    return dict(row) if row else None


async def listar_sessoes(
    token_id: Optional[str],
    tipo: Optional[str] = None,
    incluir_arquivadas: bool = False,
    limite: int = 50,
) -> list[dict[str, Any]]:
    pool = get_pool()
    if pool is None:
        return []
    condicoes = []
    params: list[Any] = []
    if token_id is not None:
        params.append(token_id)
        condicoes.append(f"token_id = ${len(params)}")
    if tipo:
        params.append(tipo)
        condicoes.append(f"tipo = ${len(params)}")
    if not incluir_arquivadas:
        condicoes.append("arquivada = FALSE")
    where = (" WHERE " + " AND ".join(condicoes)) if condicoes else ""
    params.append(limite)
    sql = (
        "SELECT id, tipo, titulo, modelo, categoria, arquivada, criado_em, atualizado_em "
        f"FROM sessoes{where} ORDER BY atualizado_em DESC LIMIT ${len(params)}"
    )
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)
    return [dict(r) for r in rows]


async def atualizar_sessao(
    sessao_id: str,
    token_id: Optional[str],
    titulo: Optional[str] = None,
    arquivada: Optional[bool] = None,
) -> bool:
    pool = get_pool()
    if pool is None:
        return False
    sets = []
    params: list[Any] = []
    if titulo is not None:
        params.append(titulo[:120])
        sets.append(f"titulo = ${len(params)}")
    if arquivada is not None:
        params.append(arquivada)
        sets.append(f"arquivada = ${len(params)}")
    if not sets:
        return False
    params.append(sessao_id)
    where = f"id = ${len(params)}"
    if token_id is not None:
        params.append(token_id)
        where += f" AND token_id = ${len(params)}"
    async with pool.acquire() as conn:
        res = await conn.execute(
            f"UPDATE sessoes SET {', '.join(sets)} WHERE {where}", *params
        )
    return res.endswith("1")


async def deletar_sessao(sessao_id: str, token_id: Optional[str]) -> bool:
    pool = get_pool()
    if pool is None:
        return False
    async with pool.acquire() as conn:
        if token_id is None:
            res = await conn.execute("DELETE FROM sessoes WHERE id = $1", sessao_id)
        else:
            res = await conn.execute(
                "DELETE FROM sessoes WHERE id = $1 AND token_id = $2",
                sessao_id,
                token_id,
            )
    return res.endswith("1")


async def tocar_sessao(sessao_id: str) -> None:
    """Atualiza atualizado_em (move a sessão para o topo da lista)."""
    pool = get_pool()
    if pool is None:
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE sessoes SET atualizado_em = now() WHERE id = $1", sessao_id
            )
    except Exception:  # noqa: BLE001
        pass


# ==========================================================
# MENSAGENS (memória de curto prazo / contexto da sessão)
# ==========================================================


async def adicionar_mensagem(
    sessao_id: str,
    role: str,
    conteudo: str,
    modelo: Optional[str] = None,
    categoria: Optional[str] = None,
    metadados: Optional[dict[str, Any]] = None,
) -> None:
    pool = get_pool()
    if pool is None:
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO mensagens (sessao_id, role, conteudo, modelo, categoria, metadados)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb)
                """,
                sessao_id,
                role,
                conteudo[:30000],
                modelo,
                categoria,
                json.dumps(metadados or {}),
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Não foi possível salvar mensagem: %s", exc)


async def listar_mensagens(
    sessao_id: str, limite: int = 40, ordem_crescente: bool = True
) -> list[dict[str, Any]]:
    """Retorna as últimas `limite` mensagens da sessão (memória de contexto)."""
    pool = get_pool()
    if pool is None:
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, role, conteudo, modelo, categoria, criado_em
            FROM (
                SELECT * FROM mensagens WHERE sessao_id = $1
                ORDER BY criado_em DESC LIMIT $2
            ) sub
            ORDER BY criado_em ASC
            """,
            sessao_id,
            limite,
        )
    msgs = [dict(r) for r in rows]
    if not ordem_crescente:
        msgs.reverse()
    return msgs


# ==========================================================
# MEMÓRIAS (longo prazo, por token)
# ==========================================================


async def salvar_memoria(
    token_id: Optional[str], chave: str, valor: str, origem: str = "manual"
) -> Optional[dict[str, Any]]:
    pool = get_pool()
    if pool is None or token_id is None:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO memorias (token_id, chave, valor, origem)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (token_id, chave)
            DO UPDATE SET valor = EXCLUDED.valor, origem = EXCLUDED.origem
            RETURNING id, chave, valor, origem, criado_em, atualizado_em
            """,
            token_id,
            chave[:200],
            valor[:4000],
            origem,
        )
    return dict(row) if row else None


async def listar_memorias(token_id: Optional[str], limite: int = 100) -> list[dict[str, Any]]:
    pool = get_pool()
    if pool is None or token_id is None:
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, chave, valor, origem, criado_em, atualizado_em "
            "FROM memorias WHERE token_id = $1 ORDER BY atualizado_em DESC LIMIT $2",
            token_id,
            limite,
        )
    return [dict(r) for r in rows]


async def deletar_memoria(memoria_id: str, token_id: Optional[str]) -> bool:
    pool = get_pool()
    if pool is None or token_id is None:
        return False
    async with pool.acquire() as conn:
        res = await conn.execute(
            "DELETE FROM memorias WHERE id = $1 AND token_id = $2", memoria_id, token_id
        )
    return res.endswith("1")


# ==========================================================
# CONTEXTO DO USUÁRIO (classificado, restrito ao token)
# ==========================================================


async def upsert_contexto_usuario(
    token_id: str,
    tipo: str,
    chave: str,
    valor: str,
    origem_prompt: Optional[str] = None,
    confianca: float = 0.8,
) -> Optional[dict[str, Any]]:
    pool = get_pool()
    if pool is None:
        return None
    chave_norm = chave.strip().lower().replace(" ", "_")[:120]
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO contexto_usuario
                    (token_id, tipo, chave, valor, origem_prompt, confianca)
                VALUES ($1::uuid, $2, $3, $4, $5, $6)
                ON CONFLICT (token_id, chave) DO UPDATE SET
                    tipo = EXCLUDED.tipo,
                    valor = EXCLUDED.valor,
                    origem_prompt = EXCLUDED.origem_prompt,
                    confianca = EXCLUDED.confianca
                RETURNING id, tipo, chave, valor, origem_prompt, confianca, criado_em, atualizado_em
                """,
                token_id,
                tipo,
                chave_norm,
                valor[:2000],
                origem_prompt[:500] if origem_prompt else None,
                confianca,
            )
        return dict(row) if row else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Não foi possível salvar contexto_usuario: %s", exc)
        return None


async def listar_contexto_usuario(
    token_id: str, limite: int = 40, tipo: Optional[str] = None
) -> list[dict[str, Any]]:
    pool = get_pool()
    if pool is None:
        return []
    async with pool.acquire() as conn:
        if tipo:
            rows = await conn.fetch(
                """
                SELECT id, tipo, chave, valor, origem_prompt, confianca, criado_em, atualizado_em
                FROM contexto_usuario
                WHERE token_id = $1::uuid AND tipo = $2
                ORDER BY atualizado_em DESC
                LIMIT $3
                """,
                token_id,
                tipo,
                limite,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT id, tipo, chave, valor, origem_prompt, confianca, criado_em, atualizado_em
                FROM contexto_usuario
                WHERE token_id = $1::uuid
                ORDER BY atualizado_em DESC
                LIMIT $2
                """,
                token_id,
                limite,
            )
    return [dict(r) for r in rows]


async def deletar_contexto_usuario(contexto_id: str, token_id: str) -> bool:
    pool = get_pool()
    if pool is None:
        return False
    async with pool.acquire() as conn:
        res = await conn.execute(
            "DELETE FROM contexto_usuario WHERE id = $1::uuid AND token_id = $2::uuid",
            contexto_id,
            token_id,
        )
    return res.endswith("1")
