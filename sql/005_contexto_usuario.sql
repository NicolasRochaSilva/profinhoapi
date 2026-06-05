-- ==========================================================
-- Contexto persistente por token (classificado pelo modelo leve)
-- Aplicar: psql ... -f sql/005_contexto_usuario.sql
-- ==========================================================

CREATE TABLE IF NOT EXISTS contexto_usuario (
    id            UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    token_id      UUID         NOT NULL REFERENCES tokens(id) ON DELETE CASCADE,
    tipo          TEXT         NOT NULL CHECK (tipo IN ('pessoal', 'preferencia', 'contexto', 'outro')),
    chave         TEXT         NOT NULL,
    valor         TEXT         NOT NULL,
    origem_prompt TEXT,
    confianca     REAL         NOT NULL DEFAULT 0.8,
    criado_em     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    atualizado_em TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (token_id, chave)
);

CREATE INDEX IF NOT EXISTS idx_contexto_usuario_token ON contexto_usuario (token_id);
CREATE INDEX IF NOT EXISTS idx_contexto_usuario_tipo ON contexto_usuario (token_id, tipo);
CREATE INDEX IF NOT EXISTS idx_contexto_usuario_atualizado
    ON contexto_usuario (token_id, atualizado_em DESC);

DROP TRIGGER IF EXISTS trg_contexto_usuario_atualizado_em ON contexto_usuario;
CREATE TRIGGER trg_contexto_usuario_atualizado_em
    BEFORE UPDATE ON contexto_usuario
    FOR EACH ROW EXECUTE FUNCTION set_atualizado_em();
