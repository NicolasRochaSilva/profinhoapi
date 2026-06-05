-- ==========================================================
-- Profinho API - Esquema de banco (PostgreSQL)
-- Controle de acesso por token.
--   Conexão: Host=92.113.34.26;Port=5432;Database=profinho;
--            Username=postgres;Password=134497Nico@
--
-- Para aplicar:
--   psql "postgresql://postgres:134497Nico%40@92.113.34.26:5432/profinho" -f sql/init.sql
-- (a senha contém '@', que na URL precisa ser %40)
-- ==========================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Tabela de tokens de acesso.
-- ID    -> identificador único (UUID)
-- Token -> chave usada no header Authorization: Bearer <token>
-- Ativo -> true/false controla se o usuário está liberado
CREATE TABLE IF NOT EXISTS tokens (
    id          UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    token       TEXT         NOT NULL UNIQUE,
    ativo       BOOLEAN      NOT NULL DEFAULT TRUE,
    descricao   TEXT,
    professor   TEXT,
    dominio     TEXT,
    tipo_usuario TEXT        NOT NULL DEFAULT 'professor' CHECK (tipo_usuario IN ('professor', 'aluno')),
    criado_em   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tokens_token ON tokens (token);
CREATE INDEX IF NOT EXISTS idx_tokens_ativo ON tokens (ativo);
CREATE INDEX IF NOT EXISTS idx_tokens_tipo_usuario ON tokens (tipo_usuario);

-- Atualiza automaticamente o campo atualizado_em.
CREATE OR REPLACE FUNCTION set_atualizado_em()
RETURNS TRIGGER AS $$
BEGIN
    NEW.atualizado_em = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_tokens_atualizado_em ON tokens;
CREATE TRIGGER trg_tokens_atualizado_em
    BEFORE UPDATE ON tokens
    FOR EACH ROW
    EXECUTE FUNCTION set_atualizado_em();

-- ==========================================================
-- MEMÓRIA / HISTÓRICO DE CONVERSAS (estilo ChatGPT / Claude / Cursor)
--   sessoes   -> cada conversa/thread (chat, agente, visão, busca)
--   mensagens -> cada turno da conversa (user/assistant/system/tool)
--   memorias  -> fatos persistentes por token (memória de longo prazo)
-- ==========================================================

-- Uma sessão é uma "conversa" (thread), como no ChatGPT.
CREATE TABLE IF NOT EXISTS sessoes (
    id            UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    token_id      UUID         REFERENCES tokens(id) ON DELETE CASCADE,
    tipo          TEXT         NOT NULL DEFAULT 'chat',   -- chat | agente | vision | busca
    titulo        TEXT,
    modelo        TEXT,
    categoria     TEXT,
    arquivada     BOOLEAN      NOT NULL DEFAULT FALSE,
    metadados     JSONB        NOT NULL DEFAULT '{}'::jsonb,
    criado_em     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    atualizado_em TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sessoes_token ON sessoes (token_id);
CREATE INDEX IF NOT EXISTS idx_sessoes_atualizado ON sessoes (atualizado_em DESC);

DROP TRIGGER IF EXISTS trg_sessoes_atualizado_em ON sessoes;
CREATE TRIGGER trg_sessoes_atualizado_em
    BEFORE UPDATE ON sessoes
    FOR EACH ROW
    EXECUTE FUNCTION set_atualizado_em();

-- Cada mensagem de uma sessão (memória de curto prazo / contexto da conversa).
CREATE TABLE IF NOT EXISTS mensagens (
    id          UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    sessao_id   UUID         NOT NULL REFERENCES sessoes(id) ON DELETE CASCADE,
    role        TEXT         NOT NULL,                    -- system | user | assistant | tool
    conteudo    TEXT         NOT NULL,
    modelo      TEXT,
    categoria   TEXT,
    metadados   JSONB        NOT NULL DEFAULT '{}'::jsonb,
    criado_em   TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_mensagens_sessao ON mensagens (sessao_id, criado_em);

-- Memória de longo prazo: fatos lembrados por token/professor (estilo "memory" do ChatGPT).
CREATE TABLE IF NOT EXISTS memorias (
    id            UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    token_id      UUID         REFERENCES tokens(id) ON DELETE CASCADE,
    chave         TEXT         NOT NULL,
    valor         TEXT         NOT NULL,
    origem        TEXT,                                   -- ex.: 'manual', 'sessao:<id>'
    criado_em     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    atualizado_em TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (token_id, chave)
);

CREATE INDEX IF NOT EXISTS idx_memorias_token ON memorias (token_id);

DROP TRIGGER IF EXISTS trg_memorias_atualizado_em ON memorias;
CREATE TRIGGER trg_memorias_atualizado_em
    BEFORE UPDATE ON memorias
    FOR EACH ROW
    EXECUTE FUNCTION set_atualizado_em();

-- Contexto classificado por token (extraído pelo modelo leve ou manual).
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

DROP TRIGGER IF EXISTS trg_contexto_usuario_atualizado_em ON contexto_usuario;
CREATE TRIGGER trg_contexto_usuario_atualizado_em
    BEFORE UPDATE ON contexto_usuario
    FOR EACH ROW
    EXECUTE FUNCTION set_atualizado_em();

-- Token de demonstração: Bearer = SHA-256 hex (mesmo valor da coluna token).
-- (derivado de "profinho-demo-token" só para gerar o hash; não envie esse texto na API)
INSERT INTO tokens (token, ativo, descricao, professor)
VALUES (
    'd2b48181af2062762f8d48d83effdf09b16ec31fa3c54eb078f48bfc74d75576',
    TRUE,
    'Token de demonstração (SHA-256 hex)',
    'admin'
)
ON CONFLICT (token) DO NOTHING;


