# Profinho API — Documentação de Funcionalidades e Endpoints

Documentação técnica completa da **Profinho API**: o que cada funcionalidade faz, como cada endpoint funciona (entrada/saída) e **receitas de combinação** para reutilizar em outros projetos (front-end, Blazor, VS Code, bots, automações, etc.).

- **Base URL:** `http://SEU_IP:7000`
- **Swagger interativo:** `http://SEU_IP:7000/docs`
- **OpenAPI JSON:** `http://SEU_IP:7000/openapi.json`
- **Base OpenAI-compatible:** `http://SEU_IP:7000/v1`

---

## Sumário

1. [Autenticação](#1-autenticação)
2. [Conceitos: roteamento e modelos](#2-conceitos-roteamento-e-modelos)
3. [Endpoints de status](#3-endpoints-de-status)
4. [Chat e roteamento](#4-chat-e-roteamento)
5. [Visão (imagens)](#5-visão-imagens)
6. [Pesquisa e documentação → código](#6-pesquisa-e-documentação--código)
7. [Agente estilo Cursor](#7-agente-estilo-cursor)
8. [MCP (ferramentas)](#8-mcp-ferramentas)
9. [Compatível com OpenAI (VS Code)](#9-compatível-com-openai-vs-code)
10. [Memória: sessões e longo prazo](#10-memória-sessões-e-longo-prazo)
11. [Receitas de combinação](#11-receitas-de-combinação)
12. [Códigos de erro](#12-códigos-de-erro)
13. [Referência rápida (tabela)](#13-referência-rápida-tabela)

---

## 1. Autenticação

Todos os endpoints (exceto `/`, `/health`, `/status`) exigem um **token Bearer**:

```
Authorization: Bearer <SEU_TOKEN>
```

O token é validado na tabela `tokens` do PostgreSQL. Regras:

- Token **inexistente** → `401 Unauthorized`.
- Token com `ativo = false` → `403 Forbidden`.
- Token com `ativo = true` → liberado.
- Existe ainda o `MASTER_TOKEN` (variável de ambiente), sempre aceito — útil para administração.

> É a coluna `ativo` da tabela `tokens` que controla se o usuário está liberado. Para bloquear alguém: `UPDATE tokens SET ativo = false WHERE token = '...';`

---

## 2. Conceitos: roteamento e modelos

A API tem **5 modelos** servidos pelo Ollama (no host), cada um com uma finalidade:

| Categoria      | Modelo             | Para quê |
|----------------|--------------------|----------|
| `chat`         | `llama3.1:8b`      | Conversa geral, atendimento, perguntas. |
| `programacao`  | `qwen2.5-coder:7b` | Código, APIs, SQL, ASP.NET, Python, HTML/CSS/JS. |
| `educacao`     | `qwen2.5:7b`       | Planos de aula, exercícios, provas, resumos. |
| `imagem`       | `qwen2.5vl:7b`     | Analisar imagens, OCR, gerar página a partir de imagem. |
| *(roteador)*   | `llama3.2:3b`      | **Decide** qual das categorias acima usar. |

**Como o roteador decide** (função `rotear`):

1. Se houver **imagem** → vai direto para `imagem`.
2. **Palavras-chave** (heurística rápida, sem inferência): se o texto bate em ≥ 2 palavras de uma categoria, usa essa.
3. Caso contrário, o **`llama3.2:3b`** classifica o texto em uma das 4 categorias.
4. *Fallback*: `chat`.

Você pode **forçar** a categoria em `/chat` passando o campo `categoria`, ou **ver a decisão** sem gastar inferência grande usando `/route`.

---

## 3. Endpoints de status

### `GET /` — Raiz (público)
Retorna metadados básicos.
```json
{ "nome": "Profinho API", "docs": "/docs", "openai_base_url": "/v1", "porta": 7000 }
```

### `GET /health` — Healthcheck (público)
```json
{ "status": "ok", "servico": "Profinho API" }
```

### Gerenciamento de RAM (descarregar modelos)

A VPS tem RAM limitada, então a API **não deixa todos os modelos carregados**:

- **`keep_alive`** (`OLLAMA_KEEP_ALIVE`, padrão `5m`): tempo que um modelo fica na RAM após o último uso. Passado o tempo ocioso, o Ollama o descarrega sozinho. Use `0` para descarregar imediatamente após cada resposta.
- **Modelo único** (`OLLAMA_MODELO_UNICO`, padrão `true`): antes de usar um modelo pesado (chat/coder/edu/vision), a API **descarrega os outros modelos pesados** que estiverem na RAM — garantindo só 1 por vez. O roteador `llama3.2:3b` (leve) não é derrubado.

| Método | Rota | Auth | Descrição |
|--------|------|------|-----------|
| GET    | `/ollama/loaded` | sim | Lista os modelos atualmente na RAM (`/api/ps`). |
| POST   | `/ollama/unload?model=<nome>` | sim | Descarrega um modelo específico. |
| POST   | `/ollama/unload` | sim | Descarrega **todos** os modelos da RAM. |

```bash
# ver o que está ocupando RAM
curl http://SEU_IP:7000/ollama/loaded -H "Authorization: Bearer SEU_TOKEN"
# liberar tudo
curl -X POST http://SEU_IP:7000/ollama/unload -H "Authorization: Bearer SEU_TOKEN"
```

O `GET /status` também mostra `modelos_na_ram`, o `keep_alive` e se o modo `modelo_unico` está ativo.

### `GET /status` — Status detalhado (público)
Verifica Ollama (online + modelos instalados + carregados na RAM), modelos configurados e PostgreSQL.
```json
{
  "api": "ok",
  "ambiente": "production",
  "ollama": {
    "online": true,
    "base_url": "http://host.docker.internal:11434",
    "modelos_instalados": ["llama3.1:8b", "qwen2.5-coder:7b", "..."],
    "modelos_configurados": { "roteador": "llama3.2:3b", "chat": "llama3.1:8b", "...": "..." }
  },
  "postgres": { "online": true, "host": "92.113.34.26" }
}
```
> Use este endpoint em monitoramento/uptime checks e para confirmar que os 5 modelos estão baixados no host.

---

## 4. Chat e roteamento

### `POST /route` — Só decide o modelo
Classifica o texto **sem** gerar resposta longa (rápido e barato).

**Request**
```json
{ "prompt": "Crie uma função em Python que soma dois números" }
```

**Response** (`RouteResponse`)
```json
{
  "categoria": "programacao",
  "modelo": "qwen2.5-coder:7b",
  "motivo": "Palavras-chave (3) indicaram 'programacao'."
}
```

### `POST /chat` — Chat com seleção automática de modelo
O coração da API. Roteia (ou usa a categoria forçada), opcionalmente pesquisa na web, e responde.

**Request** (`ChatRequest`)

| Campo         | Tipo     | Padrão | Descrição |
|---------------|----------|--------|-----------|
| `prompt`      | string   | —      | **Obrigatório.** Mensagem do usuário. |
| `categoria`   | string?  | `null` | Força `chat`/`programacao`/`educacao`/`imagem`. Se `null`, o roteador decide. |
| `historico`   | array    | `[]`   | Mensagens anteriores `{ "role": "user|assistant|system", "content": "..." }`. |
| `system`      | string?  | `null` | Instrução de sistema personalizada (sobrescreve a padrão da categoria). |
| `temperature` | float    | `0.7`  | Criatividade. |
| `usar_web`    | bool     | `false`| Se `true`, pesquisa no SearXNG + lê páginas com Crawl4AI antes de responder. |
| `sessao_id`   | string?  | `null` | ID da conversa. Se `null`, uma nova é criada e devolvida na resposta. |
| `salvar`      | bool     | `true` | Se `true`, grava a conversa na memória (sessão + mensagens). |

**Response** (`ChatResponse`)
```json
{
  "categoria": "educacao",
  "modelo": "qwen2.5:7b",
  "resposta": "Plano de aula sobre frações...",
  "motivo_roteamento": "Roteador llama3.2:3b classificou como 'educacao'.",
  "fontes": ["https://..."],
  "sessao_id": "8f3c...-uuid"
}
```
> `fontes` só vem preenchido quando `usar_web = true`. Quando `salvar = true`, a API mantém **memória de contexto**: na próxima chamada, basta reenviar o mesmo `sessao_id` (sem precisar mandar `historico`) que ela recarrega o histórico do banco automaticamente. Veja a [seção 10](#10-memória-sessões-e-longo-prazo).

**Exemplo com histórico (memória de conversa):**
```json
{
  "prompt": "E para o 7º ano?",
  "categoria": "educacao",
  "historico": [
    { "role": "user", "content": "Faça um plano de aula de frações para o 6º ano" },
    { "role": "assistant", "content": "Aqui está o plano..." }
  ]
}
```

---

## 5. Visão (imagens)

### `POST /vision` — Analisar imagem
**`multipart/form-data`** (upload de arquivo). Usa o `qwen2.5vl:7b`.

| Campo     | Tipo  | Padrão      | Descrição |
|-----------|-------|-------------|-----------|
| `arquivo` | file  | —           | **Obrigatório.** A imagem (png, jpg, etc.). |
| `prompt`  | texto | `""`        | Instrução adicional opcional. |
| `modo`    | texto | `comentar`  | `comentar`, `pagina` ou `ocr`. |

**Modos:**
- `comentar` — descreve e comenta a imagem (elementos, contexto, transcreve texto).
- `ocr` — extrai **todo o texto** da imagem (prints de erro, exercícios fotografados, etc.).
- `pagina` — **gera uma página web** (HTML+CSS+JS num único arquivo) que reproduz o layout da imagem.

**Response**
```json
{ "modo": "pagina", "modelo": "qwen2.5vl:7b", "arquivo": "layout.png", "resultado": "<!DOCTYPE html>..." }
```

**Exemplo (cURL):**
```bash
curl -X POST http://SEU_IP:7000/vision \
  -H "Authorization: Bearer SEU_TOKEN" \
  -F "modo=pagina" \
  -F "prompt=Use cores escuras e layout responsivo" \
  -F "arquivo=@./mockup.png"
```

---

## 6. Pesquisa e documentação → código

### `POST /search` — Buscar na internet
Busca no **SearXNG** e (opcional) lê o conteúdo das páginas com **Crawl4AI**.

**Request** (`SearchRequest`)
```json
{ "query": "documentação FastAPI dependency injection", "max_resultados": 5, "ler_conteudo": true }
```

**Response** (`SearchResponse`)
```json
{
  "query": "...",
  "resultados": [
    { "titulo": "...", "url": "https://...", "resumo": "...", "conteudo": "markdown limpo da página ou null" }
  ]
}
```
> `conteudo` só vem se `ler_conteudo = true`.

### `POST /doc-to-code` — Documentação vira código
Busca documentação, lê as páginas e pede ao `qwen2.5-coder:7b` para gerar código **baseado nela**.

**Request** (`DocCodeRequest`)

| Campo        | Tipo    | Padrão | Descrição |
|--------------|---------|--------|-----------|
| `objetivo`   | string  | —      | **Obrigatório.** O que você quer construir. |
| `query_doc`  | string? | `null` | Busca de doc específica. Se vazio, usa o `objetivo`. |
| `max_fontes` | int     | `3`    | Quantas páginas ler. |

**Response**
```json
{
  "objetivo": "...",
  "modelo": "qwen2.5-coder:7b",
  "fontes": ["https://...", "https://..."],
  "codigo": "..."
}
```

---

## 7. Agente estilo Cursor

Faz o que o **modo agente do Cursor** faz: cria arquivos, escreve código e **valida executando** num workspace isolado (sandbox em disco, confinado a `/workspace`).

### `POST /agent/run` — Executar tarefa
**Fluxo interno:** (1) opcionalmente pesquisa documentação na web; (2) pede ao `qwen2.5-coder:7b` um plano de arquivos em JSON; (3) escreve os arquivos; (4) roda um comando de validação; (5) se falhar, devolve o erro ao modelo e **itera** (auto-correção).

**Request** (`AgentRunRequest`)

| Campo           | Tipo    | Padrão     | Descrição |
|-----------------|---------|------------|-----------|
| `instrucao`     | string  | —          | **Obrigatório.** A tarefa. |
| `projeto`       | string? | `"projeto"`| Pasta do projeto no workspace. |
| `validar`       | bool    | `true`     | Executa o comando de validação após gerar. |
| `usar_web`      | bool    | `false`    | Pesquisa documentação antes de gerar. |
| `max_iteracoes` | int     | `4`        | Tentativas de auto-correção. |

**Response** (`AgentRunResponse`)
```json
{
  "projeto": "todo",
  "resumo": "API FastAPI de tarefas com testes.",
  "arquivos": [
    { "path": "main.py", "acao": "criado", "bytes": 1234 },
    { "path": "test_main.py", "acao": "criado", "bytes": 456 }
  ],
  "validacao": { "comando": "python -m py_compile main.py", "exit_code": 0, "stdout": "", "stderr": "", "sucesso": true },
  "fontes": []
}
```

### `GET /agent/projects/{projeto}/files` — Listar arquivos gerados
```json
{ "projeto": "todo", "arquivos": ["main.py", "test_main.py", "requirements.txt"] }
```

### `GET /agent/projects/{projeto}/file?path=main.py` — Ler um arquivo
```json
{ "path": "main.py", "conteudo": "from fastapi import FastAPI\n..." }
```

### `POST /agent/projects/{projeto}/exec?comando=...` — Executar comando
Roda um comando shell dentro do projeto (validação/testes). `comando` vai na **query string**.
```json
{ "comando": "python -m pytest", "exit_code": 0, "stdout": "...", "stderr": "", "sucesso": true }
```

---

## 8. MCP (ferramentas)

Camada padronizada (Model Context Protocol simplificado) que expõe ferramentas reutilizáveis: `IA → MCP → { SearXNG, Crawl4AI, PostgreSQL, GitHub, APIs }`.

### `GET /mcp/tools` — Listar ferramentas
```json
{ "tools": [ { "name": "web_search", "description": "...", "parameters": { "...": "..." } } ] }
```

### `POST /mcp/call/{nome}` — Chamar uma ferramenta
O corpo é o objeto de **argumentos** da ferramenta.

| Tool          | Argumentos                              | O que faz |
|---------------|-----------------------------------------|-----------|
| `web_search`  | `{ "query": "...", "max_resultados": 5 }` | Busca no SearXNG. |
| `read_url`    | `{ "url": "...", "max_chars": 12000 }`    | Lê página com Crawl4AI (markdown limpo). |
| `db_query`    | `{ "sql": "SELECT ..." }`                 | Consulta **somente SELECT** no PostgreSQL. |
| `github`      | `{ "path": "repos/owner/repo/readme" }`   | API pública do GitHub. |
| `http_get`    | `{ "url": "...", "params": { } }`         | GET genérico a uma API externa. |

**Exemplo:**
```bash
curl -X POST http://SEU_IP:7000/mcp/call/web_search \
  -H "Authorization: Bearer SEU_TOKEN" -H "Content-Type: application/json" \
  -d '{ "query": "novidades python 3.13", "max_resultados": 3 }'
```

---

## 9. Compatível com OpenAI (VS Code)

Para usar a Profinho **dentro do VS Code** (extensões como Continue) ou em qualquer SDK da OpenAI.

- **Base URL:** `http://SEU_IP:7000/v1`
- **API key:** o seu token da tabela `tokens`.

### `GET /v1/models`
Lista os "modelos" expostos: `profinho-auto` (roteia automaticamente), `profinho-chat`, `profinho-coder`, `profinho-edu`, `profinho-vision`.

### `POST /v1/chat/completions`
Formato idêntico ao da OpenAI. Suporta `stream: true`.

**Request**
```json
{
  "model": "profinho-auto",
  "messages": [
    { "role": "system", "content": "Você é um assistente de código." },
    { "role": "user", "content": "Escreva um quicksort em Python" }
  ],
  "temperature": 0.3,
  "stream": false
}
```

**Response** (formato OpenAI)
```json
{
  "id": "chatcmpl-...",
  "object": "chat.completion",
  "model": "qwen2.5-coder:7b",
  "choices": [ { "index": 0, "message": { "role": "assistant", "content": "def quicksort(...)" }, "finish_reason": "stop" } ],
  "usage": { "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0 }
}
```

**Configuração no Continue (`~/.continue/config.json`):**
```json
{
  "models": [
    { "title": "Profinho", "provider": "openai", "model": "profinho-auto",
      "apiBase": "http://SEU_IP:7000/v1", "apiKey": "SEU_TOKEN" }
  ]
}
```

**Uso com o SDK oficial da OpenAI (Python):**
```python
from openai import OpenAI
client = OpenAI(base_url="http://SEU_IP:7000/v1", api_key="SEU_TOKEN")
r = client.chat.completions.create(
    model="profinho-coder",
    messages=[{"role": "user", "content": "Gere uma classe Repository em C#"}],
)
print(r.choices[0].message.content)
```

---

## 10. Memória: sessões e longo prazo

A API tem memória persistente como ChatGPT/Claude/Cursor, em **três camadas**:

| Camada | Tabela | O que guarda | Escopo |
|--------|--------|--------------|--------|
| **Sessão** (thread) | `sessoes` | uma conversa (chat ou agente) | por token |
| **Curto prazo** (contexto) | `mensagens` | cada turno user/assistant da sessão | por sessão |
| **Longo prazo** (memory) | `memorias` | fatos que valem em todas as conversas | por token |

**Como funciona o contexto:** ao chamar `/chat` com um `sessao_id`, a API monta automaticamente o prompt assim:
```
system (+ memórias de longo prazo do token) → últimas ~20 mensagens da sessão → nova pergunta
```
Ou seja, você **não precisa reenviar o histórico** — só o `sessao_id`. As memórias de longo prazo são injetadas no `system` em toda conversa.

### Sessões / conversas

| Método | Rota | Descrição |
|--------|------|-----------|
| POST   | `/sessions` | Cria uma conversa. Body: `{ "tipo": "chat|agente|vision|busca", "titulo": "opcional" }`. |
| GET    | `/sessions` | Lista conversas (query: `tipo`, `incluir_arquivadas`). |
| GET    | `/sessions/{id}` | Abre a conversa **com todas as mensagens**. |
| PATCH  | `/sessions/{id}` | Renomeia/arquiva. Body: `{ "titulo": "...", "arquivada": true }`. |
| DELETE | `/sessions/{id}` | Apaga a conversa (e suas mensagens). |

`GET /sessions` retorna:
```json
[
  { "id": "uuid", "tipo": "chat", "titulo": "Plano de aula de frações",
    "modelo": "qwen2.5:7b", "categoria": "educacao", "arquivada": false,
    "criado_em": "2026-05-30T22:00:00Z", "atualizado_em": "2026-05-30T22:10:00Z" }
]
```

`GET /sessions/{id}` adiciona o array `mensagens` (`{ id, role, conteudo, modelo, categoria, criado_em }`) — pronto para renderizar uma tela de chat.

### Memória de longo prazo (fatos do usuário)

| Método | Rota | Descrição |
|--------|------|-----------|
| GET    | `/memory` | Lista as memórias do token. |
| POST   | `/memory` | Salva/atualiza. Body: `{ "chave": "disciplina", "valor": "Matemática, 6º ano" }`. |
| DELETE | `/memory/{id}` | Apaga uma memória. |

A `chave` é única por token (salvar de novo **atualiza**). Essas memórias entram no `system` de **todas** as conversas daquele token. Útil para o SaaS lembrar perfil do professor, disciplina, série, estilo de resposta, etc.

> Observação: a memória de longo prazo exige um **token de banco** (não funciona com o `MASTER_TOKEN`, que não tem `id` na tabela).

**Exemplo — conversa contínua com memória:**
```bash
# 1ª mensagem (sem sessao_id -> cria a conversa)
curl -X POST http://SEU_IP:7000/chat -H "Authorization: Bearer SEU_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"prompt":"Sou professor de matemática do 6º ano"}'
# resposta -> { ..., "sessao_id": "ABC" }

# guarda um fato de longo prazo
curl -X POST http://SEU_IP:7000/memory -H "Authorization: Bearer SEU_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"chave":"perfil","valor":"Professor de matemática, 6º ano, gosta de exemplos do cotidiano"}'

# 2ª mensagem reusando a MESMA sessão (recarrega o contexto sozinho)
curl -X POST http://SEU_IP:7000/chat -H "Authorization: Bearer SEU_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"prompt":"Crie um exercício sobre o que falamos","sessao_id":"ABC"}'
```

---

## 11. Receitas de combinação

Como **encadear** endpoints para fluxos completos. Reutilize estes padrões em outros projetos.

### Receita A — Chat com conhecimento atualizado
Pergunta que precisa de info recente → `/chat` com `usar_web: true` (faz busca + leitura automaticamente, sem você orquestrar nada).
```
POST /chat  { "prompt": "Qual a versão estável atual do .NET e o que mudou?", "usar_web": true }
```

### Receita B — "Pesquisar → entender → codar" (controle fino)
Quando você quer controlar cada etapa:
1. `POST /search` → escolhe as melhores URLs.
2. `POST /mcp/call/read_url` → lê as páginas específicas que você selecionou.
3. `POST /chat` (categoria `programacao`) passando o conteúdo lido no `prompt`.

> Alternativa "one-shot": `POST /doc-to-code` faz busca + leitura + geração de código de uma vez.

### Receita C — Imagem vira site publicado pelo agente
1. `POST /vision` com `modo: "pagina"` → recebe o HTML gerado a partir do mockup.
2. `POST /agent/run` com a instrução "Crie um projeto com este index.html e um servidor estático, e valide" (cole o HTML na instrução).
3. `GET /agent/projects/{projeto}/files` + `GET .../file` → baixa os arquivos finais.

### Receita D — Print de erro → correção de código
1. `POST /vision` com `modo: "ocr"` → extrai a stacktrace do print.
2. `POST /chat` (categoria `programacao`) com "Corrija este erro: <texto do OCR> no seguinte código: ...".

### Receita E — Material didático completo
1. `POST /chat` (categoria `educacao`) → "Crie um plano de aula sobre fotossíntese para o 8º ano".
2. `POST /chat` (categoria `educacao`, com `historico`) → "Agora gere 10 exercícios e um gabarito sobre o mesmo tema".

### Receita F — Geração de projeto guiada por documentação
```
POST /agent/run {
  "instrucao": "Crie uma API REST com autenticação JWT usando FastAPI",
  "projeto": "auth-api",
  "usar_web": true,
  "validar": true
}
```
O agente pesquisa a doc, gera os arquivos, roda a validação e se autocorrige.

### Receita G — Roteamento como serviço
Use `POST /route` para classificar a intenção de uma mensagem (ex.: triagem de tickets, chatbot multi-skill) **sem** gerar resposta — barato e rápido.

### Receita H — Interface de chat com histórico (estilo ChatGPT)
1. `GET /sessions` → lista lateral de conversas.
2. `GET /sessions/{id}` → carrega as mensagens da conversa selecionada.
3. `POST /chat` com `sessao_id` → continua a conversa (contexto automático).
4. `PATCH /sessions/{id}` → renomeia; `DELETE` → apaga.

---

## 12. Códigos de erro

| Código | Significado | Quando ocorre |
|--------|-------------|---------------|
| `200`  | OK          | Sucesso. |
| `400`  | Bad Request | `modo` de imagem inválido; argumento MCP inválido; SQL não-SELECT em `db_query`. |
| `401`  | Unauthorized| Token ausente ou inexistente. |
| `403`  | Forbidden   | Token com `ativo = false`. |
| `404`  | Not Found   | Arquivo do projeto não existe; tool MCP inexistente. |
| `422`  | Unprocessable | Corpo JSON com campos inválidos (validação Pydantic). |
| `500`  | Server Error| Falha no Ollama, SearXNG, Crawl4AI ou banco. Veja `/status`. |

---

## 13. Referência rápida (tabela)

| Método | Rota | Auth | Corpo | Resumo |
|--------|------|------|-------|--------|
| GET    | `/`                              | não | — | Metadados. |
| GET    | `/health`                        | não | — | Healthcheck. |
| GET    | `/status`                        | não | — | Status de Ollama/modelos/RAM/banco. |
| GET    | `/ollama/loaded`                 | sim | — | Modelos carregados na RAM. |
| POST   | `/ollama/unload`                 | sim | query `model?` | Descarrega modelo(s) da RAM. |
| POST   | `/route`                         | sim | `ChatRequest` | Decide o modelo. |
| POST   | `/chat`                          | sim | `ChatRequest` | Chat com roteamento (+web opcional). |
| POST   | `/vision`                        | sim | multipart | Imagem: comentar/pagina/ocr. |
| POST   | `/search`                        | sim | `SearchRequest` | Busca web (+leitura). |
| POST   | `/doc-to-code`                   | sim | `DocCodeRequest` | Doc → código. |
| POST   | `/agent/run`                     | sim | `AgentRunRequest` | Agente cria+valida código. |
| GET    | `/agent/projects/{p}/files`      | sim | — | Lista arquivos. |
| GET    | `/agent/projects/{p}/file`       | sim | query `path` | Lê arquivo. |
| POST   | `/agent/projects/{p}/exec`       | sim | query `comando` | Executa comando. |
| GET    | `/mcp/tools`                     | sim | — | Lista ferramentas MCP. |
| POST   | `/mcp/call/{nome}`               | sim | args (JSON) | Chama ferramenta MCP. |
| POST   | `/sessions`                      | sim | `SessionCreate` | Cria conversa. |
| GET    | `/sessions`                      | sim | — | Lista conversas. |
| GET    | `/sessions/{id}`                 | sim | — | Abre conversa com mensagens. |
| PATCH  | `/sessions/{id}`                 | sim | `SessionUpdate` | Renomeia/arquiva. |
| DELETE | `/sessions/{id}`                 | sim | — | Apaga conversa. |
| GET    | `/memory`                        | sim | — | Lista memórias de longo prazo. |
| POST   | `/memory`                        | sim | `MemoryCreate` | Salva/atualiza memória. |
| DELETE | `/memory/{id}`                   | sim | — | Apaga memória. |
| GET    | `/v1/models`                     | sim | — | Modelos (OpenAI-compat). |
| POST   | `/v1/chat/completions`           | sim | `OpenAIChatRequest` | Chat (OpenAI-compat). |

---

> Dica: a fonte da verdade sempre atualizada é o **`/openapi.json`** — você pode gerar SDKs automaticamente (openapi-generator, NSwag para C#/Blazor, etc.) a partir dele para consumir a Profinho em qualquer projeto.
