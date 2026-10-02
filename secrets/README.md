## secrets/ — Docker secret files for local development

**NEVER commit real credentials to this directory.**

This directory is listed in `.gitignore`.

### Files needed before running `docker compose up`:

```bash
# Create the secrets directory and placeholder files
mkdir -p secrets

# Postgres migrator password (used as POSTGRES_PASSWORD in docker-compose.yml)
echo "changeme_migrator" > secrets/pg_migrator_password

# Postgres statesync_reader password
echo "changeme_reader" > secrets/pg_reader_password

# Postgres statesync_ingest password (for seeding)
echo "changeme_ingest" > secrets/pg_ingest_password

# Postgres statesync_history password (cycle 007: session_state.conversation_turns
# only -- migrations/011). Mounted into the mcp container; never held by the
# orchestrator (A1).
echo "changeme_history" > secrets/pg_history_password

# Anthropic API key
echo "sk-ant-..." > secrets/anthropic_api_key

# Encryption key for the passwords of your own connected databases
# (mcp/connections.py, migration 026). Mounted into the mcp container only.
# Never regenerate it once connections exist: their passwords become unreadable.
python3 scripts/make_connection_key.py

# Shared secret between the orchestrator and the MCP (mcp/service_auth.py).
# Mounted into those two containers only; the MCP refuses to start without it
# and refuses every /rpc call that does not carry it. Rotate by writing a new
# value and recreating both services together.
openssl rand -hex 32 > secrets/mcp_token

# The evaluator's own credentials (it is off by default, but compose needs the files).
# Its MCP token may only run gold SELECTs on built-in datasets; its API principal is
# registered with the orchestrator (restart the orchestrator after adding it).
openssl rand -hex 32 > secrets/mcp_token_evaluator
python3 scripts/gen_api_principal.py --principal-id p_evaluator --append \
    --token-file secrets/api_token_evaluator > /dev/null
```

### In production:
Replace file-based secrets with Docker Swarm secrets or a secrets manager
(e.g., HashiCorp Vault). The application reads from `/run/secrets/<name>` in
Docker; the path is configurable via `<NAME>_FILE` environment variables.

### Azure OpenAI / AI Foundry (BD1 seam, provider = "azure")

```bash
# Azure OpenAI API key — Foundry portal → your deployment → Key
echo "<your-azure-key>" > secrets/azure_openai_api_key
```

The **endpoint** and **api_version** are NOT secrets and do not go here — they
live in `config/flags.toml` under `[llm.azure]`, where the config-discipline
checks can see them.

A placeholder file is fine while `llm.provider = "local"`: the key is read only
when the azure provider is actually selected, exactly like the Anthropic key.
Verify the wiring without a key at all:

```bash
python3 scripts/verify_azure_seam.py
```

### Google Gemini (BD1 seam, provider = "gemini")

```bash
# Free key from Google AI Studio: aistudio.google.com/apikey
echo "<your-gemini-key>" > secrets/gemini_api_key
```

No endpoint or version to configure — Gemini addresses models by published
name. Set `provider = "gemini"` in `config/flags.toml` and you are done.

**The free tier is rate-limited per MINUTE.** A full fixture run issues ~5.2
LLM calls per query, so 429s mid-run are expected, not exceptional; the
adapter retries with exponential backoff (`models.gemini_max_retries`).

Verify the wiring with no key at all, then one live call once you have one:

```bash
python3 scripts/verify_provider_seam.py --provider gemini
docker compose exec -T orchestrator python3 - --provider gemini --smoke < scripts/verify_provider_seam.py
```

### Orchestrator API Authentication & Principals (Workstream C1)

The orchestrator requires bearer-token authentication on protected routes
(`POST /query`, `GET /sessions`, `GET /sessions/{id}`). It loads authorized
principals from `secrets/api_principals.json` (mounted into the container as
`/run/secrets/api_principals`).

The orchestrator **fails to start** if `secrets/api_principals.json` is missing,
empty, or malformed.

- `secrets/api_principals.json`: JSON array of `{principal_id, tenant_id, token_sha256}`.
  Holds **only** SHA-256 hex digests of bearer tokens. Plaintext tokens are never stored here.
- `secrets/api_token_dev`: The raw bearer token for local development and test
  scripts (written with mode 0600), read automatically by `scripts/api_auth.py`.

Generate both files using the operator utility:

```bash
python3 scripts/gen_api_principal.py
```

To append an additional principal to an existing principals file:

```bash
python3 scripts/gen_api_principal.py --principal-id p_eval --append
```

The orchestrator reads the principal file **once at startup**. If you generate a
new principal while the stack is running, restart the orchestrator:

```bash
docker compose restart orchestrator
```

### Groq (BD1 seam, provider = "groq" — the v3 default)

```bash
# Free key from console.groq.com → API Keys
printf '%s' '<your-groq-key>' > secrets/groq_api_key
chmod 600 secrets/groq_api_key
```

`config/flags.toml` ships `provider = "groq"`. Without a key the orchestrator
refuses to start and says so; to run fully offline instead, set
`provider = "local"` and `ollama pull qwen2.5:7b`.
