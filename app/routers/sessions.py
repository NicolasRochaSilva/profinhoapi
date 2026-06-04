"""Rotas de memória: sessões/conversas e memória de longo prazo.

Permite gerenciar o histórico como no ChatGPT/Claude/Cursor:
  - listar conversas, abrir uma conversa com todas as mensagens;
  - renomear / arquivar / apagar conversas;
  - guardar e consultar "memórias" persistentes do usuário (por token).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app import database as db
from app.auth import require_token
from app.schemas import (
    MemoryCreate,
    MemoryInfo,
    MessageInfo,
    SessionCreate,
    SessionDetail,
    SessionInfo,
    SessionUpdate,
)

router = APIRouter(tags=["memoria"])


# ---------------- Sessões ----------------


@router.post("/sessions", response_model=SessionInfo, summary="Criar uma conversa/sessão")
async def criar(req: SessionCreate, token=Depends(require_token)):
    sessao_id = await db.criar_sessao(
        token_id=token.get("id"), tipo=req.tipo, titulo=req.titulo
    )
    if not sessao_id:
        raise HTTPException(status_code=503, detail="Banco indisponível.")
    sessao = await db.get_sessao(sessao_id, token.get("id"))
    return SessionInfo(id=str(sessao["id"]), **_campos_sessao(sessao))


@router.get("/sessions", response_model=list[SessionInfo], summary="Listar conversas")
async def listar(
    tipo: str | None = None,
    incluir_arquivadas: bool = False,
    token=Depends(require_token),
):
    sessoes = await db.listar_sessoes(
        token_id=token.get("id"), tipo=tipo, incluir_arquivadas=incluir_arquivadas
    )
    return [SessionInfo(id=str(s["id"]), **_campos_sessao(s)) for s in sessoes]


@router.get("/sessions/{sessao_id}", response_model=SessionDetail, summary="Abrir conversa (com mensagens)")
async def abrir(sessao_id: str, token=Depends(require_token)):
    sessao = await db.get_sessao(sessao_id, token.get("id"))
    if not sessao:
        raise HTTPException(status_code=404, detail="Sessão não encontrada.")
    mensagens = await db.listar_mensagens(sessao_id, limite=200)
    return SessionDetail(
        id=str(sessao["id"]),
        **_campos_sessao(sessao),
        mensagens=[
            MessageInfo(
                id=str(m["id"]),
                role=m["role"],
                conteudo=m["conteudo"],
                modelo=m.get("modelo"),
                categoria=m.get("categoria"),
                criado_em=m.get("criado_em"),
            )
            for m in mensagens
        ],
    )


@router.patch("/sessions/{sessao_id}", summary="Renomear ou arquivar conversa")
async def atualizar(sessao_id: str, req: SessionUpdate, token=Depends(require_token)):
    ok = await db.atualizar_sessao(
        sessao_id, token.get("id"), titulo=req.titulo, arquivada=req.arquivada
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Sessão não encontrada ou nada a atualizar.")
    return {"ok": True}


@router.delete("/sessions/{sessao_id}", summary="Apagar conversa")
async def apagar(sessao_id: str, token=Depends(require_token)):
    ok = await db.deletar_sessao(sessao_id, token.get("id"))
    if not ok:
        raise HTTPException(status_code=404, detail="Sessão não encontrada.")
    return {"ok": True}


# ---------------- Memória de longo prazo ----------------


@router.get("/memory", response_model=list[MemoryInfo], summary="Listar memórias do usuário")
async def listar_memorias(token=Depends(require_token)):
    memorias = await db.listar_memorias(token.get("id"))
    return [
        MemoryInfo(
            id=str(m["id"]),
            chave=m["chave"],
            valor=m["valor"],
            origem=m.get("origem"),
            criado_em=m.get("criado_em"),
            atualizado_em=m.get("atualizado_em"),
        )
        for m in memorias
    ]


@router.post("/memory", response_model=MemoryInfo, summary="Salvar/atualizar uma memória")
async def salvar_memoria(req: MemoryCreate, token=Depends(require_token)):
    if token.get("id") is None:
        raise HTTPException(
            status_code=400,
            detail="Memória de longo prazo exige um token de banco (não o master).",
        )
    m = await db.salvar_memoria(token.get("id"), req.chave, req.valor, origem="manual")
    if not m:
        raise HTTPException(status_code=503, detail="Banco indisponível.")
    return MemoryInfo(
        id=str(m["id"]),
        chave=m["chave"],
        valor=m["valor"],
        origem=m.get("origem"),
        criado_em=m.get("criado_em"),
        atualizado_em=m.get("atualizado_em"),
    )


@router.delete("/memory/{memoria_id}", summary="Apagar uma memória")
async def apagar_memoria(memoria_id: str, token=Depends(require_token)):
    ok = await db.deletar_memoria(memoria_id, token.get("id"))
    if not ok:
        raise HTTPException(status_code=404, detail="Memória não encontrada.")
    return {"ok": True}


def _campos_sessao(s: dict) -> dict:
    return {
        "tipo": s["tipo"],
        "titulo": s.get("titulo"),
        "modelo": s.get("modelo"),
        "categoria": s.get("categoria"),
        "arquivada": s.get("arquivada", False),
        "criado_em": s.get("criado_em"),
        "atualizado_em": s.get("atualizado_em"),
    }
