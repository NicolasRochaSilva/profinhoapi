"""Rota de chat com roteamento automático de modelo."""

from __future__ import annotations

import logging
import time
from typing import Optional

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.auth import require_token
from app.config import settings
from app.ollama_client import ollama
from app.router_model import decidir_usar_web, rotear
from app.schemas import Categoria, ChatRequest, ChatResponse, RouteResponse
from app.services import cache_respostas as cache_svc
from app.services import chat_common as common
from app.services import chat_stream
from app.services import contexto_usuario as ctx_svc
from app.services import crawl4ai, exercicio_interativo as exercicio_svc, memoria, moderacao, perfil_usuario as perfil, searxng

router = APIRouter(tags=["chat"])
logger = logging.getLogger("profinho.chat")

_CATEGORIAS = frozenset({"chat", "programacao", "educacao", "imagem"})

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _as_categoria(val: str) -> Categoria:
    if val in _CATEGORIAS:
        return val  # type: ignore[return-value]
    return "chat"


@router.post("/route", response_model=RouteResponse, summary="Apenas roteia (escolhe o modelo)")
async def route(req: ChatRequest, _=Depends(require_token)):
    categoria, modelo, motivo = await rotear(req.prompt)
    return RouteResponse(categoria=categoria, modelo=modelo, motivo=motivo)


@router.post(
    "/chat",
    summary="Chat com seleção automática de modelo (JSON ou SSE se stream=true)",
)
async def chat(req: ChatRequest, token=Depends(require_token)):
    if req.stream:
        return StreamingResponse(
            chat_stream.eventos_chat(req, token),
            media_type="text/event-stream",
            headers=_SSE_HEADERS,
        )
    return await _chat_json(req, token)


async def _chat_json(req: ChatRequest, token: dict) -> ChatResponse:
    t0 = time.perf_counter()
    timings: dict[str, float | int] = {}
    token_id = token.get("id")
    tipo = common.tipo_usuario(token)

    bloqueio = moderacao.detectar_tema_bloqueado(req.prompt)
    if bloqueio:
        resposta = moderacao.resposta_bloqueio(bloqueio, tipo)
        sessao_id = await common.persistir_sessao(
            req, token_id, resposta, req.categoria or "chat", settings.model_light, [],
            cache_turno=False,
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

    if cache_svc.eh_saudacao_simples(req.prompt):
        t = time.perf_counter()
        resposta = await cache_svc.responder_saudacao(req.prompt, bloco_ctx, tipo)
        timings["saudacao_s"] = round(time.perf_counter() - t, 2)
        categoria: Categoria = req.categoria or "chat"
        modelo = settings.model_light
        sessao_id = await common.persistir_sessao(
            req, token_id, resposta, categoria, modelo, []
        )
        common.extrair_contexto_em_background(token_id, req.prompt, tipo)
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

    if perfil.eh_pergunta_identidade(req.prompt):
        t = time.perf_counter()
        resposta, modelo = await cache_svc.responder_identidade(req.prompt, tipo)
        timings["identidade_s"] = round(time.perf_counter() - t, 2)
        categoria = req.categoria or "chat"  # type: ignore[assignment]
        sessao_id = await common.persistir_sessao(
            req, token_id, resposta, categoria, modelo, []
        )
        common.extrair_contexto_em_background(token_id, req.prompt, tipo)
        timings["total_s"] = round(time.perf_counter() - t0, 2)
        logger.info("POST /chat identidade | token=%s | tempos=%s", token_id, timings)
        return ChatResponse(
            categoria=categoria,
            modelo=modelo,
            resposta=resposta,
            motivo_roteamento="Apresentação do Profinho (quem é você).",
            usar_web=False,
            motivo_web="Pergunta de identidade; sem web.",
            fontes=[],
            sessao_id=sessao_id,
            cache_hit=False,
            motivo_cache=None,
            tipo_usuario=tipo,
        )

    if perfil.eh_piada_generica(req.prompt):
        t = time.perf_counter()
        resposta, modelo = await cache_svc.responder_piada_generica(req.prompt, tipo)
        timings["piada_generica_s"] = round(time.perf_counter() - t, 2)
        categoria = req.categoria or "chat"  # type: ignore[assignment]
        sessao_id = await common.persistir_sessao(
            req, token_id, resposta, categoria, modelo, []
        )
        common.extrair_contexto_em_background(token_id, req.prompt, tipo)
        timings["total_s"] = round(time.perf_counter() - t0, 2)
        logger.info("POST /chat piada genérica | token=%s | tempos=%s", token_id, timings)
        return ChatResponse(
            categoria=categoria,
            modelo=modelo,
            resposta=resposta,
            motivo_roteamento="Piada inocente livre via Profinho (modelo leve).",
            usar_web=False,
            motivo_web="Humor leve; sem busca na web.",
            fontes=[],
            sessao_id=sessao_id,
            cache_hit=False,
            motivo_cache=None,
            tipo_usuario=tipo,
        )

    if perfil.eh_pedido_piada_conteudo(req.prompt):
        t = time.perf_counter()
        resposta, modelo = await cache_svc.responder_piada_conteudo(req.prompt, tipo)
        timings["piada_tema_s"] = round(time.perf_counter() - t, 2)
        categoria = req.categoria or "educacao"  # type: ignore[assignment]
        sessao_id = await common.persistir_sessao(
            req, token_id, resposta, categoria, modelo, []
        )
        common.extrair_contexto_em_background(token_id, req.prompt, tipo)
        timings["total_s"] = round(time.perf_counter() - t0, 2)
        logger.info("POST /chat piada sobre tema | token=%s | tempos=%s", token_id, timings)
        return ChatResponse(
            categoria=categoria,
            modelo=modelo,
            resposta=resposta,
            motivo_roteamento="Piadas inocentes sobre o tema pedido (Profinho).",
            usar_web=False,
            motivo_web="Humor sobre conteúdo; sem web.",
            fontes=[],
            sessao_id=sessao_id,
            cache_hit=False,
            motivo_cache=None,
            tipo_usuario=tipo,
        )

    if ctx_svc.eh_consulta_pessoal(req.prompt):
        t = time.perf_counter()
        resposta_ctx = await ctx_svc.responder_com_contexto(token_id, req.prompt)
        timings["contexto_s"] = round(time.perf_counter() - t, 2)
        if ctx_svc.resposta_contexto_valida(resposta_ctx):
            categoria = req.categoria or "chat"
            modelo = settings.model_light
            sessao_id = await common.persistir_sessao(
                req, token_id, resposta_ctx, categoria, modelo, []
            )
            common.extrair_contexto_em_background(token_id, req.prompt, tipo)
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

    if await common.pode_usar_cache(req):
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
            sessao_id = await common.persistir_sessao(
                req, token_id, resposta, categoria, modelo, hit.fontes, cache_turno=True
            )
            common.extrair_contexto_em_background(token_id, req.prompt, tipo)
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

    if (
        req.categoria not in ("programacao", "imagem")
        and exercicio_svc.eh_pedido_exercicio_interativo(req.prompt)
    ):
        cat_web: Categoria = "educacao"  # type: ignore[assignment]
        t = time.perf_counter()
        (
            resposta,
            modelo,
            motivo_web_pipe,
            usar_web_efetivo,
            motivo_web,
            fontes,
        ) = await exercicio_svc.gerar_exercicio_interativo(
            req.prompt, tipo, bloco_ctx, req.usar_web
        )
        timings["exercicio_web_s"] = round(time.perf_counter() - t, 2)
        resposta_fmt = f"```html\n{resposta}\n```"
        sessao_id = await common.persistir_sessao(
            req, token_id, resposta_fmt, cat_web, modelo, fontes
        )
        common.extrair_contexto_em_background(token_id, req.prompt, tipo)
        timings["total_s"] = round(time.perf_counter() - t0, 2)
        logger.info(
            "POST /chat exercício interativo modelo=%s web=%s | tempos=%s",
            modelo,
            usar_web_efetivo,
            timings,
        )
        return ChatResponse(
            categoria=cat_web,
            modelo=modelo,
            resposta=resposta_fmt,
            motivo_roteamento=motivo_web_pipe,
            usar_web=usar_web_efetivo,
            motivo_web=motivo_web,
            fontes=fontes,
            sessao_id=sessao_id,
            cache_hit=False,
            motivo_cache=None,
            tipo_usuario=tipo,
        )

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
        sessao_id = await common.persistir_sessao(
            req, token_id, resposta, cat_rapida, modelo, []
        )
        await cache_svc.salvar(
            req.prompt, resposta, cat_rapida, modelo, False, [], token_id=token_id
        )
        common.extrair_contexto_em_background(token_id, req.prompt, tipo)
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

    system = req.system or perfil.system_prompt(categoria, tipo, req.prompt)

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

    user_content = perfil.enriquecer_prompt_usuario(
        req.prompt,
        f"[Informações atualizadas da web]\n{contexto_web}" if contexto_web else "",
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
        options=common.opcoes_resposta_chat(categoria, req.prompt),
        exclusivo=common.modelo_pesado(modelo),
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

    common.extrair_contexto_em_background(token_id, req.prompt, tipo)

    if await common.pode_usar_cache(req) and not usar_web_efetivo:
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
