-- ==========================================================
-- Cache semântico de prompts/respostas (all-MiniLM-L6-v2, 384 dims)
-- Escopo por token (cada professor tem seu cache).
-- Aplicar: psql ... -f sql/003_cache_embeddings.sql
-- ==========================================================

CREATE TABLE IF NOT EXISTS cache_respostas (
    id            UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    token_id      UUID         REFERENCES tokens(id) ON DELETE CASCADE,
    token_scope   TEXT         NOT NULL DEFAULT 'GLOBAL',
    prompt        TEXT         NOT NULL,
    prompt_norm   TEXT         NOT NULL,
    embedding     JSONB        NOT NULL,
    resposta      TEXT         NOT NULL,
    categoria     TEXT,
    modelo        TEXT,
    usar_web      BOOLEAN      NOT NULL DEFAULT false,
    fontes        JSONB        NOT NULL DEFAULT '[]'::jsonb,
    hits          INT          NOT NULL DEFAULT 0,
    criado_em     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    ultimo_hit    TIMESTAMPTZ,
    UNIQUE (token_scope, prompt_norm)
);

CREATE INDEX IF NOT EXISTS idx_cache_respostas_token ON cache_respostas (token_id);
CREATE INDEX IF NOT EXISTS idx_cache_respostas_categoria ON cache_respostas (categoria);
CREATE INDEX IF NOT EXISTS idx_cache_respostas_token_criado
    ON cache_respostas (token_scope, criado_em DESC);
