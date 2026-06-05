-- ==========================================================
-- Tipo de usuário por token (professor | aluno)
-- Aplicar: psql ... -f sql/006_tipo_usuario.sql
-- ==========================================================

ALTER TABLE tokens
    ADD COLUMN IF NOT EXISTS tipo_usuario TEXT NOT NULL DEFAULT 'professor';

ALTER TABLE tokens DROP CONSTRAINT IF EXISTS tokens_tipo_usuario_check;
ALTER TABLE tokens
    ADD CONSTRAINT tokens_tipo_usuario_check
    CHECK (tipo_usuario IN ('professor', 'aluno'));

CREATE INDEX IF NOT EXISTS idx_tokens_tipo_usuario ON tokens (tipo_usuario);

-- Exemplo: marcar token existente como aluno
-- UPDATE tokens SET tipo_usuario = 'aluno', descricao = 'Aluno 6A' WHERE professor = 'Maria';
