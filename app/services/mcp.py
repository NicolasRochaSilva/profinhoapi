"""Camada MCP (Model Context Protocol) simplificada.

Padroniza as ferramentas que os modelos podem usar:
  IA -> MCP -> { SearXNG, Crawl4AI, PostgreSQL, GitHub, Arquivos, APIs }

Cada tool tem nome, descrição (JSON-schema-like) e um handler async.
Isso facilita futuras integrações e o uso de "tools" pelos modelos.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

import httpx

from app.config import settings
from app.database import get_pool
from app.services import crawl4ai, searxng

logger = logging.getLogger("profinho.mcp")

Handler = Callable[[dict[str, Any]], Awaitable[Any]]


class Tool:
    def __init__(self, name: str, description: str, parameters: dict[str, Any], handler: Handler):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.handler = handler

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


# --- Handlers ---


async def _tool_web_search(args: dict[str, Any]) -> Any:
    return await searxng.buscar(args["query"], int(args.get("max_resultados", 5)))


async def _tool_read_url(args: dict[str, Any]) -> Any:
    return await crawl4ai.ler_pagina(args["url"], int(args.get("max_chars", 12000)))


async def _tool_db_query(args: dict[str, Any]) -> Any:
    """Executa SELECT no PostgreSQL (somente leitura por segurança)."""
    sql = args["sql"].strip()
    if not sql.lower().startswith("select"):
        raise ValueError("Apenas comandos SELECT são permitidos via MCP db_query.")
    pool = get_pool()
    if pool is None:
        raise RuntimeError("Banco indisponível.")
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql)
    return [dict(r) for r in rows]


async def _tool_github(args: dict[str, Any]) -> Any:
    """Consulta pública à API do GitHub (ex.: ler README/arquivos de um repo)."""
    path = args["path"].lstrip("/")
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"https://api.github.com/{path}",
            headers={"Accept": "application/vnd.github+json"},
        )
        resp.raise_for_status()
        return resp.json()


async def _tool_http_get(args: dict[str, Any]) -> Any:
    """GET genérico a uma API externa."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(args["url"], params=args.get("params"))
        resp.raise_for_status()
        try:
            return resp.json()
        except Exception:  # noqa: BLE001
            return resp.text[:8000]


REGISTRY: dict[str, Tool] = {}


def _registrar(tool: Tool) -> None:
    REGISTRY[tool.name] = tool


_registrar(Tool(
    "web_search",
    "Busca informações atualizadas na internet via SearXNG.",
    {"type": "object", "properties": {
        "query": {"type": "string"},
        "max_resultados": {"type": "integer", "default": 5},
    }, "required": ["query"]},
    _tool_web_search,
))

_registrar(Tool(
    "read_url",
    "Lê e extrai o conteúdo limpo (markdown) de uma página/documentação via Crawl4AI.",
    {"type": "object", "properties": {
        "url": {"type": "string"},
        "max_chars": {"type": "integer", "default": 12000},
    }, "required": ["url"]},
    _tool_read_url,
))

_registrar(Tool(
    "db_query",
    "Executa uma consulta SELECT no PostgreSQL do Profinho.",
    {"type": "object", "properties": {"sql": {"type": "string"}}, "required": ["sql"]},
    _tool_db_query,
))

_registrar(Tool(
    "github",
    "Consulta a API pública do GitHub (ex.: repos/{owner}/{repo}/readme).",
    {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    _tool_github,
))

_registrar(Tool(
    "http_get",
    "Faz uma requisição GET a uma API/URL externa.",
    {"type": "object", "properties": {
        "url": {"type": "string"},
        "params": {"type": "object"},
    }, "required": ["url"]},
    _tool_http_get,
))


def listar_tools() -> list[dict[str, Any]]:
    return [t.schema() for t in REGISTRY.values()]


async def chamar_tool(nome: str, args: dict[str, Any]) -> Any:
    tool = REGISTRY.get(nome)
    if tool is None:
        raise KeyError(f"Tool '{nome}' não registrada.")
    logger.info("MCP -> chamando tool '%s' com %s", nome, args)
    return await tool.handler(args)
