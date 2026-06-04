-- ==========================================================
-- Cache semântico de prompts/respostas (all-MiniLM-L6-v2, 384 dims)
-- Aplicar: psql ... -f sql/003_cache_embeddings.sql
-- ==========================================================

CREATE TABLE IF NOT EXISTS cache_respostas (
    id            UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    prompt        TEXT         NOT NULL,
    prompt_norm   TEXT         NOT NULL UNIQUE,
    embedding     JSONB        NOT NULL,
    resposta      TEXT         NOT NULL,
    categoria     TEXT,
    modelo        TEXT,
    usar_web      BOOLEAN      NOT NULL DEFAULT false,
    fontes        JSONB        NOT NULL DEFAULT '[]'::jsonb,
    hits          INT          NOT NULL DEFAULT 0,
    criado_em     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    ultimo_hit    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_cache_respostas_norm ON cache_respostas (prompt_norm);
CREATE INDEX IF NOT EXISTS idx_cache_respostas_categoria ON cache_respostas (categoria);
CREATE INDEX IF NOT EXISTS idx_cache_respostas_criado ON cache_respostas (criado_em DESC);
