"""Pipeline educação interativa: web (opcional) → phi3 (brief) → coder (HTML/CSS/JS)."""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Optional

from app.config import settings
from app.ollama_client import ollama
from app.router_model import decidir_usar_web
from app.services import crawl4ai, searxng
from app.services.perfil_usuario import system_prompt

logger = logging.getLogger("profinho.exercicio_interativo")

_RE_CRIAR = re.compile(
    r"\b(crie|criar|monte|faça|faca|elabore|gere|gerar|desenvolva|construa)\b",
    re.IGNORECASE,
)
_RE_TIPO_WEB = re.compile(
    r"\b("
    r"landing\s+page|p[aá]gina\s+(?:educacional|interativa|html)|"
    r"exerc[ií]cio\s+interativ[oa]|exerc[ií]cios\s+interativos|"
    r"atividade\s+interativa|jogo\s+educacional|jogos?\s+educacionais?|"
    r"quiz\s+interativo|html\s+interativo"
    r")\b",
    re.IGNORECASE,
)
_RE_EXERCICIO = re.compile(
    r"\b(exerc[ií]cio|exerc[ií]cios|atividade|atividades|jogo|jogos|quiz)\b",
    re.IGNORECASE,
)
_RE_INTERATIVO = re.compile(
    r"\b(interativ[oa]s?|html|jogo|jogos|landing|arrastar|clic[aá]vel)\b",
    re.IGNORECASE,
)
_RE_DETALHES = re.compile(
    r"\b("
    r"\d+\s*(quest|pergunt|item|alternativ|lacuna)|"
    r"\d+º\s+ano|ensino\s+(fundamental|m[eé]dio)|"
    r"m[uú]ltipla\s+escolha|multipla\s+escolha|verdadeiro\s+ou\s+falso|"
    r"gabarito|dificuldade|n[ií]vel|bncc|habilidade|"
    r"cor\s+prim[aá]ria|fonte|layout|se[cç][aã]o"
    r")\b",
    re.IGNORECASE,
)
_RE_RUIDO_BUSCA = re.compile(
    r"\b("
    r"crie|criar|monte|faça|faca|elabore|gere|gerar|desenvolva|construa|"
    r"um|uma|uns|umas|de|do|da|sobre|para|com|"
    r"exerc[ií]cio\s+interativ[oa]|exerc[ií]cios\s+interativos|"
    r"atividade\s+interativa|jogo\s+educacional|landing\s+page|"
    r"html\s+interativo|p[aá]gina\s+interativa|p[aá]gina\s+educacional|"
    r"interativ[oa]s?"
    r")\b",
    re.IGNORECASE,
)

_SYSTEM_BRIEF = """Você é o Profinho, livrinho educativo simpático e didático.
O usuário pediu material web educacional com poucos detalhes.
Elabore um BRIEF TÉCNICO (sem código) para um desenvolvedor front-end implementar.

Use as referências da web (se fornecidas) para garantir conteúdo pedagógico correto.
Inclua, em tópicos objetivos:
1. TIPO: landing_page | exercicio_interativo | jogo_educacional (escolha um)
2. Tema/conteúdo pedagógico e objetivo de aprendizagem
3. Público-alvo (infira série/idade se não informado)
4. Mecânica interativa (quiz, lacunas, arrastar-soltar, memória, etc.)
5. Quantidade sugerida de perguntas/itens (5 a 8 se não informado)
6. Feedback ao acertar/errar e mensagem final
7. Visual: simples, legível, cores suaves, responsivo básico
8. Restrições: um único arquivo HTML com CSS e JS embutidos; jQuery via CDN se precisar

Máximo 350 palavras. Português do Brasil. Não escreva HTML."""

_SYSTEM_CODER = """Você é o Profinho, desenvolvedor front-end educacional.

REGRAS ABSOLUTAS:
- Entregue APENAS um arquivo HTML completo (<!DOCTYPE html> ...), com CSS e JS no mesmo arquivo.
- Tecnologias: HTML5, CSS3, JavaScript e jQuery (CDN) se necessário.
- PROIBIDO: React, Vue, Angular, Python, PHP, backends, npm, múltiplos arquivos, Tailwind/Bootstrap pesado.
- Você SÓ pode construir: (1) landing pages educacionais, (2) exercícios interativos, (3) jogos educacionais.
- Use as referências pedagógicas do brief para conteúdo factual correto.
- Deve funcionar abrindo o .html no navegador, sem servidor.
- Responda SOMENTE com o código HTML, sem markdown e sem texto antes ou depois."""

MOTIVO_ROTEAMENTO = (
    "Material web educacional: busca referências (se necessário), "
    f"{settings.model_edu} elabora brief, {settings.model_code} gera HTML."
)


@dataclass
class PipelineExercicio:
    brief: str
    fontes: list[str]
    usar_web: bool
    motivo_web: str


def eh_pedido_exercicio_interativo(texto: str) -> bool:
    """Pedido de material web educacional interativo com poucos detalhes."""
    t = texto.strip()
    if not t or len(t) > 450:
        return False

    tipo_explicito = bool(_RE_TIPO_WEB.search(t))
    criar_com_exercicio = bool(_RE_CRIAR.search(t) and _RE_EXERCICIO.search(t))
    criar_interativo = bool(_RE_CRIAR.search(t) and _RE_INTERATIVO.search(t))

    if not (tipo_explicito or criar_com_exercicio or criar_interativo):
        return False

    detalhes = len(_RE_DETALHES.findall(t))
    if detalhes >= 2:
        return False
    if len(t) > 180 and detalhes >= 1:
        return False

    return True


def extrair_html(texto: str) -> str:
    bloco = re.search(r"```(?:html)?\s*\n(.*?)```", texto, re.DOTALL | re.IGNORECASE)
    if bloco:
        return bloco.group(1).strip()
    inicio = re.search(r"(<!DOCTYPE|<html)", texto, re.IGNORECASE)
    if inicio:
        return texto[inicio.start() :].strip()
    return texto.strip()


def modelo_label() -> str:
    return f"{settings.model_edu}+{settings.model_code}"


def _query_educacional(prompt: str) -> str:
    tema = _RE_RUIDO_BUSCA.sub(" ", prompt)
    tema = re.sub(r"\s+", " ", tema).strip(" .,-")
    if len(tema) < 4:
        tema = prompt.strip()
    return f"{tema} conceitos educação ensino"


async def _decidir_busca_referencias(
    prompt: str,
    usar_web: Optional[bool],
) -> tuple[bool, str]:
    if usar_web is False:
        return False, "Busca web desativada pelo cliente."
    if usar_web is True:
        return True, "Busca web ativada para referências pedagógicas."
    precisa, motivo = await decidir_usar_web(prompt)
    if precisa:
        return True, f"{motivo} (referências para material interativo)."
    # Material educacional interativo com poucos detalhes: buscar referências por padrão.
    return True, (
        "Referências pedagógicas na web para enriquecer exercício/jogo/landing "
        "(pedido com poucos detalhes)."
    )


async def _buscar_referencias(
    prompt: str,
    usar_web: Optional[bool],
) -> tuple[str, list[str], bool, str]:
    buscar, motivo_web = await _decidir_busca_referencias(prompt, usar_web)
    if not buscar:
        return "", [], False, motivo_web

    query = _query_educacional(prompt)
    resultados = await searxng.buscar(query, max_resultados=4)
    urls = [r["url"] for r in resultados if r.get("url")]
    if not urls:
        logger.info("Exercício interativo: busca sem URLs para %r", query)
        return "", [], False, "Busca web sem resultados úteis; brief só com o pedido."

    conteudos = await crawl4ai.ler_varias(urls, max_chars_total=10000)
    fontes = list(conteudos.keys())
    if not conteudos:
        return "", [], False, "Busca web realizada, mas páginas não puderam ser lidas."

    contexto = "\n\n".join(
        f"Fonte: {u}\n{c[:3500]}" for u, c in conteudos.items()
    )
    logger.info(
        "Exercício interativo: %d fontes, %d chars de contexto",
        len(fontes),
        len(contexto),
    )
    return contexto, fontes, True, motivo_web


def _bloco_referencias(contexto_web: str) -> str:
    if not contexto_web:
        return ""
    return f"\n\n[Referências pedagógicas da web]\n{contexto_web}"


async def _gerar_brief(
    prompt: str,
    tipo_usuario: str,
    contexto_token: str = "",
    contexto_web: str = "",
) -> str:
    ctx = f"\n\nContexto do professor:\n{contexto_token}" if contexto_token else ""
    refs = _bloco_referencias(contexto_web)
    return await ollama.generate(
        model=settings.model_edu,
        system=system_prompt("educacao", tipo_usuario),
        prompt=(
            f"{_SYSTEM_BRIEF}\n\n"
            f"Pedido do usuário: {prompt[:2000]}{ctx}{refs}\n\n"
            "Brief técnico:"
        ),
        temperature=0.4,
        options={"num_predict": 700},
        exclusivo=True,
    )


def _prompt_coder(brief: str, prompt: str) -> str:
    return (
        "Implemente o material web conforme o brief abaixo.\n\n"
        f"{brief}\n\n"
        f"Pedido original: {prompt[:800]}\n\n"
        "HTML completo:"
    )


async def preparar_pipeline(
    prompt: str,
    tipo_usuario: str,
    contexto_token: str = "",
    usar_web: Optional[bool] = None,
) -> PipelineExercicio:
    contexto_web, fontes, usou_web, motivo_web = await _buscar_referencias(
        prompt, usar_web
    )
    brief = await _gerar_brief(
        prompt, tipo_usuario, contexto_token, contexto_web if usou_web else ""
    )
    logger.info("Brief exercício interativo (%d chars), web=%s", len(brief), usou_web)
    return PipelineExercicio(
        brief=brief,
        fontes=fontes,
        usar_web=usou_web,
        motivo_web=motivo_web,
    )


async def gerar_exercicio_interativo(
    prompt: str,
    tipo_usuario: str,
    contexto_token: str = "",
    usar_web: Optional[bool] = None,
) -> tuple[str, str, str, bool, str, list[str]]:
    """Retorna (html, modelo_label, motivo_roteamento, usar_web, motivo_web, fontes)."""
    prep = await preparar_pipeline(prompt, tipo_usuario, contexto_token, usar_web)

    bruto = await ollama.generate(
        model=settings.model_code,
        system=_SYSTEM_CODER,
        prompt=_prompt_coder(prep.brief, prompt),
        temperature=0.25,
        options={"num_predict": settings.chat_num_predict_exercicio},
        exclusivo=True,
    )
    html = extrair_html(bruto)
    return (
        html,
        modelo_label(),
        MOTIVO_ROTEAMENTO,
        prep.usar_web,
        prep.motivo_web,
        prep.fontes,
    )


async def iter_exercicio_interativo(
    prompt: str,
    tipo_usuario: str,
    contexto_token: str = "",
    usar_web: Optional[bool] = None,
) -> AsyncIterator[str]:
    prep = await preparar_pipeline(prompt, tipo_usuario, contexto_token, usar_web)

    async for pedaco in ollama.generate_stream(
        model=settings.model_code,
        system=_SYSTEM_CODER,
        prompt=_prompt_coder(prep.brief, prompt),
        temperature=0.25,
        options={"num_predict": settings.chat_num_predict_exercicio},
        exclusivo=True,
    ):
        yield pedaco


async def preparar_para_stream(
    prompt: str,
    tipo_usuario: str,
    contexto_token: str = "",
    usar_web: Optional[bool] = None,
) -> PipelineExercicio:
    """Prepara web + brief antes de emitir tokens SSE."""
    return await preparar_pipeline(prompt, tipo_usuario, contexto_token, usar_web)


async def iter_html_pipeline(prep: PipelineExercicio, prompt: str) -> AsyncIterator[str]:
    async for pedaco in ollama.generate_stream(
        model=settings.model_code,
        system=_SYSTEM_CODER,
        prompt=_prompt_coder(prep.brief, prompt),
        temperature=0.25,
        options={"num_predict": settings.chat_num_predict_exercicio},
        exclusivo=True,
    ):
        yield pedaco
