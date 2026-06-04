"""Rota de chat com roteamento automático de modelo."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.auth import require_token
from app.config import settings
from app.ollama_client import ollama
from app.router_model import rotear
from app.schemas import ChatRequest, ChatResponse, RouteResponse
from app.services import crawl4ai, memoria, searxng

router = APIRouter(tags=["chat"])

_SYSTEMS = {
    "chat": "Você é o Profinho, um assistente educacional simpático e prestativo. Responda em português do Brasil.",
    "programacao": "Você é o Profinho Coder, especialista em ASP.NET, Python, SQL, HTML/CSS/JS e APIs. Gere código correto e explique de forma objetiva. Responda em português do Brasil.",
    "educacao": "Você é o Profinho Educador, especialista em pedagogia. Crie planos de aula, exercícios, provas e resumos claros e bem estruturados. Responda em português do Brasil.",
    "imagem": "Você é o Profinho Vision, especialista em análise de imagens. Responda em português do Brasil.",
}


@router.post("/route", response_model=RouteResponse, summary="Apenas roteia (escolhe o modelo)")
async def route(req: ChatRequest, _=Depends(require_token)):
    categoria, modelo, motivo = await rotear(req.prompt)
    return RouteResponse(categoria=categoria, modelo=modelo, motivo=motivo)


@router.post("/chat", response_model=ChatResponse, summary="Chat com seleção automática de modelo")
async def chat(req: ChatRequest, token=Depends(require_token)):
    if req.categoria:
        categoria = req.categoria
        modelo = settings.categories[categoria]
        motivo = "Categoria informada pelo cliente."
    else:
        categoria, modelo, motivo = await rotear(req.prompt)

    fontes: list[str] = []
    contexto_web = ""
    if req.usar_web:
        resultados = await searxng.buscar(req.prompt, max_resultados=4)
        urls = [r["url"] for r in resultados if r.get("url")]
        conteudos = await crawl4ai.ler_varias(urls[:3])
        fontes = list(conteudos.keys())
        if conteudos:
            contexto_web = "\n\n".join(
                f"Fonte: {u}\n{c[:5000]}" for u, c in conteudos.items()
            )

    system = req.system or _SYSTEMS.get(categoria, _SYSTEMS["chat"])
    token_id = token.get("id")

    # Garante a sessão (cria nova se necessário) quando salvar=True.
    sessao_id = None
    if req.salvar:
        sessao_id = await memoria.garantir_sessao(
            sessao_id=req.sessao_id,
            token_id=token_id,
            tipo="chat",
            primeiro_prompt=req.prompt,
            modelo=modelo,
            categoria=categoria,
        )

    user_content = req.prompt
    if contexto_web:
        user_content = (
            f"{req.prompt}\n\n[Informações atualizadas da web]\n{contexto_web}"
        )

    # Monta contexto: system (+memórias) + histórico persistido + histórico da requisição.
    historico_extra = [{"role": m.role, "content": m.content} for m in req.historico]
    messages = await memoria.montar_contexto(
        sessao_id=sessao_id,
        token_id=token_id,
        system=system,
        prompt_usuario=user_content,
        historico_extra=historico_extra,
    )

    resposta = await ollama.chat(
        model=modelo, messages=messages, temperature=req.temperature, exclusivo=True
    )

    if req.salvar and sessao_id:
        await memoria.registrar_turno(
            sessao_id=sessao_id,
            prompt_usuario=req.prompt,
            resposta=resposta,
            modelo=modelo,
            categoria=categoria,
            metadados_assistente={"fontes": fontes} if fontes else None,
        )

    return ChatResponse(
        categoria=categoria,
        modelo=modelo,
        resposta=resposta,
        motivo_roteamento=motivo,
        fontes=fontes,
        sessao_id=sessao_id,
    )
