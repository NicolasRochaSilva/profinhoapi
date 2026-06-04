"""Rotas de status/saúde e administração do Ollama (RAM/modelos)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.auth import require_token
from app.config import settings
from app.database import get_pool
from app.ollama_client import ollama

router = APIRouter(tags=["health"])


@router.get("/health", summary="Healthcheck simples")
async def health():
    return {"status": "ok", "servico": settings.api_title}


@router.get("/status", summary="Status detalhado das dependências")
async def status():
    ollama_ok = await ollama.health()
    modelos = await ollama.list_models() if ollama_ok else []
    carregados = await ollama.modelos_carregados() if ollama_ok else []
    return {
        "api": "ok",
        "ambiente": settings.environment,
        "ollama": {
            "online": ollama_ok,
            "base_url": settings.ollama_base_url,
            "keep_alive": settings.ollama_keep_alive,
            "quentes_keep_alive": settings.ollama_quentes_keep_alive,
            "modelos_quentes": list(settings.modelos_quentes),
            "preload_quentes": settings.ollama_preload_quentes,
            "modelo_unico": settings.ollama_modelo_unico,
            "modelos_instalados": modelos,
            "modelos_na_ram": [
                {"nome": m.get("name"), "tamanho_vram": m.get("size_vram"), "size": m.get("size")}
                for m in carregados
            ],
            "modelos_configurados": {
                "roteador": settings.model_router,
                "chat": settings.model_chat,
                "programacao": settings.model_code,
                "educacao": settings.model_edu,
                "imagem": settings.model_vision,
            },
        },
        "postgres": {"online": get_pool() is not None, "host": settings.postgres_host},
    }


@router.get("/ollama/loaded", summary="Modelos atualmente carregados na RAM")
async def ollama_loaded(_=Depends(require_token)):
    return {"modelos": await ollama.modelos_carregados()}


@router.post("/ollama/preload", summary="Pré-aquecer os 4 modelos quentes na RAM")
async def ollama_preload(_=Depends(require_token)):
    """Carrega na RAM: llama3.2:3b, llama3.1:8b, qwen2.5-coder:7b e qwen2.5:7b.

    Pode levar vários minutos na primeira vez. Usa `OLLAMA_QUENTES_KEEP_ALIVE` (padrão -1).
    """
    if not await ollama.health():
        raise HTTPException(status_code=503, detail="Ollama indisponível.")
    return await ollama.preload_modelos_quentes(forcar=True)


@router.post("/ollama/unload", summary="Descarregar modelo(s) da RAM")
async def ollama_unload(model: str | None = None, _=Depends(require_token)):
    """Descarrega um modelo específico (?model=...) ou todos (sem parâmetro)."""
    if not await ollama.health():
        raise HTTPException(status_code=503, detail="Ollama indisponível.")
    if model:
        ok = await ollama.descarregar(model)
        return {"descarregados": [model] if ok else [], "ok": ok}
    descarregados = await ollama.descarregar_todos()
    return {"descarregados": descarregados, "ok": True}
