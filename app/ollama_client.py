"""Cliente HTTP para o Ollama rodando no HOST da VPS (fora do Docker).

Gerencia também o uso de RAM:
  - `keep_alive` controla quanto tempo o modelo fica na memória após o uso;
  - quando `ollama_modelo_unico` está ativo, os modelos pesados que não estão
    sendo usados são descarregados antes de carregar o próximo (1 por vez).
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Union

import httpx
from tenacity import RetryError, retry, stop_after_attempt, wait_exponential

from app.config import settings

logger = logging.getLogger("profinho.ollama")


class OllamaClient:
    def __init__(self, base_url: Optional[str] = None, timeout: Optional[int] = None):
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self.timeout = timeout or settings.ollama_timeout
        self.keep_alive = settings.ollama_keep_alive

    def _resolve_keep_alive(self, model: str, keep_alive: Optional[str]) -> str:
        if keep_alive is not None:
            return keep_alive
        if model in settings.modelos_quentes:
            return settings.ollama_quentes_keep_alive
        return self.keep_alive

    @staticmethod
    def _format_keep_alive(value: str) -> Union[str, int]:
        """Ollama aceita '5m' ou segundos inteiros; -1 (int) = forever (não string '-1')."""
        v = value.strip()
        if v == "-1":
            return -1
        if v == "0":
            return 0
        if v.isdigit():
            return int(v)
        return v

    @staticmethod
    def _formatar_erro_ollama(exc: Exception) -> str:
        causa = exc
        if isinstance(exc, RetryError) and exc.last_attempt.failed:
            causa = exc.last_attempt.exception() or exc
        if isinstance(causa, httpx.HTTPStatusError):
            corpo = (causa.response.text or "")[:400]
            return f"HTTP {causa.response.status_code}: {corpo or causa.response.reason_phrase}"
        return str(exc)

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(min=1, max=4))
    async def generate(
        self,
        model: str,
        prompt: str,
        system: Optional[str] = None,
        images: Optional[list[str]] = None,
        temperature: float = 0.7,
        options: Optional[dict[str, Any]] = None,
        exclusivo: bool = False,
        keep_alive: Optional[str] = None,
    ) -> str:
        """Geração simples (endpoint /api/generate). `images` = lista base64 (sem prefixo).

        `exclusivo=True` descarrega os outros modelos pesados antes de rodar (economia de RAM).
        """
        if exclusivo:
            await self.garantir_unico(model)

        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "keep_alive": self._format_keep_alive(self._resolve_keep_alive(model, keep_alive)),
            "options": {"temperature": temperature, **(options or {})},
        }
        if system:
            payload["system"] = system
        if images:
            payload["images"] = images

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(f"{self.base_url}/api/generate", json=payload)
            resp.raise_for_status()
            data = resp.json()
        return data.get("response", "")

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(min=1, max=4))
    async def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float = 0.7,
        options: Optional[dict[str, Any]] = None,
        exclusivo: bool = False,
        keep_alive: Optional[str] = None,
    ) -> str:
        """Chat (endpoint /api/chat). messages = [{role, content, images?}]."""
        if exclusivo:
            await self.garantir_unico(model)

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "keep_alive": self._format_keep_alive(self._resolve_keep_alive(model, keep_alive)),
            "options": {"temperature": temperature, **(options or {})},
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(f"{self.base_url}/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()
        return data.get("message", {}).get("content", "")

    async def list_models(self) -> list[str]:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{self.base_url}/api/tags")
                resp.raise_for_status()
                data = resp.json()
            return [m.get("name", "") for m in data.get("models", [])]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Não foi possível listar modelos do Ollama: %s", exc)
            return []

    async def modelos_carregados(self) -> list[dict[str, Any]]:
        """Lista os modelos atualmente carregados na RAM (endpoint /api/ps)."""
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{self.base_url}/api/ps")
                resp.raise_for_status()
                data = resp.json()
            return data.get("models", [])
        except Exception as exc:  # noqa: BLE001
            logger.warning("Não foi possível consultar /api/ps: %s", exc)
            return []

    async def preload_modelos_quentes(self, *, forcar: bool = False) -> dict[str, Any]:
        """Carrega roteador, chat, coder e edu na RAM. Retorna relatório por modelo."""
        if not forcar and not settings.ollama_preload_quentes:
            return {
                "ok": False,
                "ignorado": True,
                "motivo": "OLLAMA_PRELOAD_QUENTES=false (use forcar via POST /ollama/preload).",
                "modelos": [],
            }
        if not await self.health():
            logger.warning("Ollama offline; pré-carga de modelos quentes ignorada.")
            return {"ok": False, "erro": "Ollama indisponível.", "modelos": []}

        ordem = [
            settings.model_light,
            settings.model_router,
            settings.model_chat,
            settings.model_code,
            settings.model_edu,
        ]
        resultados: list[dict[str, Any]] = []
        for model in ordem:
            try:
                logger.info("Pré-carregando modelo quente na RAM: %s", model)
                await self.generate(
                    model=model,
                    prompt="",
                    temperature=0.0,
                    options={"num_predict": 1},
                    keep_alive=settings.ollama_quentes_keep_alive,
                )
                resultados.append({"modelo": model, "ok": True})
            except Exception as exc:  # noqa: BLE001
                msg = self._formatar_erro_ollama(exc)
                logger.warning("Falha ao pré-carregar %s: %s", model, msg)
                resultados.append({"modelo": model, "ok": False, "erro": msg})

        carregados = await self.modelos_carregados()
        nomes_ram = [m.get("name") for m in carregados if m.get("name")]
        return {
            "ok": all(r["ok"] for r in resultados),
            "keep_alive": settings.ollama_quentes_keep_alive,
            "ollama_base_url": self.base_url,
            "aquecidos": resultados,
            "modelos_na_ram": nomes_ram,
        }

    async def descarregar(self, model: str) -> bool:
        """Descarrega um modelo da RAM imediatamente (keep_alive = 0)."""
        if model in settings.modelos_quentes:
            logger.info("Modelo quente protegido, não descarregado: %s", model)
            return False
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{self.base_url}/api/generate",
                    json={"model": model, "keep_alive": 0},
                )
                resp.raise_for_status()
            logger.info("Modelo descarregado da RAM: %s", model)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Falha ao descarregar %s: %s", model, exc)
            return False

    async def descarregar_todos(self) -> list[str]:
        """Descarrega todos os modelos carregados. Retorna os nomes descarregados."""
        carregados = await self.modelos_carregados()
        nomes = [m.get("name", "") for m in carregados if m.get("name")]
        for nome in nomes:
            await self.descarregar(nome)
        return nomes

    async def garantir_unico(self, model: str) -> None:
        """Se o modo modelo-único estiver ativo, descarrega os outros modelos
        pesados carregados, mantendo apenas o `model` que será usado agora."""
        if not settings.ollama_modelo_unico:
            return
        carregados = await self.modelos_carregados()
        alvos = set(settings.modelos_trabalho)
        for m in carregados:
            nome = m.get("name", "")
            # só mexe nos modelos de trabalho (não derruba o roteador 3b à toa)
            if (
                nome
                and nome != model
                and nome in alvos
                and nome not in settings.modelos_quentes
            ):
                await self.descarregar(nome)

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{self.base_url}/api/version")
                return resp.status_code == 200
        except Exception:  # noqa: BLE001
            return False


ollama = OllamaClient()
