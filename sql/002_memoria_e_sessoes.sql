-- ==========================================================
-- Profinho API - Migração 002: memória e sessões
-- Aplique em bancos que já rodaram o init.sql antigo.
--   psql "postgresql://postgres:134497Nico%40@92.113.34.26:5432/profinho" -f sql/002_memoria_e_sessoes.sql
-- ==========================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE OR REPLACE FUNCTION set_atualizado_em()
RETURNS TRIGGER AS $$
BEGIN
    NEW.atualizado_em = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TABLE IF NOT EXISTS sessoes (
    id            UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    token_id      UUID         REFERENCES tokens(id) ON DELETE CASCADE,
    tipo          TEXT         NOT NULL DEFAULT 'chat',
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
    FOR EACH ROW EXECUTE FUNCTION set_atualizado_em();

CREATE TABLE IF NOT EXISTS mensagens (
    id          UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    sessao_id   UUID         NOT NULL REFERENCES sessoes(id) ON DELETE CASCADE,
    role        TEXT         NOT NULL,
    conteudo    TEXT         NOT NULL,
    modelo      TEXT,
    categoria   TEXT,
    metadados   JSONB        NOT NULL DEFAULT '{}'::jsonb,
    criado_em   TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_mensagens_sessao ON mensagens (sessao_id, criado_em);

CREATE TABLE IF NOT EXISTS memorias (
    id            UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    token_id      UUID         REFERENCES tokens(id) ON DELETE CASCADE,
    chave         TEXT         NOT NULL,
    valor         TEXT         NOT NULL,
    origem        TEXT,
    criado_em     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    atualizado_em TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (token_id, chave)
);
CREATE INDEX IF NOT EXISTS idx_memorias_token ON memorias (token_id);

DROP TRIGGER IF EXISTS trg_memorias_atualizado_em ON memorias;
CREATE TRIGGER trg_memorias_atualizado_em
    BEFORE UPDATE ON memorias
    FOR EACH ROW EXECUTE FUNCTION set_atualizado_em();

-- Migra dados da tabela antiga 'conversas' (se existir) para o novo formato.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'conversas') THEN
        INSERT INTO sessoes (id, token_id, tipo, titulo, modelo, categoria, criado_em)
        SELECT gen_random_uuid(), c.token_id, 'chat',
               left(c.prompt, 60), c.modelo, c.categoria, c.criado_em
        FROM conversas c;
        -- Observação: as mensagens antigas não são reconstruídas turno a turno;
        -- a tabela 'conversas' permanece intacta para consulta histórica.
    END IF;
END $$;
