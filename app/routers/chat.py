"""Rota de chat com roteamento automático de modelo."""

from __future__ import annotations

import logging
import time
from typing import Optional

from fastapi import APIRouter, Depends

from app.auth import require_token
from app.config import settings
from app.ollama_client import ollama
from app.router_model import decidir_usar_web, rotear
from app.schemas import Categoria, ChatRequest, ChatResponse, RouteResponse
from app.services import cache_respostas as cache_svc
from app.services import crawl4ai, memoria, searxng

router = APIRouter(tags=["chat"])
logger = logging.getLogger("profinho.chat")

_SYSTEMS = {
    "chat": "Você é o Profinho, um assistente educacional simpático e prestativo. Responda em português do Brasil.",
    "programacao": "Você é o Profinho Coder, especialista em ASP.NET, Python, SQL, HTML/CSS/JS e APIs. Gere código correto e explique de forma objetiva. Responda em português do Brasil.",
    "educacao": "Você é o Profinho Educador, especialista em pedagogia. Crie planos de aula, exercícios, provas e resumos claros e bem estruturados. Responda em português do Brasil.",
    "imagem": "Você é o Profinho Vision, especialista em análise de imagens. Responda em português do Brasil.",
}


def _as_categoria(val: str) -> Categoria:
    if val in _SYSTEMS:
        return val  # type: ignore[return-value]
    return "chat"


async def _pode_usar_cache(req: ChatRequest) -> bool:
    if not settings.cache_enabled:
        return False
    if req.usar_web is True:
        return False
    if req.categoria == "imagem":
        return False
    if req.historico:
        return False
    if await cache_svc.sessao_tem_historico(req.sessao_id):
        return False
    return True


async def _persistir_sessao(
    req: ChatRequest,
    token_id: Optional[str],
    resposta: str,
    categoria: str,
    modelo: str,
    fontes: list[str],
) -> Optional[str]:
    if not req.salvar:
        return req.sessao_id
    sessao_id = await memoria.garantir_sessao(
        sessao_id=req.sessao_id,
        token_id=token_id,
        tipo="chat",
        primeiro_prompt=req.prompt,
        modelo=modelo,
        categoria=categoria,
    )
    if sessao_id:
        await memoria.registrar_turno(
            sessao_id=sessao_id,
            prompt_usuario=req.prompt,
            resposta=resposta,
            modelo=modelo,
            categoria=categoria,
            metadados_assistente={"fontes": fontes, "cache": True} if fontes else {"cache": True},
        )
    return sessao_id


@router.post("/route", response_model=RouteResponse, summary="Apenas roteia (escolhe o modelo)")
async def route(req: ChatRequest, _=Depends(require_token)):
    categoria, modelo, motivo = await rotear(req.prompt)
    return RouteResponse(categoria=categoria, modelo=modelo, motivo=motivo)


@router.post("/chat", response_model=ChatResponse, summary="Chat com seleção automática de modelo")
async def chat(req: ChatRequest, token=Depends(require_token)):
    t0 = time.perf_counter()
    timings: dict[str, float | int] = {}
    token_id = token.get("id")

    # --- Cache / saudação (roteador leve; sem modelo pesado) ---
    if await _pode_usar_cache(req):
        if cache_svc.eh_saudacao_simples(req.prompt):
            t = time.perf_counter()
            resposta = await cache_svc.responder_saudacao(req.prompt)
            timings["saudacao_s"] = round(time.perf_counter() - t, 2)
            categoria: Categoria = req.categoria or "chat"
            modelo = settings.model_router
            sessao_id = await _persistir_sessao(
                req, token_id, resposta, categoria, modelo, []
            )
            await cache_svc.salvar(
                req.prompt, resposta, categoria, modelo, False, []
            )
            timings["total_s"] = round(time.perf_counter() - t0, 2)
            logger.info("POST /chat saudação | tempos=%s", timings)
            return ChatResponse(
                categoria=categoria,
                modelo=modelo,
                resposta=resposta,
                motivo_roteamento="Saudação respondida pelo roteador leve.",
                usar_web=False,
                motivo_web="Saudação: web não utilizada.",
                fontes=[],
                sessao_id=sessao_id,
                cache_hit=False,
                motivo_cache="Resposta curta via llama3.2:3b.",
            )

        t = time.perf_counter()
        hit = await cache_svc.buscar(req.prompt, req.categoria)
        timings["cache_busca_s"] = round(time.perf_counter() - t, 2)
        if hit:
            resposta = hit.resposta
            if hit.similaridade < settings.cache_reformat_min:
                t = time.perf_counter()
                resposta = await cache_svc.adaptar_resposta_cache(req.prompt, hit.resposta)
                timings["cache_reformat_s"] = round(time.perf_counter() - t, 2)
            categoria = _as_categoria(hit.categoria)
            modelo = hit.modelo
            sessao_id = await _persistir_sessao(
                req, token_id, resposta, categoria, modelo, hit.fontes
            )
            timings["total_s"] = round(time.perf_counter() - t0, 2)
            logger.info(
                "POST /chat CACHE sim=%.2f | tempos=%s", hit.similaridade, timings
            )
            return ChatResponse(
                categoria=categoria,
                modelo=modelo,
                resposta=resposta,
                motivo_roteamento=hit.motivo,
                usar_web=False,
                motivo_web="Cache: web não reexecutada.",
                fontes=hit.fontes,
                sessao_id=sessao_id,
                cache_hit=True,
                motivo_cache=hit.motivo,
            )

    # --- Fluxo normal ---
    if req.categoria:
        categoria = req.categoria
        modelo = settings.categories[categoria]
        motivo = "Categoria informada pelo cliente."
        timings["roteamento_s"] = 0.0
    else:
        t = time.perf_counter()
        categoria, modelo, motivo = await rotear(req.prompt)
        timings["roteamento_s"] = round(time.perf_counter() - t, 2)

    if req.usar_web is None:
        t = time.perf_counter()
        usar_web_efetivo, motivo_web = await decidir_usar_web(req.prompt)
        timings["decisao_web_s"] = round(time.perf_counter() - t, 2)
    elif req.usar_web:
        usar_web_efetivo, motivo_web = True, "Busca web forçada pelo cliente."
    else:
        usar_web_efetivo, motivo_web = False, "Busca web desativada pelo cliente."

    fontes: list[str] = []
    contexto_web = ""
    if usar_web_efetivo:
        t = time.perf_counter()
        resultados = await searxng.buscar(req.prompt, max_resultados=4)
        urls = [r["url"] for r in resultados if r.get("url")]
        conteudos = await crawl4ai.ler_varias(urls, max_chars_total=12000)
        timings["web_s"] = round(time.perf_counter() - t, 2)
        fontes = list(conteudos.keys())
        if conteudos:
            contexto_web = "\n\n".join(
                f"Fonte: {u}\n{c[:4000]}" for u, c in conteudos.items()
            )
    else:
        timings["web_s"] = 0.0

    system = req.system or _SYSTEMS.get(categoria, _SYSTEMS["chat"])

    sessao_id = None
    if req.salvar:
        t = time.perf_counter()
        sessao_id = await memoria.garantir_sessao(
            sessao_id=req.sessao_id,
            token_id=token_id,
            tipo="chat",
            primeiro_prompt=req.prompt,
            modelo=modelo,
            categoria=categoria,
        )
        timings["sessao_s"] = round(time.perf_counter() - t, 2)

    user_content = req.prompt
    if contexto_web:
        user_content = (
            f"{req.prompt}\n\n[Informações atualizadas da web]\n{contexto_web}"
        )

    historico_extra = [{"role": m.role, "content": m.content} for m in req.historico]
    t = time.perf_counter()
    messages = await memoria.montar_contexto(
        sessao_id=sessao_id,
        token_id=token_id,
        system=system,
        prompt_usuario=user_content,
        historico_extra=historico_extra,
    )
    timings["contexto_s"] = round(time.perf_counter() - t, 2)
    timings["mensagens_ctx"] = len(messages)

    t = time.perf_counter()
    resposta = await ollama.chat(
        model=modelo, messages=messages, temperature=req.temperature, exclusivo=True
    )
    timings["ollama_s"] = round(time.perf_counter() - t, 2)

    if req.salvar and sessao_id:
        t = time.perf_counter()
        await memoria.registrar_turno(
            sessao_id=sessao_id,
            prompt_usuario=req.prompt,
            resposta=resposta,
            modelo=modelo,
            categoria=categoria,
            metadados_assistente={"fontes": fontes} if fontes else None,
        )
        timings["salvar_s"] = round(time.perf_counter() - t, 2)

    if await _pode_usar_cache(req) and not usar_web_efetivo:
        await cache_svc.salvar(
            req.prompt, resposta, categoria, modelo, usar_web_efetivo, fontes
        )

    timings["total_s"] = round(time.perf_counter() - t0, 2)
    logger.info(
        "POST /chat modelo=%s categoria=%s usar_web=%s | tempos=%s",
        modelo,
        categoria,
        usar_web_efetivo,
        timings,
    )

    return ChatResponse(
        categoria=categoria,
        modelo=modelo,
        resposta=resposta,
        motivo_roteamento=motivo,
        usar_web=usar_web_efetivo,
        motivo_web=motivo_web,
        fontes=fontes,
        sessao_id=sessao_id,
        cache_hit=False,
        motivo_cache=None,
    )
