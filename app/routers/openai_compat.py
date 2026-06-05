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
from app.services import moderacao, perfil_usuario as perfil

router = APIRouter(tags=["openai-compat"])

# Mapa de "modelos" expostos para o cliente OpenAI.
_MODELOS_EXPOSTOS = {
    "profinho-auto": None,  # roteamento automático
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


@router.get("/v1/models", summary="Listar modelos (OpenAI-compatible)")
async def list_models(_=Depends(require_token)):
    data = [
        {"id": nome, "object": "model", "created": int(time.time()), "owned_by": "profinho"}
        for nome in _MODELOS_EXPOSTOS
    ]
    return {"object": "list", "data": data}


async def _resolver_modelo(req: OpenAIChatRequest) -> str:
    nome = (req.model or "profinho-auto").strip()
    if nome in _MODELOS_EXPOSTOS and _MODELOS_EXPOSTOS[nome]:
        return _MODELOS_EXPOSTOS[nome]
    if nome == "profinho-auto" or nome not in _MODELOS_EXPOSTOS:
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
    if messages and messages[0]["role"] != "system":
        cat = "programacao" if "coder" in (req.model or "") else "chat"
        messages.insert(
            0,
            {"role": "system", "content": perfil.system_prompt(cat, tipo)},
        )
    elif messages and messages[0]["role"] == "system" and tipo == "aluno":
        messages[0]["content"] = (
            perfil.system_prompt("chat", tipo) + "\n\n" + messages[0]["content"]
        )
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    criado = int(time.time())

    if req.stream:
        async def gerar():
            texto = await ollama.chat(
                model=modelo, messages=messages, temperature=req.temperature, exclusivo=True
            )
            chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": criado,
                "model": modelo,
                "choices": [
                    {"index": 0, "delta": {"role": "assistant", "content": texto}, "finish_reason": None}
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

        return StreamingResponse(gerar(), media_type="text/event-stream")

    texto = await ollama.chat(
        model=modelo, messages=messages, temperature=req.temperature, exclusivo=True
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
