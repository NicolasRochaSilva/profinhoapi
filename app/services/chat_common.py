"""Funções compartilhadas entre chat síncrono e streaming."""

from __future__ import annotations

import asyncio
from typing import Optional

from app.config import settings
from app.schemas import ChatRequest
from app.services import cache_respostas as cache_svc
from app.services import contexto_usuario as ctx_svc
from app.services import memoria, perfil_usuario as perfil


async def pode_usar_cache(req: ChatRequest) -> bool:
    if not settings.cache_enabled:
        return False
    if perfil.eh_pedido_piada(req.prompt):
        return False
    if perfil.eh_pergunta_identidade(req.prompt):
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


async def persistir_sessao(
    req: ChatRequest,
    token_id: Optional[str],
    resposta: str,
    categoria: str,
    modelo: str,
    fontes: list[str],
    *,
    cache_turno: bool = True,
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
        meta: dict | None = None
        if cache_turno:
            meta = {"fontes": fontes, "cache": True} if fontes else {"cache": True}
        elif fontes:
            meta = {"fontes": fontes}
        await memoria.registrar_turno(
            sessao_id=sessao_id,
            prompt_usuario=req.prompt,
            resposta=resposta,
            modelo=modelo,
            categoria=categoria,
            metadados_assistente=meta,
        )
    return sessao_id


def tipo_usuario(token: dict) -> str:
    return perfil.normalizar_tipo(token.get("tipo_usuario"))


def modelo_pesado(modelo: str) -> bool:
    return modelo in settings.modelos_trabalho


def extrair_contexto_em_background(
    token_id: Optional[str], prompt: str, tipo_usuario: str
) -> None:
    if not token_id:
        return
    asyncio.create_task(ctx_svc.extrair_e_salvar(token_id, prompt, tipo_usuario=tipo_usuario))


def opcoes_resposta_chat(categoria: str, prompt: str = "") -> dict:
    """Limites de geração: respostas curtas; um pouco mais para piadas pedidas."""
    if categoria == "programacao":
        return {"num_predict": settings.chat_num_predict}
    if perfil.eh_pedido_piada(prompt):
        return {"num_predict": settings.chat_num_predict_piada}
    return {"num_predict": settings.chat_num_predict_curto}
