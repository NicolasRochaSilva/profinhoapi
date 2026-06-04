"""Rotas de visão: comentar imagem, gerar página a partir de imagem, OCR."""

from __future__ import annotations

import base64

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.auth import require_token
from app.config import settings
from app.ollama_client import ollama

router = APIRouter(tags=["imagem"])

_PROMPTS = {
    "comentar": "Analise a imagem em detalhes e comente sobre ela em português do Brasil. "
                "Descreva elementos, contexto e, se houver texto, transcreva.",
    "ocr": "Faça OCR: extraia e transcreva TODO o texto presente na imagem, "
           "preservando a estrutura. Responda só com o texto extraído.",
    "pagina": "Você é um desenvolvedor front-end. Analise o layout da imagem e gere uma "
              "página web COMPLETA e funcional (HTML + CSS + JS embutidos em um único "
              "arquivo index.html) que reproduza fielmente o design mostrado. "
              "Responda apenas com o código HTML.",
}


@router.post("/vision", summary="Analisar imagem (comentar, gerar página ou OCR)")
async def vision(
    arquivo: UploadFile = File(..., description="Imagem (png, jpg, etc.)"),
    prompt: str = Form("", description="Instrução adicional (opcional)."),
    modo: str = Form("comentar", description="comentar | pagina | ocr"),
    _=Depends(require_token),
):
    if modo not in _PROMPTS:
        raise HTTPException(status_code=400, detail="modo inválido. Use comentar, pagina ou ocr.")

    conteudo = await arquivo.read()
    if not conteudo:
        raise HTTPException(status_code=400, detail="Arquivo de imagem vazio.")

    imagem_b64 = base64.b64encode(conteudo).decode("utf-8")
    instrucao = _PROMPTS[modo]
    if prompt:
        instrucao = f"{instrucao}\n\nInstrução do usuário: {prompt}"

    resposta = await ollama.generate(
        model=settings.model_vision,
        prompt=instrucao,
        images=[imagem_b64],
        temperature=0.2 if modo == "pagina" else 0.4,
        options={"num_ctx": 8192},
        exclusivo=True,
    )

    return {
        "modo": modo,
        "modelo": settings.model_vision,
        "arquivo": arquivo.filename,
        "resultado": resposta,
    }
