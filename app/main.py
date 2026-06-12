"""Profinho API - ponto de entrada FastAPI.

IA educacional multi-modelo (Ollama) com roteador automático, visão,
pesquisa na web (SearXNG + Crawl4AI), MCP e agente estilo Cursor.
Exposta na porta 7000. Documentação em /docs (Swagger).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import close_pool, init_pool
from app.ollama_client import ollama
from app.routers import agent, chat, health, images, openai_compat, search, sessions

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_pool()
    await ollama.preload_modelos_quentes()
    yield
    await close_pool()


DESCRICAO = """
**Profinho API** — plataforma SaaS de IA para professores e escolas.

Capacidades:
- 💬 **Chat** com roteamento automático entre modelos internos (conversa, educação, código, visão).
- 🧭 **Roteador** escolhe o melhor modelo por palavras-chave + classificação.
- 👨‍🏫 Planos de aula, exercícios, provas, resumos e explicações de conteúdo.
- 💻 Código, APIs, SQL, ASP.NET, Python.
- 🖼️ Análise de imagens, OCR e geração de páginas a partir de layouts.
- 🌐 **Pesquisa** (SearXNG + Crawl4AI): ler documentação e gerar código a partir dela.
- 🤖 **Agente**: cria arquivos, escreve código e valida a execução (sempre como Profinho).
- 🧠 **Memória** (estilo ChatGPT/Claude/Cursor): sessões/conversas com histórico de contexto + memória de longo prazo.
- 🔌 **OpenAI-compatible** (`/v1`): use no VS Code para gerar código como o Cursor.

Autenticação: `Authorization: Bearer <token>` (controlado pela tabela `tokens`, campo `ativo`).
"""

app = FastAPI(
    title=settings.api_title,
    version="1.0.0",
    description=DESCRICAO,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(chat.router)
app.include_router(images.router)
app.include_router(search.router)
app.include_router(agent.router)
app.include_router(sessions.router)
app.include_router(openai_compat.router)


@app.get("/", tags=["health"], summary="Raiz")
async def root():
    return {
        "nome": settings.api_title,
        "docs": "/docs",
        "openai_base_url": "/v1",
        "porta": settings.api_port,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.environment != "production",
    )
