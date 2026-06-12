"""Endpoints compatíveis com a API da OpenAI.

Permitem usar a Profinho API direto no VS Code (extensões como Continue,
Cody, ou qualquer cliente "OpenAI compatible") para gerar código igual ao Cursor.

Base URL no VS Code:  http://SEU_IP:7000/v1
Chave/API key:        o token da tabela `tokens`.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.auth import require_token
from app.config import settings
from app.ollama_client import ollama
from app.router_model import rotear
from app.schemas import OpenAIChatRequest
from app.services import chat_common as common
from app.services import moderacao, perfil_usuario as perfil

router = APIRouter(tags=["openai-compat"])

# Mapa de "modelos" expostos para o cliente OpenAI (IDs de roteamento; identidade sempre Profinho).
_MODELOS_EXPOSTOS = {
    "profinho": None,  # roteamento automático (recomendado)
    "profinho-auto": None,  # alias legado
    "profinho-chat": settings.model_chat,
    "profinho-coder": settings.model_code,
    "profinho-edu": settings.model_edu,
    "profinho-vision": settings.model_vision,
}


def _extrair_texto(content: Any) -> str:
    """messages[].content pode ser string ou lista (formato OpenAI multimodal)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        partes = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                partes.append(item.get("text", ""))
        return "\n".join(partes)
    return str(content)


def _categoria_openai(model_name: str) -> str:
    m = (model_name or "").lower()
    if "coder" in m:
        return "programacao"
    if "edu" in m:
        return "educacao"
    if "vision" in m:
        return "imagem"
    return "chat"


def _enriquecer_ultimo_usuario(messages: list[dict[str, str]]) -> None:
    for i in range(len(messages) - 1, -1, -1):
        if messages[i]["role"] == "user":
            messages[i]["content"] = perfil.enriquecer_prompt_usuario(messages[i]["content"])
            return


@router.get("/v1/models", summary="Listar modelos (OpenAI-compatible)")
async def list_models(_=Depends(require_token)):
    data = [
        {"id": nome, "object": "model", "created": int(time.time()), "owned_by": "profinho"}
        for nome in _MODELOS_EXPOSTOS
    ]
    return {"object": "list", "data": data}


async def _resolver_modelo(req: OpenAIChatRequest) -> str:
    nome = (req.model or "profinho").strip()
    if nome in _MODELOS_EXPOSTOS and _MODELOS_EXPOSTOS[nome]:
        return _MODELOS_EXPOSTOS[nome]
    if nome in ("profinho", "profinho-auto") or nome not in _MODELOS_EXPOSTOS:
        # roteia pela última mensagem do usuário
        ultima = ""
        for m in reversed(req.messages):
            if m.role == "user":
                ultima = _extrair_texto(m.content)
                break
        _, modelo, _motivo = await rotear(ultima)
        return modelo
    return settings.model_chat


@router.post("/v1/chat/completions", summary="Chat completions (OpenAI-compatible)")
async def chat_completions(req: OpenAIChatRequest, token=Depends(require_token)):
    tipo = perfil.normalizar_tipo(token.get("tipo_usuario"))
    ultima = ""
    for m in reversed(req.messages):
        if m.role == "user":
            ultima = _extrair_texto(m.content)
            break

    bloqueio = moderacao.detectar_tema_bloqueado(ultima)
    if bloqueio:
        texto = moderacao.resposta_bloqueio(bloqueio, tipo)
        modelo = settings.model_light
        completion_id = f"chatcmpl-{uuid.uuid4().hex}"
        criado = int(time.time())
        return {
            "id": completion_id,
            "object": "chat.completion",
            "created": criado,
            "model": modelo,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": texto},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

    modelo = await _resolver_modelo(req)
    messages = [{"role": m.role, "content": _extrair_texto(m.content)} for m in req.messages]
    cat = _categoria_openai(req.model or "")
    ultima_user = ""
    for m in reversed(req.messages):
        if m.role == "user":
            ultima_user = _extrair_texto(m.content)
            break
    if messages and messages[0]["role"] != "system":
        messages.insert(
            0,
            {"role": "system", "content": perfil.system_prompt(cat, tipo, ultima_user)},
        )
    elif messages and messages[0]["role"] == "system" and tipo == "aluno":
        messages[0]["content"] = (
            perfil.system_prompt("chat", tipo, ultima_user)
            + "\n\n"
            + messages[0]["content"]
        )
    _enriquecer_ultimo_usuario(messages)
    opts = common.opcoes_resposta_chat(cat, ultima_user)
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    criado = int(time.time())

    if req.stream:
        async def gerar():
            enviou_role = False
            async for pedaco in ollama.chat_stream(
                model=modelo,
                messages=messages,
                temperature=req.temperature,
                options=opts,
                exclusivo=True,
            ):
                delta: dict[str, str] = {"content": pedaco}
                if not enviou_role:
                    delta["role"] = "assistant"
                    enviou_role = True
                chunk = {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "created": criado,
                    "model": modelo,
                    "choices": [
                        {"index": 0, "delta": delta, "finish_reason": None}
                    ],
                }
                yield f"data: {json.dumps(chunk)}\n\n"
            final = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": criado,
                "model": modelo,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
            yield f"data: {json.dumps(final)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            gerar(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    texto = await ollama.chat(
        model=modelo,
        messages=messages,
        temperature=req.temperature,
        options=opts,
        exclusivo=True,
    )
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": criado,
        "model": modelo,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": texto},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }
