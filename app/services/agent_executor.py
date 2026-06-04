"""Executor do agente estilo Cursor.

Capacidades dentro de um workspace isolado (sandbox em disco):
  - criar/atualizar/ler/listar arquivos
  - executar comandos (validar se o código funciona)

Tudo é confinado ao diretório AGENT_WORKSPACE para evitar escrita fora do sandbox.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger("profinho.agent")

WORKSPACE = Path(settings.agent_workspace)


def _projeto_dir(projeto: str) -> Path:
    nome = "".join(c for c in (projeto or "default") if c.isalnum() or c in ("-", "_")) or "default"
    destino = (WORKSPACE / nome).resolve()
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    destino.mkdir(parents=True, exist_ok=True)
    return destino


def _resolver_seguro(base: Path, relativo: str) -> Path:
    alvo = (base / relativo).resolve()
    if base not in alvo.parents and alvo != base:
        raise ValueError(f"Caminho fora do sandbox: {relativo}")
    return alvo


def escrever_arquivo(projeto: str, path: str, conteudo: str) -> dict[str, Any]:
    base = _projeto_dir(projeto)
    alvo = _resolver_seguro(base, path)
    existia = alvo.exists()
    alvo.parent.mkdir(parents=True, exist_ok=True)
    alvo.write_text(conteudo, encoding="utf-8")
    return {
        "path": str(alvo.relative_to(base)),
        "acao": "atualizado" if existia else "criado",
        "bytes": len(conteudo.encode("utf-8")),
    }


def ler_arquivo(projeto: str, path: str) -> str:
    base = _projeto_dir(projeto)
    alvo = _resolver_seguro(base, path)
    if not alvo.exists():
        raise FileNotFoundError(path)
    return alvo.read_text(encoding="utf-8")


def listar_arquivos(projeto: str) -> list[str]:
    base = _projeto_dir(projeto)
    arquivos: list[str] = []
    for p in base.rglob("*"):
        if p.is_file():
            arquivos.append(str(p.relative_to(base)))
    return sorted(arquivos)


async def executar_comando(projeto: str, comando: str, timeout: int | None = None) -> dict[str, Any]:
    """Executa um comando shell dentro do diretório do projeto (validação)."""
    base = _projeto_dir(projeto)
    timeout = timeout or settings.agent_exec_timeout
    try:
        proc = await asyncio.create_subprocess_shell(
            comando,
            cwd=str(base),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return {
            "comando": comando,
            "exit_code": proc.returncode,
            "stdout": stdout.decode("utf-8", "replace")[-8000:],
            "stderr": stderr.decode("utf-8", "replace")[-8000:],
            "sucesso": proc.returncode == 0,
        }
    except asyncio.TimeoutError:
        return {
            "comando": comando,
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Tempo limite de {timeout}s excedido.",
            "sucesso": False,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "comando": comando,
            "exit_code": -1,
            "stdout": "",
            "stderr": str(exc),
            "sucesso": False,
        }


def comando_validacao_padrao(arquivos: list[str]) -> str | None:
    """Sugere um comando de validação a partir dos arquivos gerados."""
    nomes = [a.lower() for a in arquivos]
    if any(n == "requirements.txt" for n in nomes):
        # Apenas valida sintaxe dos .py para não exigir instalação pesada.
        pys = [a for a in arquivos if a.endswith(".py")]
        if pys:
            return "python -m py_compile " + " ".join(shlex.quote(p) for p in pys)
    if "package.json" in nomes:
        return "node --check $(ls *.js 2>/dev/null | head -n1) 2>/dev/null || true"
    pys = [a for a in arquivos if a.endswith(".py")]
    if pys:
        return "python -m py_compile " + " ".join(shlex.quote(p) for p in pys)
    return None
