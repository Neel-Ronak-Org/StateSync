# T1: ONDC becomes a dataset profile; neutral core prompts; titled citations

Parent spec: `docs/specs/routing_docs_fixes.md` (§1b O1–O8, fixes F-O and F-X1). Base commit:
`bac6d38`. You implement; the lead reviews by running the checks below and probing past them.

## Why

The normaliser, rewriter and answer writer tell the model it serves "an ONDC logistics network" on
**every** question, whatever the dataset. olist and live (an e-commerce corpus and a simulated shop)
also get ONDC's SQL framing and the DIGIPIN resolver. ONDC is now one *profile*, used only for the ONDC
sample dataset (`public`). Every other dataset gets neutral prompts.

## Requirements

1. **New module `orchestrator/profiles.py`.**

   ```python
   @dataclass(frozen=True)
   class DatasetProfile:
       name: str                          # "ondc" | "neutral"
       normalizer_prompt: str
       rewrite_prompt: str
       synthesizer_prompt: str
       synthesizer_short_prompt: str      # keeps its {max_sentences} placeholder
       sql_domain: str                    # the phrase framed into the SQL writer and corrector prompts
       router_prompt: str | None          # None: the general router prompt in nodes/supervisor.py
       decomposer_prompt: str | None      # None: the general decomposer prompt
       spatial_resolver: bool             # DIGIPIN
       party_id_columns: tuple[str, ...]  # hybrid documents half narrows by these columns

   def profile_for(dataset: str | None) -> DatasetProfile: ...
   ```

   - It returns the ONDC profile for datasets in a new config list, `[profiles] ondc_schemas = ["public"]`
     in `config/statesync.toml`, and the neutral profile for everything else: `olist`, `live`, an
     owner's own database (`cn_…`) and `None`.
   - An owner's own database keeps today's SQL domain, "business analytics system over the owner's own
     PostgreSQL database" (`datasets._OWN_DOMAIN`).
   - The other neutral datasets use "business analytics system over a PostgreSQL database".

2. **New module `orchestrator/profiles_ondc.py`.** It holds the ONDC texts, moved byte-identical from:
   - `language_normalizer._SYSTEM_PROMPT`;
   - `nodes/rewrite._SYSTEM_PROMPT`;
   - `nodes/synthesizer._SYSTEM_PROMPT` and `_SYSTEM_PROMPT_SHORT`;
   - `nodes/supervisor._SYSTEM_PROMPT` (the ONDC router prompt);
   - `nodes/unstructured_knowledge._DECOMPOSER_SYSTEM_PROMPT`;
   - `datasets._SYNTHETIC_DOMAIN`.

   It also sets `spatial_resolver=True` and `party_id_columns=("seller_id", "provider_id")`.

3. **Neutral texts in the core modules.** These are what the neutral profile returns. Each is the
   current text with only these edits, and every other byte unchanged:

   - **Normaliser.**
     - Replace `You are a language-detection and query-normalization step for an ONDC\nlogistics
       analytics system.` with `You are a language-detection and query-normalization step for a
       data\nanalytics assistant.`
     - Replace `- Any proper noun -- a seller/store name, city name, product name,\n  logistics provider
       name, or any other specific named entity -- must be` with `- Any proper noun -- a person,
       company, store, product or place name,\n  or any other specific named entity -- must be`.
     - The worked example stays.
   - **Rewriter.** Replace `, for an ONDC logistics analytics system.` with `, for a data analytics
     assistant.`
   - **Writer, both the full and short prompts.** Replace `You are a data analytics assistant for an
     ONDC logistics network. Answer \` with `You are a data analytics assistant. Answer \`.

4. **Every dataset-dependent choice goes through `profile_for(target_schema)`:**
   - the normaliser, rewriter and both writer prompts;
   - the SQL writer and corrector framing, which replaces `datasets.frame_prompt` and
     `synthetic_features`;
   - DIGIPIN, only when `profile.spatial_resolver`;
   - `supervisor.router_prompt`;
   - `unstructured_knowledge.decomposer_prompt`;
   - `_extract_party_ids`, which uses `profile.party_id_columns`. The neutral profile has none, so it
     falls through to the two-round search.

   Remove `synthetic_schemas` and `ondc_prompt_schemas` from config and code. Keep `document_schemas`;
   it is unrelated. `target_schema` is already in the state at every one of these nodes; check each.

5. **F-X1.** A7's citation item accepts an optional `"title": {"type": "string", "maxLength":
   cfg.library.title_max_chars}`. Nothing else in A7 changes.

## Acceptance (the lead runs these; write them as tests where marked)

- **A1, `tests/test_profiles.py`, new.**
  - `profile_for("public").name == "ondc"`. `"olist"`, `"live"`, `"cn_abc"` and `None` give
    `"neutral"`.
  - The ONDC profile's texts hash to the base commit's (sha256 of the UTF-8 text):

    | Text | sha256 |
    |---|---|
    | normalizer | `cc746390c5053070832009f8f01958518f8c657ce14f8de4f452730276ced267` |
    | rewrite | `9b018aaf1b8bc6c07c2bb22c5e21bad567bf19396e748d774351f445b25d9123` |
    | synthesizer | `b57ed5d1223cba42a456e00c72449f485eca7ef92df07c83ccc53214840c7d4d` |
    | synthesizer_short | `2c2bbe5b7bf626d0ed37ff8be04cd39dc162df29af80ca12ed38f2c2c0b49c7e` |
    | router | `027c94ed89dbfc379425d256c2cba8ba5eb9b1c145d761fa37d6343b4e164a02` |
    | decomposer | `a640f89c98185c74e523ef6044d9607a15a9b5737a82c3ccb305b4032116394b` |
    | sql_domain | `77433c871ddf08717aa8e8dde8d6106a4f17b82ab94d0efb3e8fda819730ec05` |

  - No neutral text contains, case-insensitively, `ondc`, `logistic`, `digipin` or `fulfil`. That
    covers the normaliser, rewriter, both writer prompts, both SQL domains, the general router prompt
    and the general decomposer prompt.
  - The neutral texts equal the base texts with exactly the edits in requirement 3. Assert this by
    applying the replacements to the ONDC texts.
  - DIGIPIN is not called for `olist` or `live`, using a stub.
  - `_extract_party_ids` returns `[]` under the neutral profile even when a `seller_id` column exists.
- **A2, A7 tests.** A chunk citation with `title` validates. An unknown extra key still fails.
- **A3.** `/opt/anaconda3/bin/python3 -m pytest -q tests` passes. Also run the tests the host skips in
  a throwaway venv (`openai pytest fastapi httpx pydantic ruff`). In your report, list every existing
  test you changed and why.
- **A4.** `PATH=/opt/anaconda3/bin:$PATH scripts/lint-no-literals.sh` and `ruff check orchestrator mcp
  tests` pass.

## Rules

- **Allowed:** `orchestrator/`, `config/statesync.toml`, `tests/`.
- **Not allowed:** `config/flags.toml`, `mcp/`, `web/`, `scripts/`, `docs/eval/`, Docker.
- Never `git stash`, never push, never touch another branch. Commit once on your worktree branch with
  a message starting `T1:`.
- Code style: match the surrounding code (comment density, naming). Every numeric literal is config or
  carries `# noqa: literals - <reason>`.
- Report the files changed, the tests added or changed, the commands you ran with their output tails,
  and anything you were unsure of.
