"""Streaming SSE para POST /chat."""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from typing import Any, Optional

from app.config import settings
from app.ollama_client import ollama
from app.router_model import decidir_usar_web, rotear
from app.schemas import Categoria, ChatRequest
from app.services import cache_respostas as cache_svc
from app.services import chat_common as common
from app.services import contexto_usuario as ctx_svc
from app.services import crawl4ai, memoria, moderacao, perfil_usuario as perfil, searxng
from app.services.sse import iter_texto_simulado, sse_event

logger = logging.getLogger("profinho.chat.stream")

_CATEGORIAS = frozenset({"chat", "programacao", "educacao", "imagem"})


def _as_categoria(val: str) -> Categoria:
    if val in _CATEGORIAS:
        return val  # type: ignore[return-value]
    return "chat"


def _meta_base(tipo: str, **extra: Any) -> dict[str, Any]:
    return {"tipo_usuario": tipo, **extra}


async def _emitir_tokens(meta: dict[str, Any], texto: str) -> AsyncIterator[str]:
    yield sse_event("meta", meta)
    async for pedaco in iter_texto_simulado(texto):
        yield sse_event("token", {"content": pedaco})


async def eventos_chat(req: ChatRequest, token: dict) -> AsyncIterator[str]:
    t0 = time.perf_counter()
    token_id = token.get("id")
    tipo = common.tipo_usuario(token)

    bloqueio = moderacao.detectar_tema_bloqueado(req.prompt)
    if bloqueio:
        resposta = moderacao.resposta_bloqueio(bloqueio, tipo)
        meta = _meta_base(
            tipo,
            categoria=req.categoria or "chat",
            modelo=settings.model_light,
            motivo_roteamento="Conteúdo bloqueado pela moderação.",
            usar_web=False,
            motivo_web="Tema sensível não permitido.",
            fontes=[],
            cache_hit=False,
            motivo_cache=None,
            conteudo_bloqueado=True,
            motivo_bloqueio=bloqueio.motivo,
        )
        async for ev in _emitir_tokens(meta, resposta):
            yield ev
        sessao_id = await common.persistir_sessao(
            req, token_id, resposta, meta["categoria"], meta["modelo"], [], cache_turno=False
        )
        yield sse_event("done", {**meta, "resposta": resposta, "sessao_id": sessao_id})
        return

    bloco_ctx = await ctx_svc.formatar_bloco(token_id, compacto=True)

    if cache_svc.eh_saudacao_simples(req.prompt):
        categoria: Categoria = req.categoria or "chat"
        modelo = settings.model_light
        meta = _meta_base(
            tipo,
            categoria=categoria,
            modelo=modelo,
            motivo_roteamento="Saudação respondida pelo modelo ultra-leve.",
            usar_web=False,
            motivo_web="Saudação: web não utilizada.",
            fontes=[],
            cache_hit=False,
            motivo_cache=f"Resposta curta via {settings.model_light}.",
        )
        partes: list[str] = []
        yield sse_event("meta", meta)
        async for pedaco in cache_svc.iter_saudacao(req.prompt, bloco_ctx, tipo):
            partes.append(pedaco)
            yield sse_event("token", {"content": pedaco})
        resposta = "".join(partes)
        sessao_id = await common.persistir_sessao(
            req, token_id, resposta, categoria, modelo, []
        )
        common.extrair_contexto_em_background(token_id, req.prompt, tipo)
        yield sse_event("done", {**meta, "resposta": resposta, "sessao_id": sessao_id})
        logger.info("POST /chat stream saudação | %.2fs", time.perf_counter() - t0)
        return

    if ctx_svc.eh_consulta_pessoal(req.prompt):
        resposta_ctx = await ctx_svc.responder_com_contexto(token_id, req.prompt)
        if ctx_svc.resposta_contexto_valida(resposta_ctx):
            categoria = req.categoria or "chat"
            modelo = settings.model_light
            meta = _meta_base(
                tipo,
                categoria=categoria,
                modelo=modelo,
                motivo_roteamento="Resposta via contexto pessoal do token (modelo leve).",
                usar_web=False,
                motivo_web="Dados pessoais: sem web nem cache compartilhado.",
                fontes=[],
                cache_hit=False,
                motivo_cache="Contexto restrito ao token.",
            )
            async for ev in _emitir_tokens(meta, resposta_ctx):
                yield ev
            sessao_id = await common.persistir_sessao(
                req, token_id, resposta_ctx, categoria, modelo, []
            )
            common.extrair_contexto_em_background(token_id, req.prompt, tipo)
            yield sse_event(
                "done", {**meta, "resposta": resposta_ctx, "sessao_id": sessao_id}
            )
            return

    if await common.pode_usar_cache(req):
        hit = await cache_svc.buscar(req.prompt, req.categoria, token_id=token_id)
        if hit:
            resposta = hit.resposta
            if hit.similaridade < settings.cache_reformat_min or bloco_ctx:
                resposta = await cache_svc.adaptar_resposta_cache(
                    req.prompt, hit.resposta, bloco_ctx
                )
            categoria = _as_categoria(hit.categoria)
            modelo = hit.modelo
            meta = _meta_base(
                tipo,
                categoria=categoria,
                modelo=modelo,
                motivo_roteamento=hit.motivo,
                usar_web=False,
                motivo_web="Cache: web não reexecutada.",
                fontes=hit.fontes,
                cache_hit=True,
                motivo_cache=hit.motivo,
            )
            async for ev in _emitir_tokens(meta, resposta):
                yield ev
            sessao_id = await common.persistir_sessao(
                req, token_id, resposta, categoria, modelo, hit.fontes, cache_turno=True
            )
            common.extrair_contexto_em_background(token_id, req.prompt, tipo)
            yield sse_event("done", {**meta, "resposta": resposta, "sessao_id": sessao_id})
            return

    if (
        req.usar_web is not True
        and cache_svc.eh_pergunta_generica(req.prompt)
        and req.categoria not in ("programacao", "imagem")
    ):
        cat_rapida: Categoria = req.categoria or "educacao"  # type: ignore[assignment]
        modelo = settings.model_router
        meta = _meta_base(
            tipo,
            categoria=cat_rapida,
            modelo=modelo,
            motivo_roteamento=f"Pergunta factual via {modelo} (atalho rápido).",
            usar_web=False,
            motivo_web="Conteúdo estável: web não utilizada.",
            fontes=[],
            cache_hit=False,
            motivo_cache="Primeira resposta; salva no cache compartilhado.",
        )
        partes = []
        yield sse_event("meta", meta)
        async for pedaco in cache_svc.iter_pergunta_generica(
            req.prompt, tipo, bloco_ctx, cat_rapida
        ):
            partes.append(pedaco)
            yield sse_event("token", {"content": pedaco})
        resposta = "".join(partes)
        sessao_id = await common.persistir_sessao(
            req, token_id, resposta, cat_rapida, modelo, []
        )
        await cache_svc.salvar(
            req.prompt, resposta, cat_rapida, modelo, False, [], token_id=token_id
        )
        common.extrair_contexto_em_background(token_id, req.prompt, tipo)
        yield sse_event("done", {**meta, "resposta": resposta, "sessao_id": sessao_id})
        return

    # --- Fluxo normal ---
    if req.categoria:
        categoria = req.categoria
        modelo = settings.categories[categoria]
        motivo = "Categoria informada pelo cliente."
    else:
        categoria, modelo, motivo = await rotear(req.prompt)

    if req.usar_web is None:
        usar_web_efetivo, motivo_web = await decidir_usar_web(req.prompt)
    elif req.usar_web:
        usar_web_efetivo, motivo_web = True, "Busca web forçada pelo cliente."
    else:
        usar_web_efetivo, motivo_web = False, "Busca web desativada pelo cliente."

    fontes: list[str] = []
    contexto_web = ""
    if usar_web_efetivo:
        resultados = await searxng.buscar(req.prompt, max_resultados=4)
        urls = [r["url"] for r in resultados if r.get("url")]
        conteudos = await crawl4ai.ler_varias(urls, max_chars_total=12000)
        fontes = list(conteudos.keys())
        if conteudos:
            contexto_web = "\n\n".join(
                f"Fonte: {u}\n{c[:4000]}" for u, c in conteudos.items()
            )

    system = req.system or perfil.system_prompt(categoria, tipo)
    sessao_id: Optional[str] = None
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

    historico_extra = [{"role": m.role, "content": m.content} for m in req.historico]
    messages = await memoria.montar_contexto(
        sessao_id=sessao_id,
        token_id=token_id,
        system=system,
        prompt_usuario=user_content,
        historico_extra=historico_extra,
    )

    meta = _meta_base(
        tipo,
        categoria=categoria,
        modelo=modelo,
        motivo_roteamento=motivo,
        usar_web=usar_web_efetivo,
        motivo_web=motivo_web,
        fontes=fontes,
        cache_hit=False,
        motivo_cache=None,
        sessao_id=sessao_id,
    )

    partes = []
    yield sse_event("meta", meta)
    async for pedaco in ollama.chat_stream(
        model=modelo,
        messages=messages,
        temperature=req.temperature,
        exclusivo=common.modelo_pesado(modelo),
    ):
        partes.append(pedaco)
        yield sse_event("token", {"content": pedaco})
    resposta = "".join(partes)

    if req.salvar and sessao_id:
        await memoria.registrar_turno(
            sessao_id=sessao_id,
            prompt_usuario=req.prompt,
            resposta=resposta,
            modelo=modelo,
            categoria=categoria,
            metadados_assistente={"fontes": fontes} if fontes else None,
        )

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

    logger.info(
        "POST /chat stream modelo=%s categoria=%s | %.2fs",
        modelo,
        categoria,
        time.perf_counter() - t0,
    )
    yield sse_event(
        "done",
        {
            **meta,
            "resposta": resposta,
            "sessao_id": sessao_id,
        },
    )
