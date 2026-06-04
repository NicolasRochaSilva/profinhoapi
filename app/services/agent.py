"""Orquestrador do agente: gera arquivos/código com o modelo, valida e corrige.

Funciona como o "modo agente" do Cursor:
  1. (opcional) pesquisa documentação na web (SearXNG + Crawl4AI);
  2. pede ao modelo de código um plano de arquivos em JSON;
  3. escreve os arquivos no workspace;
  4. executa um comando de validação;
  5. se falhar, devolve o erro ao modelo e itera (auto-correção).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.config import settings
from app.ollama_client import ollama
from app.services import agent_executor as ex
from app.services import crawl4ai, searxng

logger = logging.getLogger("profinho.agent.orq")

_SYSTEM = """Você é o Profinho Agent, um engenheiro de software autônomo (como o modo agente do Cursor).
Você cria projetos completos, escreve código de qualidade e garante que ele funcione.

Responda SEMPRE com um único bloco JSON válido, sem texto fora do JSON, no formato:
{
  "resumo": "explicação curta do que foi feito",
  "arquivos": [
    {"path": "caminho/relativo/arquivo.ext", "conteudo": "conteúdo completo do arquivo"}
  ],
  "comando_validacao": "comando shell para validar (ou null)"
}

Regras:
- Caminhos sempre relativos (sem '/' inicial, sem '..').
- Inclua TODOS os arquivos necessários para rodar.
- Para Python, prefira validar com 'python -m py_compile <arquivos>' ou testes.
- Não escreva nada além do JSON."""


def _extrair_json(texto: str) -> dict[str, Any]:
    """Extrai o primeiro objeto JSON do texto do modelo (tolerante a ```json)."""
    texto = texto.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", texto, re.DOTALL)
    if fence:
        texto = fence.group(1)
    inicio = texto.find("{")
    fim = texto.rfind("}")
    if inicio == -1 or fim == -1:
        raise ValueError("Modelo não retornou JSON.")
    bruto = texto[inicio : fim + 1]
    return json.loads(bruto)


async def _coletar_docs(query: str, max_fontes: int) -> tuple[str, list[str]]:
    """Busca documentação e lê o conteúdo. Retorna (contexto, fontes)."""
    resultados = await searxng.buscar(query, max_resultados=max_fontes)
    urls = [r["url"] for r in resultados if r.get("url")]
    conteudos = await crawl4ai.ler_varias(urls)
    partes = []
    for url, txt in conteudos.items():
        partes.append(f"### Fonte: {url}\n{txt[:6000]}")
    return "\n\n".join(partes), list(conteudos.keys())


async def executar_agente(
    instrucao: str,
    projeto: str | None,
    validar: bool = True,
    usar_web: bool = False,
    max_iteracoes: int = 4,
) -> dict[str, Any]:
    projeto = projeto or "projeto"
    fontes: list[str] = []
    contexto_web = ""

    if usar_web:
        contexto_web, fontes = await _coletar_docs(instrucao, max_fontes=4)

    prompt = instrucao
    if contexto_web:
        prompt = (
            f"{instrucao}\n\n"
            f"Use a documentação abaixo como referência para escrever código correto:\n\n"
            f"{contexto_web}"
        )

    historico_erros = ""
    plano: dict[str, Any] = {}
    arquivos_aplicados: list[dict[str, Any]] = []
    validacao: dict[str, Any] | None = None

    for iteracao in range(1, max(1, max_iteracoes) + 1):
        prompt_iter = prompt
        if historico_erros:
            prompt_iter = (
                f"{prompt}\n\n"
                f"A tentativa anterior falhou na validação. Corrija os erros:\n{historico_erros}\n"
                f"Reescreva os arquivos necessários."
            )

        saida = await ollama.generate(
            model=settings.model_code,
            prompt=prompt_iter,
            system=_SYSTEM,
            temperature=0.2,
            options={"num_ctx": 8192},
            exclusivo=True,
        )

        try:
            plano = _extrair_json(saida)
        except Exception as exc:  # noqa: BLE001
            logger.warning("JSON inválido na iteração %s: %s", iteracao, exc)
            historico_erros = f"Sua resposta não era JSON válido ({exc}). Responda apenas JSON."
            continue

        arquivos_aplicados = []
        for arq in plano.get("arquivos", []):
            path = arq.get("path")
            conteudo = arq.get("conteudo", "")
            if not path:
                continue
            try:
                info = ex.escrever_arquivo(projeto, path, conteudo)
                arquivos_aplicados.append(info)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Falha ao escrever %s: %s", path, exc)

        if not validar:
            validacao = None
            break

        comando = plano.get("comando_validacao") or ex.comando_validacao_padrao(
            [a["path"] for a in arquivos_aplicados]
        )
        if not comando:
            validacao = {"sucesso": True, "comando": None, "stdout": "Sem comando de validação."}
            break

        validacao = await ex.executar_comando(projeto, comando)
        if validacao.get("sucesso"):
            break

        historico_erros = (
            f"Comando: {validacao.get('comando')}\n"
            f"stderr:\n{validacao.get('stderr')}\n"
            f"stdout:\n{validacao.get('stdout')}"
        )

    return {
        "projeto": projeto,
        "resumo": plano.get("resumo", "Projeto gerado."),
        "arquivos": arquivos_aplicados,
        "validacao": validacao,
        "fontes": fontes,
    }
