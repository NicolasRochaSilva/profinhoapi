"""Rotas do agente estilo Cursor (criar arquivos, código e validar)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.auth import require_token
from app.schemas import AgentRunRequest, AgentRunResponse, FileChange
from app.services import agent as agent_service
from app.services import agent_executor as ex
from app.services import mcp, memoria
from app.services import moderacao, perfil_usuario as perfil

router = APIRouter(tags=["agente"])


@router.post("/agent/run", response_model=AgentRunResponse, summary="Executar tarefa do agente")
async def agent_run(req: AgentRunRequest, token=Depends(require_token)):
    tipo = perfil.normalizar_tipo(token.get("tipo_usuario"))
    if not perfil.agente_permitido(tipo):
        raise HTTPException(
            status_code=403,
            detail="O agente de código está disponível apenas para tokens de professor.",
        )

    bloqueio = moderacao.detectar_tema_bloqueado(req.instrucao)
    if bloqueio:
        raise HTTPException(
            status_code=400,
            detail=moderacao.resposta_bloqueio(bloqueio, tipo),
        )

    token_id = token.get("id")
    sessao_id = await memoria.garantir_sessao(
        sessao_id=req.sessao_id,
        token_id=token_id,
        tipo="agente",
        primeiro_prompt=req.instrucao,
        categoria="programacao",
    )

    resultado = await agent_service.executar_agente(
        instrucao=req.instrucao,
        projeto=req.projeto,
        validar=req.validar,
        usar_web=req.usar_web,
        max_iteracoes=req.max_iteracoes,
    )

    if sessao_id:
        arquivos_txt = ", ".join(a["path"] for a in resultado["arquivos"]) or "(nenhum)"
        resumo_assistente = (
            f"{resultado['resumo']}\nArquivos: {arquivos_txt}\n"
            f"Validação: {'ok' if (resultado.get('validacao') or {}).get('sucesso') else 'falhou/none'}"
        )
        await memoria.registrar_turno(
            sessao_id=sessao_id,
            prompt_usuario=req.instrucao,
            resposta=resumo_assistente,
            modelo="qwen2.5-coder:7b",
            categoria="programacao",
            metadados_assistente={
                "projeto": resultado["projeto"],
                "arquivos": resultado["arquivos"],
            },
        )

    return AgentRunResponse(
        projeto=resultado["projeto"],
        resumo=resultado["resumo"],
        arquivos=[FileChange(**a) for a in resultado["arquivos"]],
        validacao=resultado["validacao"],
        fontes=resultado["fontes"],
        sessao_id=sessao_id,
    )


@router.get("/agent/projects/{projeto}/files", summary="Listar arquivos de um projeto")
async def listar_arquivos(projeto: str, _=Depends(require_token)):
    return {"projeto": projeto, "arquivos": ex.listar_arquivos(projeto)}


@router.get("/agent/projects/{projeto}/file", summary="Ler um arquivo do projeto")
async def ler_arquivo(projeto: str, path: str, _=Depends(require_token)):
    try:
        return {"path": path, "conteudo": ex.ler_arquivo(projeto, path)}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado.")


@router.post("/agent/projects/{projeto}/exec", summary="Executar comando no projeto (validação)")
async def executar(projeto: str, comando: str, _=Depends(require_token)):
    return await ex.executar_comando(projeto, comando)


@router.get("/mcp/tools", summary="Listar ferramentas MCP disponíveis")
async def mcp_tools(_=Depends(require_token)):
    return {"tools": mcp.listar_tools()}


@router.post("/mcp/call/{nome}", summary="Chamar uma ferramenta MCP")
async def mcp_call(nome: str, args: dict, _=Depends(require_token)):
    try:
        resultado = await mcp.chamar_tool(nome, args)
        return {"tool": nome, "resultado": resultado}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Tool '{nome}' não existe.")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc))
