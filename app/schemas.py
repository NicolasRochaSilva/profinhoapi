"""Modelos de dados (Pydantic) usados pela API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

Categoria = Literal["chat", "programacao", "educacao", "imagem"]


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"] = "user"
    content: str


class ChatRequest(BaseModel):
    prompt: str = Field(..., description="Mensagem/pergunta do usuário.")
    categoria: Optional[Categoria] = Field(
        None,
        description="Force uma categoria. Se vazio, o roteador (llama3.2:3b) decide.",
    )
    historico: list[ChatMessage] = Field(default_factory=list)
    system: Optional[str] = Field(None, description="Instrução de sistema opcional.")
    temperature: float = 0.7
    usar_web: bool = Field(
        False, description="Se true, pesquisa na web (SearXNG+Crawl4AI) antes de responder."
    )
    sessao_id: Optional[str] = Field(
        None,
        description="ID da sessão/conversa. Se vazio, uma nova é criada e devolvida na resposta.",
    )
    salvar: bool = Field(True, description="Se true, salva a conversa na memória (banco).")


class ChatResponse(BaseModel):
    categoria: Categoria
    modelo: str
    resposta: str
    motivo_roteamento: Optional[str] = None
    fontes: list[str] = Field(default_factory=list)
    sessao_id: Optional[str] = None


class RouteResponse(BaseModel):
    categoria: Categoria
    modelo: str
    motivo: str


class VisionRequest(BaseModel):
    prompt: str = Field("Descreva e comente esta imagem.", description="Instrução.")
    modo: Literal["comentar", "pagina", "ocr"] = Field(
        "comentar",
        description="comentar = analisar; pagina = gerar HTML a partir da imagem; ocr = extrair texto.",
    )


class SearchRequest(BaseModel):
    query: str
    max_resultados: int = 5
    ler_conteudo: bool = Field(
        True, description="Se true, usa Crawl4AI para ler o conteúdo das páginas."
    )


class SearchResultItem(BaseModel):
    titulo: str
    url: str
    resumo: str = ""
    conteudo: Optional[str] = None


class SearchResponse(BaseModel):
    query: str
    resultados: list[SearchResultItem]


class DocCodeRequest(BaseModel):
    objetivo: str = Field(..., description="O que você quer construir/aprender.")
    query_doc: Optional[str] = Field(
        None, description="Busca de documentação. Se vazio, usa o objetivo."
    )
    max_fontes: int = 3


class AgentRunRequest(BaseModel):
    instrucao: str = Field(..., description="Tarefa para o agente executar.")
    projeto: Optional[str] = Field(
        None, description="Nome da pasta do projeto dentro do workspace."
    )
    validar: bool = Field(True, description="Tenta executar/validar o código gerado.")
    usar_web: bool = False
    max_iteracoes: int = 4
    sessao_id: Optional[str] = Field(
        None, description="Sessão do agente. Se vazia, uma nova é criada."
    )


class FileChange(BaseModel):
    path: str
    acao: Literal["criado", "atualizado"] = "criado"
    bytes: int = 0


class AgentRunResponse(BaseModel):
    projeto: str
    resumo: str
    arquivos: list[FileChange] = Field(default_factory=list)
    validacao: Optional[dict[str, Any]] = None
    fontes: list[str] = Field(default_factory=list)
    sessao_id: Optional[str] = None


# --- Sessões e memória (estilo ChatGPT/Claude/Cursor) ---


class SessionCreate(BaseModel):
    tipo: Literal["chat", "agente", "vision", "busca"] = "chat"
    titulo: Optional[str] = None


class SessionInfo(BaseModel):
    id: str
    tipo: str
    titulo: Optional[str] = None
    modelo: Optional[str] = None
    categoria: Optional[str] = None
    arquivada: bool = False
    criado_em: Optional[datetime] = None
    atualizado_em: Optional[datetime] = None


class SessionUpdate(BaseModel):
    titulo: Optional[str] = None
    arquivada: Optional[bool] = None


class MessageInfo(BaseModel):
    id: str
    role: str
    conteudo: str
    modelo: Optional[str] = None
    categoria: Optional[str] = None
    criado_em: Optional[datetime] = None


class SessionDetail(SessionInfo):
    mensagens: list[MessageInfo] = Field(default_factory=list)


class MemoryCreate(BaseModel):
    chave: str = Field(..., description="Rótulo do fato. Ex.: 'disciplina', 'serie'.")
    valor: str = Field(..., description="Conteúdo a lembrar. Ex.: 'Matemática, 6º ano'.")


class MemoryInfo(BaseModel):
    id: str
    chave: str
    valor: str
    origem: Optional[str] = None
    criado_em: Optional[datetime] = None
    atualizado_em: Optional[datetime] = None


# --- OpenAI-compatible (para VS Code / Continue / extensões estilo Cursor) ---


class OpenAIMessage(BaseModel):
    role: str
    content: Any


class OpenAIChatRequest(BaseModel):
    model: Optional[str] = None
    messages: list[OpenAIMessage]
    temperature: float = 0.7
    stream: bool = False
    max_tokens: Optional[int] = None
