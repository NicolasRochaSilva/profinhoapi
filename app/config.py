"""Configuração central da Profinho API (carregada de variáveis de ambiente)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 7000
    api_title: str = "Profinho API"
    environment: str = "production"
    master_token: str = ""

    # Ollama (host da VPS)
    ollama_base_url: str = "http://host.docker.internal:11434"
    ollama_timeout: int = 600
    # Tempo que um modelo fica na RAM após o último uso (formato Ollama: "5m", "30s", "0").
    # "0" = descarrega imediatamente após cada resposta (máxima economia de RAM).
    ollama_keep_alive: str = "5m"
    # Modelos quentes (roteador, chat, coder, edu): "-1" = RAM até reiniciar o Ollama.
    ollama_quentes_keep_alive: str = "-1"
    # Ao subir a API, pré-carrega os modelos quentes no host.
    ollama_preload_quentes: bool = True
    # Mantém apenas UM modelo pesado na RAM por vez: antes de usar um modelo,
    # descarrega os outros modelos de trabalho que estiverem carregados.
    ollama_modelo_unico: bool = True

    # --- Modelos (um por finalidade) ---
    # Config anterior (pesada — descomente os defaults abaixo e comente a config atual):
    # model_light: str = "llama3.2:1b"
    # model_router: str = "llama3.2:3b"
    # model_chat: str = "llama3.1:8b"
    # model_code: str = "qwen2.5-coder:7b"
    # model_edu: str = "qwen2.5:7b"
    # model_vision: str = "qwen2.5vl:7b"
    #
    # Config atual (leve / velocidade em CPU):
    model_light: str = "llama3.2:1b"
    model_router: str = "qwen2.5:1.5b"
    model_chat: str = "phi3:mini"
    model_code: str = "deepseek-coder:1.3b"
    model_edu: str = "phi3:mini"
    model_vision: str = "qwen2.5vl:7b"

    # PostgreSQL
    postgres_host: str = "92.113.34.26"
    postgres_port: int = 5432
    postgres_db: str = "profinho"
    postgres_user: str = "postgres"
    postgres_password: str = ""

    # SearXNG / Crawl4AI
    searxng_base_url: str = "http://searxng:8080"
    crawl4ai_base_url: str = "http://crawl4ai:11235"
    crawl4ai_token: str = ""
    crawl4ai_max_paginas: int = 4

    # Agente
    agent_workspace: str = "/workspace"
    agent_exec_timeout: int = 120

    # Cache semântico (all-MiniLM-L6-v2)
    cache_enabled: bool = True
    cache_similaridade_min: float = 0.88
    cache_reformat_min: float = 0.95
    cache_max_candidatos: int = 100

    # Limite de tokens gerados (evita respostas infinitas em CPU).
    chat_num_predict: int = 2048
    chat_num_predict_rapido: int = 1200

    # Extração automática de contexto por token (modelo leve)
    context_extract_enabled: bool = True

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def modelos_quentes(self) -> frozenset[str]:
        """Sempre na RAM: ultra-leve, roteador, chat, programação e educação."""
        return frozenset(
            {
                self.model_light,
                self.model_router,
                self.model_chat,
                self.model_code,
                self.model_edu,
            }
        )

    @property
    def modelos_trabalho(self) -> list[str]:
        """Modelos de inferência principal + visão (para modo modelo-único)."""
        return [self.model_chat, self.model_code, self.model_edu, self.model_vision]

    @property
    def categories(self) -> dict[str, str]:
        """Mapeia categoria -> modelo Ollama."""
        return {
            "chat": self.model_chat,
            "programacao": self.model_code,
            "educacao": self.model_edu,
            "imagem": self.model_vision,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
