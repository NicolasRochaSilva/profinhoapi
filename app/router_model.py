"""Roteador de modelos.

Decide qual modelo Ollama usar para cada requisição:
  1. Se houver imagem -> imagem (qwen2.5vl).
  2. Tenta palavras-chave (rápido, sem custo de inferência).
  3. Se inconclusivo, usa o modelo roteador llama3.2:3b para classificar.
"""

from __future__ import annotations

import logging

from app.config import settings
from app.keywords import detectar_categoria
from app.ollama_client import ollama

logger = logging.getLogger("profinho.router")

CATEGORIAS_VALIDAS = {"chat", "programacao", "educacao", "imagem"}

_PROMPT_ROTEADOR = """Você é um classificador de intenções. Leia a mensagem do usuário e responda APENAS com uma única palavra, sem pontuação, escolhendo entre:

programacao  -> pedidos de código, sistemas, APIs, SQL, debug, html/css/js, dotnet/python
educacao     -> planos de aula, exercícios, provas, resumos, conteúdo didático, explicar matérias
imagem       -> análise/leitura de imagens, OCR, criar página a partir de imagem
chat         -> conversa geral, saudações, perguntas gerais, atendimento

Mensagem: "{mensagem}"

Responda só com uma palavra (programacao, educacao, imagem ou chat):"""


def _normalizar(resposta: str) -> str | None:
    txt = resposta.strip().lower()
    for cat in CATEGORIAS_VALIDAS:
        if cat in txt:
            return cat
    return None


async def rotear(texto: str, tem_imagem: bool = False) -> tuple[str, str, str]:
    """Retorna (categoria, modelo, motivo)."""
    # 1 + 2: heurística por palavras-chave / imagem
    categoria, placar = detectar_categoria(texto, tem_imagem=tem_imagem)
    if tem_imagem:
        return "imagem", settings.categories["imagem"], "Imagem detectada na requisição."
    if categoria and placar >= 2:
        return (
            categoria,
            settings.categories[categoria],
            f"Palavras-chave ({placar}) indicaram '{categoria}'.",
        )

    # 3: roteador via llama3.2:3b
    try:
        resposta = await ollama.generate(
            model=settings.model_router,
            prompt=_PROMPT_ROTEADOR.format(mensagem=texto[:2000]),
            temperature=0.0,
            options={"num_predict": 8},
        )
        cat_modelo = _normalizar(resposta)
        if cat_modelo:
            return (
                cat_modelo,
                settings.categories[cat_modelo],
                f"Roteador {settings.model_router} classificou como '{cat_modelo}'.",
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Roteador llama3.2 falhou (%s); usando fallback.", exc)

    # Fallback final
    cat_final = categoria or "chat"
    return (
        cat_final,
        settings.categories[cat_final],
        f"Fallback para '{cat_final}'.",
    )
