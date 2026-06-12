"""Contexto persistente por token, classificado pelo modelo leve.

Informações pessoais ficam restritas ao token (nunca entram no cache SHARED).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from app import database as db
from app.config import settings
from app.ollama_client import ollama

logger = logging.getLogger("profinho.contexto")

TIPOS_VALIDOS = frozenset({"pessoal", "preferencia", "contexto", "outro"})

_CONSULTA_PESSOAL = (
    "meu nome",
    "minha turma",
    "minha escola",
    "meu ano",
    "minha serie",
    "minha série",
    "onde estudo",
    "onde eu estudo",
    "me chamo",
    "como me chamo",
    "quem sou eu",
    "lembra de mim",
    "lembra do meu",
    "você sabe meu",
    "voce sabe meu",
    "sobre mim",
    "minhas turmas",
    "onde eu leciono",
    "qual minha",
    "qual meu",
)


def eh_consulta_pessoal(texto: str) -> bool:
    norm = " ".join((texto or "").strip().lower().split())
    return any(p in norm for p in _CONSULTA_PESSOAL)


def _parse_json_array(texto: str) -> list[dict[str, Any]]:
    texto = (texto or "").strip()
    match = re.search(r"\[.*\]", texto, re.DOTALL)
    if not match:
        return []
    try:
        data = json.loads(match.group())
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


async def listar(token_id: Optional[str], limite: int = 40) -> list[dict[str, Any]]:
    if not token_id:
        return []
    return await db.listar_contexto_usuario(token_id, limite=limite)


async def formatar_bloco(token_id: Optional[str], *, compacto: bool = False) -> str:
    """Texto para injetar no system prompt (somente deste token)."""
    itens = await listar(token_id, limite=20 if compacto else 35)
    if not itens:
        return ""

    linhas: list[str] = []
    for item in itens:
        tipo = item.get("tipo") or "outro"
        chave = item.get("chave") or "?"
        valor = item.get("valor") or ""
        if compacto and len(valor) > 120:
            valor = valor[:117] + "..."
        linhas.append(f"- [{tipo}] {chave}: {valor}")

    titulo = (
        "Contexto deste usuário (use quando relevante; dados pessoais são privados):"
        if not compacto
        else "Contexto do usuário:"
    )
    return f"{titulo}\n" + "\n".join(linhas)


async def extrair_e_salvar(
    token_id: Optional[str], prompt: str, *, tipo_usuario: str = "professor"
) -> int:
    """Classifica e persiste fatos da mensagem via modelo leve. Retorna quantos itens salvos."""
    if not settings.context_extract_enabled or not token_id:
        return 0

    prompt_limpo = (prompt or "").strip()
    if len(prompt_limpo) < 12:
        return 0

    papel = "aluno" if tipo_usuario == "aluno" else "professor"

    try:
        raw = await ollama.generate(
            model=settings.model_light,
            prompt=(
                f"Analise a mensagem do {papel} e extraia fatos úteis para lembrar depois.\n"
                "Classifique cada fato em exatamente um tipo:\n"
                "- pessoal: nome, escola, cidade, turma, série, dados privados\n"
                "- preferencia: estilo de resposta, matérias favoritas, preferências\n"
                "- contexto: rotina de estudos/ensino, projetos, informações úteis\n"
                "- outro: demais fatos relevantes\n\n"
                "Se não houver nada para memorizar, retorne [].\n"
                "Responda SOMENTE com JSON válido:\n"
                '[{"tipo":"pessoal","chave":"nome_curto","valor":"texto"}]\n\n'
                f'Mensagem: "{prompt_limpo[:1500]}"'
            ),
            temperature=0.1,
            options={"num_predict": 400},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Extração de contexto falhou: %s", exc)
        return 0

    salvos = 0
    for item in _parse_json_array(raw):
        if not isinstance(item, dict):
            continue
        tipo = str(item.get("tipo", "")).strip().lower()
        chave = str(item.get("chave", "")).strip().lower().replace(" ", "_")
        valor = str(item.get("valor", "")).strip()
        if tipo not in TIPOS_VALIDOS or not chave or not valor or len(valor) < 2:
            continue
        if len(chave) > 120 or len(valor) > 2000:
            continue
        row = await db.upsert_contexto_usuario(
            token_id=token_id,
            tipo=tipo,
            chave=chave,
            valor=valor,
            origem_prompt=prompt_limpo[:500],
        )
        if row:
            salvos += 1
    if salvos:
        logger.info("Contexto: %d fato(s) salvos para token %s", salvos, token_id)
    return salvos


async def responder_com_contexto(token_id: Optional[str], prompt: str) -> Optional[str]:
    """Resposta rápida via modelo leve usando só o contexto deste token."""
    if not token_id:
        return None
    bloco = await formatar_bloco(token_id, compacto=True)
    if not bloco:
        return None

    try:
        return await ollama.generate(
            model=settings.model_light,
            prompt=(
                "Você é o Profinho, livrinho educativo bem-humorado. "
                "Fale DIRETO com quem pergunta, usando 'você'. "
                "Responda de forma didática, curta e objetiva em português do Brasil "
                "usando APENAS o contexto abaixo e a pergunta do usuário. "
                'Se a resposta não estiver no contexto, responda exatamente: "NAO_SEI"\n\n'
                f"{bloco}\n\n"
                f'Pergunta: "{prompt[:800]}"\n\n'
                "Resposta:"
            ),
            temperature=0.2,
            options={"num_predict": settings.chat_num_predict_curto},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Resposta com contexto falhou: %s", exc)
        return None


def resposta_contexto_valida(texto: Optional[str]) -> bool:
    if not texto:
        return False
    t = texto.strip().upper()
    return t not in ("NAO_SEI", "NÃO SEI", "NAO SEI", "")
