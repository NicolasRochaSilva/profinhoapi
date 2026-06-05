-- ==========================================================
-- Cache: escopo por token (professor)
-- Aplicar após 003: psql ... -f sql/004_cache_por_token.sql
-- ==========================================================

ALTER TABLE cache_respostas
    ADD COLUMN IF NOT EXISTS token_id UUID REFERENCES tokens(id) ON DELETE CASCADE;

ALTER TABLE cache_respostas
    ADD COLUMN IF NOT EXISTS token_scope TEXT NOT NULL DEFAULT 'GLOBAL';

UPDATE cache_respostas
SET token_scope = token_id::text
WHERE token_id IS NOT NULL AND token_scope = 'GLOBAL';

-- UNIQUE em prompt_norm vira constraint (não índice solto) na 003 antiga.
ALTER TABLE cache_respostas DROP CONSTRAINT IF EXISTS cache_respostas_prompt_norm_key;
DROP INDEX IF EXISTS idx_cache_respostas_norm;
DROP INDEX IF EXISTS idx_cache_respostas_token_prompt;

ALTER TABLE cache_respostas DROP CONSTRAINT IF EXISTS cache_respostas_token_scope_prompt_norm_key;
ALTER TABLE cache_respostas
    ADD CONSTRAINT cache_respostas_token_scope_prompt_norm_key
    UNIQUE (token_scope, prompt_norm);

CREATE INDEX IF NOT EXISTS idx_cache_respostas_token ON cache_respostas (token_id);
CREATE INDEX IF NOT EXISTS idx_cache_respostas_token_criado
    ON cache_respostas (token_scope, criado_em DESC);
