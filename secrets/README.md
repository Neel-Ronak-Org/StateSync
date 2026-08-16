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

# Anthropic API key
echo "sk-ant-..." > secrets/anthropic_api_key
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
