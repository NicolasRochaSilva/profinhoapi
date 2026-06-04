# Profinho API

IA educacional **multi-modelo** para professores e escolas (SaaS), construída em **Python + FastAPI**, exposta na **porta 7000**, rodando em **Docker Compose** e consumindo o **Ollama instalado no host da VPS**.

Faz tudo que o modo agente do Cursor faz: cria arquivos, escreve código, valida a execução, lê documentação na internet e gera código a partir dela. Também entende imagens (comentar / gerar página / OCR) e pode ser usada **dentro do VS Code** (endpoints compatíveis com a OpenAI).

---

## Arquitetura

```
Usuário / VS Code / Blazor
        ↓
   FastAPI (porta 7000)  ──  Swagger em /docs
        ↓
  Roteador llama3.2:3b  (palavras-chave + classificação)
        ↓
 ┌─────────────┬───────────────┬────────────────┬───────────────┐
 │ llama3.1:8b │ qwen2.5-coder │ qwen2.5:7b     │ qwen2.5vl:7b  │
 │   (chat)    │ (programação) │ (educação)     │  (imagem)     │
 └─────────────┴───────────────┴────────────────┴───────────────┘
        ↓ MCP
 ┌──────────┬───────────┬─────────────┬──────────┐
 │ SearXNG  │ Crawl4AI  │ PostgreSQL  │ GitHub   │
 └──────────┴───────────┴─────────────┴──────────┘
```

| Finalidade   | Modelo (Ollama)      | Categoria       |
|--------------|----------------------|-----------------|
| Roteamento   | `llama3.2:3b`        | (decide)        |
| Chat geral   | `llama3.1:8b`        | `chat`          |
| Programação  | `qwen2.5-coder:7b`   | `programacao`   |
| Educação     | `qwen2.5:7b`         | `educacao`      |
| Imagens      | `qwen2.5vl:7b`       | `imagem`        |

O Ollama roda **no host** (fora do Docker). Os containers o acessam via `host.docker.internal` (mapeado com `extra_hosts: host-gateway`).

### Economia de RAM

Com 16 GB de RAM não dá para manter todos os modelos carregados. A API gerencia isso:

- `OLLAMA_KEEP_ALIVE` (padrão `5m`): tempo na RAM após o último uso (`0` = descarrega na hora).
- `OLLAMA_MODELO_UNICO` (padrão `true`): mantém só **1 modelo pesado** na RAM por vez — antes de usar um, descarrega os outros automaticamente.
- `GET /ollama/loaded` mostra o que está na RAM; `POST /ollama/unload` libera um modelo (`?model=`) ou todos.

---

## Estrutura do projeto

```
profinhoapi/
├── app/
│   ├── main.py              # FastAPI + Swagger + routers
│   ├── config.py            # configurações (env)
│   ├── auth.py              # autenticação por token (tabela tokens)
│   ├── database.py          # pool PostgreSQL (asyncpg)
│   ├── ollama_client.py     # cliente do Ollama no host
│   ├── router_model.py      # roteador (llama3.2:3b + keywords)
│   ├── keywords.py          # palavras-chave por modelo
│   ├── schemas.py           # modelos Pydantic
│   ├── routers/             # chat, agent, images, search, openai_compat, health
│   └── services/            # searxng, crawl4ai, mcp, agent, agent_executor
├── sql/init.sql             # tabela `tokens` (ID, Token, Ativo)
├── searxng/settings.yml     # config do SearXNG (JSON habilitado)
├── docker-compose.yml       # stack local
├── docker-compose.vps.yml   # stack VPS multi-ambiente
├── Dockerfile
├── requirements.txt
├── .env.example
└── .github/workflows/deploy.yaml
```

---

## Banco de dados (controle de acesso por token)

Aplique o `sql/init.sql` no PostgreSQL informado:

```bash
psql "postgresql://postgres:134497Nico%40@92.113.34.26:5432/profinho" -f sql/init.sql
# Se o banco já existia (versão antiga), aplique também a migração de memória:
psql "postgresql://postgres:134497Nico%40@92.113.34.26:5432/profinho" -f sql/002_memoria_e_sessoes.sql
```

> A senha tem `@`, que na URL vira `%40`.

Além de `tokens`, o esquema cria a **memória** (estilo ChatGPT/Claude/Cursor):
`sessoes` (conversas), `mensagens` (contexto de cada turno) e `memorias` (fatos de longo prazo por token).

A tabela `tokens` controla quem está liberado:

| coluna  | tipo    | descrição                                    |
|---------|---------|----------------------------------------------|
| `id`    | UUID    | identificador                                |
| `token` | TEXT    | enviado em `Authorization: Bearer <token>`   |
| `ativo` | BOOLEAN | **true** = liberado, **false** = bloqueado   |

Criar um token novo:

```sql
INSERT INTO tokens (token, ativo, professor, dominio)
VALUES ('tok_professor_joao', TRUE, 'João', 'joao.profinho.com.br');
```

Bloquear um usuário: `UPDATE tokens SET ativo = FALSE WHERE token = '...';`

---

## Como rodar

### Pré-requisitos no host (VPS)

```bash
# Ollama no host + modelos
ollama pull llama3.2:3b
ollama pull llama3.1:8b
ollama pull qwen2.5-coder:7b
ollama pull qwen2.5:7b
ollama pull qwen2.5vl:7b

# Ollama precisa escutar em todas as interfaces para o Docker alcançá-lo:
#   /etc/systemd/system/ollama.service.d/override.conf
#   [Service]
#   Environment="OLLAMA_HOST=0.0.0.0:11434"
```

### Local / desenvolvimento

```bash
cp .env.example .env       # ajuste as variáveis
docker compose up -d --build
```

Acesse o **Swagger** em: `http://localhost:7000/docs`

### Produção (VPS)

```bash
# serviços compartilhados (uma vez)
docker compose -f docker-compose.vps.yml up -d searxng crawl4ai
# ambiente principal (porta 7000)
docker compose -f docker-compose.vps.yml up -d --build profinho-main
```

---

## Endpoints principais

Todos exigem `Authorization: Bearer <token>` (exceto `/health` e `/status`).

| Método | Rota                          | Descrição |
|--------|-------------------------------|-----------|
| POST   | `/chat`                       | Chat com seleção automática de modelo (`usar_web` para pesquisar). |
| POST   | `/route`                      | Só decide qual modelo usar. |
| POST   | `/vision`                     | Imagem: `comentar`, `pagina` (gera HTML) ou `ocr`. |
| POST   | `/search`                     | Busca na internet (SearXNG + Crawl4AI). |
| POST   | `/doc-to-code`                | Lê documentação e gera código a partir dela. |
| POST   | `/agent/run`                  | Agente: cria arquivos, escreve código e valida. |
| GET    | `/agent/projects/{p}/files`   | Lista arquivos gerados. |
| GET    | `/mcp/tools`                  | Lista ferramentas MCP. |
| POST   | `/mcp/call/{nome}`            | Chama uma ferramenta MCP. |
| GET/POST | `/sessions`                 | Listar/criar conversas (memória). |
| GET/PATCH/DELETE | `/sessions/{id}`    | Abrir (com mensagens), renomear/arquivar, apagar. |
| GET/POST | `/memory`                   | Listar/salvar memórias de longo prazo. |
| GET    | `/status`                     | Status do Ollama, modelos e banco. |

> Memória de contexto: chame `/chat` uma vez (recebe `sessao_id`); nas próximas, reenvie o mesmo `sessao_id` que a API recarrega o histórico sozinha. Detalhes em `docs/FUNCIONALIDADES.md`.

### Exemplos

Chat (roteamento automático):

```bash
curl -X POST http://localhost:7000/chat \
  -H "Authorization: Bearer profinho-demo-token" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Crie um plano de aula sobre frações para o 6º ano"}'
```

Agente (cria e valida código):

```bash
curl -X POST http://localhost:7000/agent/run \
  -H "Authorization: Bearer profinho-demo-token" \
  -H "Content-Type: application/json" \
  -d '{"instrucao": "Crie uma API FastAPI de tarefas com testes", "projeto": "todo", "validar": true, "usar_web": true}'
```

Imagem → página web:

```bash
curl -X POST http://localhost:7000/vision \
  -H "Authorization: Bearer profinho-demo-token" \
  -F "modo=pagina" -F "arquivo=@layout.png"
```

---

## Usar no VS Code (gerar código como o Cursor)

A API expõe endpoints **compatíveis com a OpenAI** em `/v1`. Configure uma extensão como **Continue** (`~/.continue/config.json`):

```json
{
  "models": [
    {
      "title": "Profinho",
      "provider": "openai",
      "model": "profinho-auto",
      "apiBase": "http://SEU_IP:7000/v1",
      "apiKey": "profinho-demo-token"
    }
  ]
}
```

Modelos expostos: `profinho-auto` (roteia automaticamente), `profinho-chat`, `profinho-coder`, `profinho-edu`, `profinho-vision`.

---

## Deploy automático (GitHub Actions)

`.github/workflows/deploy.yaml` faz deploy por branch via SSH:

| Branch    | Serviço            | Porta |
|-----------|--------------------|-------|
| `main`    | `profinho-main`    | 7000  |
| `develop` | `profinho-develop` | 7001  |
| `demo`    | `profinho-demo`    | 7002  |
| `homolog` | `profinho-homolog` | 7003  |

**Secrets** (Settings → Secrets and variables → Actions):

- `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY` (chave privada).
- `POSTGRES_PASSWORD`, `MASTER_TOKEN` (opcionais; sobrescrevem os defaults).

Cada push para uma branch mapeada empacota o projeto, envia para `/opt/profinho/<ambiente>` e sobe o container correspondente.
