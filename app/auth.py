"""Autenticação por token. O acesso é liberado pela tabela `tokens` (Ativo = true)."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings
from app.database import fetch_token

bearer_scheme = HTTPBearer(auto_error=False)


async def require_token(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> dict[str, Any]:
    """Valida o token Bearer contra a tabela `tokens`.

    Regras:
      - Sem token -> 401.
      - MASTER_TOKEN (se configurado) é sempre aceito.
      - Bearer deve ser o mesmo valor SHA-256 (hex) gravado em tokens.token.
      - Token precisa existir e estar com Ativo = TRUE.
    """
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de acesso ausente. Use 'Authorization: Bearer <token>'.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials.strip()

    if settings.master_token and token == settings.master_token:
        return {
            "id": None,
            "token": token,
            "ativo": True,
            "professor": "master",
            "tipo_usuario": "professor",
        }

    record = await fetch_token(token)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido.",
        )
    if not record.get("ativo"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token inativo. Acesso bloqueado.",
        )
    return record
