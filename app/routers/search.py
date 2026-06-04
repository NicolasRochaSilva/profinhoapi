"""Rotas de pesquisa na web e geração de código a partir de documentação."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.auth import require_token
from app.config import settings
from app.ollama_client import ollama
from app.schemas import (
    DocCodeRequest,
    SearchRequest,
    SearchResponse,
    SearchResultItem,
)
from app.services import crawl4ai, searxng

router = APIRouter(tags=["pesquisa"])


@router.post("/search", response_model=SearchResponse, summary="Buscar na internet (SearXNG)")
async def search(req: SearchRequest, _=Depends(require_token)):
    resultados = await searxng.buscar(req.query, max_resultados=req.max_resultados)
    itens: list[SearchResultItem] = []

    conteudos: dict[str, str] = {}
    if req.ler_conteudo:
        urls = [r["url"] for r in resultados if r.get("url")]
        conteudos = await crawl4ai.ler_varias(urls)

    for r in resultados:
        itens.append(
            SearchResultItem(
                titulo=r.get("titulo", ""),
                url=r.get("url", ""),
                resumo=r.get("resumo", ""),
                conteudo=conteudos.get(r.get("url", "")),
            )
        )
    return SearchResponse(query=req.query, resultados=itens)


@router.post("/doc-to-code", summary="Ler documentação e gerar código baseado nela")
async def doc_to_code(req: DocCodeRequest, _=Depends(require_token)):
    query = req.query_doc or req.objetivo
    resultados = await searxng.buscar(query, max_resultados=req.max_fontes)
    urls = [r["url"] for r in resultados if r.get("url")]
    conteudos = await crawl4ai.ler_varias(urls)

    contexto = "\n\n".join(f"### {u}\n{c[:6000]}" for u, c in conteudos.items())
    prompt = (
        f"Objetivo: {req.objetivo}\n\n"
        f"Use a documentação técnica abaixo como referência e escreva código correto, "
        f"completo e comentado para atingir o objetivo. Explique brevemente o uso.\n\n"
        f"{contexto if contexto else '(Sem conteúdo extraído; use seu conhecimento.)'}"
    )

    resposta = await ollama.generate(
        model=settings.model_code,
        prompt=prompt,
        system="Você é o Profinho Coder. Gere código correto baseado em documentação oficial. Responda em português do Brasil.",
        temperature=0.2,
        options={"num_ctx": 8192},
        exclusivo=True,
    )

    return {
        "objetivo": req.objetivo,
        "modelo": settings.model_code,
        "fontes": list(conteudos.keys()),
        "codigo": resposta,
    }
