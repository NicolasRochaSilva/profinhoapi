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
from app.services import contexto_usuario as ctx_svc
from app.services import crawl4ai, memoria, moderacao, perfil_usuario as perfil, searxng

router = APIRouter(tags=["chat"])
logger = logging.getLogger("profinho.chat")

_CATEGORIAS = frozenset({"chat", "programacao", "educacao", "imagem"})


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


def _as_categoria(val: str) -> Categoria:
    if val in _CATEGORIAS:
        return val  # type: ignore[return-value]
    return "chat"


def _tipo(token: dict) -> str:
    return perfil.normalizar_tipo(token.get("tipo_usuario"))


async def _extrair_contexto(token_id: Optional[str], prompt: str, tipo_usuario: str) -> None:
    if token_id:
        await ctx_svc.extrair_e_salvar(token_id, prompt, tipo_usuario=tipo_usuario)


def _extrair_contexto_em_background(
    token_id: Optional[str], prompt: str, tipo_usuario: str
) -> None:
    """Não bloqueia a resposta HTTP."""
    import asyncio

    if not token_id:
        return
    asyncio.create_task(_extrair_contexto(token_id, prompt, tipo_usuario))


def _modelo_pesado(modelo: str) -> bool:
    return modelo in settings.modelos_trabalho


@router.post("/route", response_model=RouteResponse, summary="Apenas roteia (escolhe o modelo)")
async def route(req: ChatRequest, _=Depends(require_token)):
    categoria, modelo, motivo = await rotear(req.prompt)
    return RouteResponse(categoria=categoria, modelo=modelo, motivo=motivo)


@router.post("/chat", response_model=ChatResponse, summary="Chat com seleção automática de modelo")
async def chat(req: ChatRequest, token=Depends(require_token)):
    t0 = time.perf_counter()
    timings: dict[str, float | int] = {}
    token_id = token.get("id")
    tipo = _tipo(token)

    bloqueio = moderacao.detectar_tema_bloqueado(req.prompt)
    if bloqueio:
        resposta = moderacao.resposta_bloqueio(bloqueio, tipo)
        sessao_id = await _persistir_sessao(
            req, token_id, resposta, req.categoria or "chat", settings.model_light, []
        )
        timings["total_s"] = round(time.perf_counter() - t0, 2)
        logger.info("POST /chat bloqueado tema=%s tipo=%s", bloqueio.tema, tipo)
        return ChatResponse(
            categoria=req.categoria or "chat",
            modelo=settings.model_light,
            resposta=resposta,
            motivo_roteamento="Conteúdo bloqueado pela moderação.",
            usar_web=False,
            motivo_web="Tema sensível não permitido.",
            fontes=[],
            sessao_id=sessao_id,
            cache_hit=False,
            motivo_cache=None,
            conteudo_bloqueado=True,
            motivo_bloqueio=bloqueio.motivo,
            tipo_usuario=tipo,
        )

    bloco_ctx = await ctx_svc.formatar_bloco(token_id, compacto=True)

    # Saudação: sempre modelo ultra-leve, nunca cai no llama3.1:8b nem no cache antigo.
    if cache_svc.eh_saudacao_simples(req.prompt):
        t = time.perf_counter()
        resposta = await cache_svc.responder_saudacao(req.prompt, bloco_ctx, tipo)
        timings["saudacao_s"] = round(time.perf_counter() - t, 2)
        categoria: Categoria = req.categoria or "chat"
        modelo = settings.model_light
        sessao_id = await _persistir_sessao(
            req, token_id, resposta, categoria, modelo, []
        )
        _extrair_contexto_em_background(token_id, req.prompt, tipo)
        timings["total_s"] = round(time.perf_counter() - t0, 2)
        logger.info("POST /chat saudação | token=%s tipo=%s | tempos=%s", token_id, tipo, timings)
        return ChatResponse(
            categoria=categoria,
            modelo=modelo,
            resposta=resposta,
            motivo_roteamento="Saudação respondida pelo modelo ultra-leve.",
            usar_web=False,
            motivo_web="Saudação: web não utilizada.",
            fontes=[],
            sessao_id=sessao_id,
            cache_hit=False,
            motivo_cache=f"Resposta curta via {settings.model_light}.",
            tipo_usuario=tipo,
        )

    # Consulta sobre dados pessoais deste token (nunca usa cache de outros).
    if ctx_svc.eh_consulta_pessoal(req.prompt):
        t = time.perf_counter()
        resposta_ctx = await ctx_svc.responder_com_contexto(token_id, req.prompt)
        timings["contexto_s"] = round(time.perf_counter() - t, 2)
        if ctx_svc.resposta_contexto_valida(resposta_ctx):
            categoria = req.categoria or "chat"
            modelo = settings.model_light
            sessao_id = await _persistir_sessao(
                req, token_id, resposta_ctx, categoria, modelo, []
            )
            _extrair_contexto_em_background(token_id, req.prompt, tipo)
            timings["total_s"] = round(time.perf_counter() - t0, 2)
            logger.info("POST /chat contexto pessoal | token=%s | tempos=%s", token_id, timings)
            return ChatResponse(
                categoria=categoria,
                modelo=modelo,
                resposta=resposta_ctx,
                motivo_roteamento="Resposta via contexto pessoal do token (modelo leve).",
                usar_web=False,
                motivo_web="Dados pessoais: sem web nem cache compartilhado.",
                fontes=[],
                sessao_id=sessao_id,
                cache_hit=False,
                motivo_cache="Contexto restrito ao token.",
                tipo_usuario=tipo,
            )

    # --- Cache semântico (por token; genéricas também usam SHARED) ---
    if await _pode_usar_cache(req):
        t = time.perf_counter()
        hit = await cache_svc.buscar(req.prompt, req.categoria, token_id=token_id)
        timings["cache_busca_s"] = round(time.perf_counter() - t, 2)
        if hit:
            resposta = hit.resposta
            if hit.similaridade < settings.cache_reformat_min or bloco_ctx:
                t = time.perf_counter()
                resposta = await cache_svc.adaptar_resposta_cache(
                    req.prompt, hit.resposta, bloco_ctx
                )
                timings["cache_reformat_s"] = round(time.perf_counter() - t, 2)
            categoria = _as_categoria(hit.categoria)
            modelo = hit.modelo
            sessao_id = await _persistir_sessao(
                req, token_id, resposta, categoria, modelo, hit.fontes
            )
            _extrair_contexto_em_background(token_id, req.prompt, tipo)
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
                tipo_usuario=tipo,
            )

    # Pergunta genérica sem cache: roteador 3b (evita llama3.1:8b / qwen 7b em CPU).
    if (
        req.usar_web is not True
        and cache_svc.eh_pergunta_generica(req.prompt)
        and req.categoria not in ("programacao", "imagem")
    ):
        cat_rapida: Categoria = req.categoria or "educacao"  # type: ignore[assignment]
        t = time.perf_counter()
        resposta, modelo = await cache_svc.responder_pergunta_generica(
            req.prompt, tipo, bloco_ctx, cat_rapida
        )
        timings["generico_rapido_s"] = round(time.perf_counter() - t, 2)
        sessao_id = await _persistir_sessao(
            req, token_id, resposta, cat_rapida, modelo, []
        )
        await cache_svc.salvar(
            req.prompt, resposta, cat_rapida, modelo, False, [], token_id=token_id
        )
        _extrair_contexto_em_background(token_id, req.prompt, tipo)
        timings["total_s"] = round(time.perf_counter() - t0, 2)
        logger.info(
            "POST /chat genérico rápido modelo=%s | tempos=%s", modelo, timings
        )
        return ChatResponse(
            categoria=cat_rapida,
            modelo=modelo,
            resposta=resposta,
            motivo_roteamento=f"Pergunta factual via {modelo} (atalho rápido).",
            usar_web=False,
            motivo_web="Conteúdo estável: web não utilizada.",
            fontes=[],
            sessao_id=sessao_id,
            cache_hit=False,
            motivo_cache="Primeira resposta; salva no cache compartilhado.",
            tipo_usuario=tipo,
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

    system = req.system or perfil.system_prompt(categoria, tipo)

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
        model=modelo,
        messages=messages,
        temperature=req.temperature,
        exclusivo=_modelo_pesado(modelo),
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

    _extrair_contexto_em_background(token_id, req.prompt, tipo)

    if await _pode_usar_cache(req) and not usar_web_efetivo:
        await cache_svc.salvar(
            req.prompt,
            resposta,
            categoria,
            modelo,
            usar_web_efetivo,
            fontes,
            token_id=token_id,
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
        tipo_usuario=tipo,
    )
