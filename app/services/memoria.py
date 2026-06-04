"""Serviço de memória/contexto (estilo ChatGPT/Claude/Cursor).

Responsável por:
  - garantir/abrir uma sessão de conversa;
  - montar a lista de mensagens (system + memórias de longo prazo + histórico
    recente da sessão + nova mensagem) que será enviada ao modelo;
  - persistir as mensagens do usuário e do assistente;
  - gerar um título automático para a sessão a partir da primeira pergunta.
"""

from __future__ import annotations

from typing import Any, Optional

from app import database as db

# Quantos turnos recentes da sessão entram no contexto (janela de memória curta).
JANELA_MENSAGENS = 20


async def garantir_sessao(
    sessao_id: Optional[str],
    token_id: Optional[str],
    tipo: str,
    primeiro_prompt: str,
    modelo: Optional[str] = None,
    categoria: Optional[str] = None,
) -> Optional[str]:
    """Retorna um sessao_id válido. Cria uma sessão nova se necessário."""
    if sessao_id:
        sessao = await db.get_sessao(sessao_id, token_id)
        if sessao:
            return sessao_id
        # id informado não existe/não pertence ao token -> cria uma nova
    titulo = _titulo_automatico(primeiro_prompt)
    return await db.criar_sessao(
        token_id=token_id, tipo=tipo, titulo=titulo, modelo=modelo, categoria=categoria
    )


def _titulo_automatico(texto: str) -> str:
    limpo = " ".join((texto or "Nova conversa").split())
    return limpo[:60] if limpo else "Nova conversa"


async def montar_contexto(
    sessao_id: Optional[str],
    token_id: Optional[str],
    system: str,
    prompt_usuario: str,
    historico_extra: Optional[list[dict[str, str]]] = None,
) -> list[dict[str, str]]:
    """Monta as mensagens para o modelo: system (+memórias) + histórico + prompt atual."""
    messages: list[dict[str, str]] = []

    bloco_memorias = await _bloco_memorias(token_id)
    system_final = system if not bloco_memorias else f"{system}\n\n{bloco_memorias}"
    messages.append({"role": "system", "content": system_final})

    # Histórico persistido da sessão (memória de curto prazo).
    if sessao_id:
        anteriores = await db.listar_mensagens(sessao_id, limite=JANELA_MENSAGENS)
        for m in anteriores:
            if m["role"] in ("user", "assistant"):
                messages.append({"role": m["role"], "content": m["conteudo"]})

    # Histórico passado direto na requisição (clientes sem sessão).
    for m in historico_extra or []:
        messages.append({"role": m["role"], "content": m["content"]})

    messages.append({"role": "user", "content": prompt_usuario})
    return messages


async def _bloco_memorias(token_id: Optional[str]) -> str:
    memorias = await db.listar_memorias(token_id, limite=30)
    if not memorias:
        return ""
    linhas = [f"- {m['chave']}: {m['valor']}" for m in memorias]
    return (
        "Memórias persistentes sobre o usuário (use quando relevante):\n"
        + "\n".join(linhas)
    )


async def registrar_turno(
    sessao_id: Optional[str],
    prompt_usuario: str,
    resposta: str,
    modelo: str,
    categoria: str,
    metadados_usuario: Optional[dict[str, Any]] = None,
    metadados_assistente: Optional[dict[str, Any]] = None,
) -> None:
    """Persiste o par (mensagem do usuário, resposta do assistente)."""
    if not sessao_id:
        return
    await db.adicionar_mensagem(
        sessao_id, "user", prompt_usuario, categoria=categoria, metadados=metadados_usuario
    )
    await db.adicionar_mensagem(
        sessao_id,
        "assistant",
        resposta,
        modelo=modelo,
        categoria=categoria,
        metadados=metadados_assistente,
    )
    await db.tocar_sessao(sessao_id)
