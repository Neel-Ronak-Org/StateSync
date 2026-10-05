"""
tests/test_profiles.py
Dataset profiles (orchestrator/profiles.py, profiles_ondc.py) and the A7 citation title.

WHY THIS EXISTS. ONDC used to be written into every prompt of the core path: the normaliser,
rewriter and answer writer told the model it served an ONDC logistics network on every question,
and olist and live also got ONDC's SQL framing and the DIGIPIN resolver. ONDC is now one profile,
used only for the ONDC sample (`public`); everything else gets neutral prompts. These tests pin
both halves: the ONDC texts are byte-identical to the ones the sample was measured with (sha256),
and the neutral texts are those texts with only the intended phrases replaced.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))

import language_normalizer  # noqa: E402
import nodes.rewrite as rewrite_mod  # noqa: E402
from config_loader import cfg  # noqa: E402
from nodes import sql_agent, supervisor, synthesizer  # noqa: E402
from nodes import unstructured_knowledge as uk  # noqa: E402
from profiles import profile_for  # noqa: E402
from schemas.synthesizer_output import validate_synthesizer_output  # noqa: E402

OWN = "cn_0123456789abcdef"
NEUTRAL_DATASETS = ("olist", "live", OWN, None, "something_else")

# sha256 of each ONDC text at the base commit bac6d38 (UTF-8).
ONDC_HASHES = {
    "normalizer_prompt": "cc746390c5053070832009f8f01958518f8c657ce14f8de4f452730276ced267",
    "rewrite_prompt": "9b018aaf1b8bc6c07c2bb22c5e21bad567bf19396e748d774351f445b25d9123",
    "synthesizer_prompt": "b57ed5d1223cba42a456e00c72449f485eca7ef92df07c83ccc53214840c7d4d",
    "synthesizer_short_prompt": "2c2bbe5b7bf626d0ed37ff8be04cd39dc162df29af80ca12ed38f2c2c0b49c7e",
    "router_prompt": "027c94ed89dbfc379425d256c2cba8ba5eb9b1c145d761fa37d6343b4e164a02",
    "decomposer_prompt": "a640f89c98185c74e523ef6044d9607a15a9b5737a82c3ccb305b4032116394b",
    "sql_domain": "77433c871ddf08717aa8e8dde8d6106a4f17b82ab94d0efb3e8fda819730ec05",
}
BANNED = ("ondc", "logistic", "digipin", "fulfil")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _neutral_texts(name: str | None = None) -> dict[str, str]:
    """Every neutral text a question on a non-ONDC dataset can carry."""
    profile = profile_for(name)
    return {
        "normalizer": profile.normalizer_prompt,
        "rewrite": profile.rewrite_prompt,
        "synthesizer": profile.synthesizer_prompt,
        "synthesizer_short": profile.synthesizer_short_prompt,
        "sql_domain": profile.sql_domain,
        "router": supervisor.router_prompt(name),
        "decomposer": uk.decomposer_prompt(name),
    }


# ── A1: which profile ─────────────────────────────────────────────────────────

def test_the_ondc_sample_gets_the_ondc_profile_and_everything_else_the_neutral_one():
    assert profile_for("public").name == "ondc"
    for name in NEUTRAL_DATASETS:
        assert profile_for(name).name == "neutral", name


def test_the_ondc_schemas_come_from_config():
    assert list(cfg.profiles.ondc_schemas) == ["public"]
    assert not hasattr(cfg.pipeline, "synthetic_schemas") and not hasattr(cfg.pipeline, "ondc_prompt_schemas")
    assert list(cfg.pipeline.document_schemas) == ["public", "olist"]


def test_the_profile_is_frozen():
    with pytest.raises(Exception):
        profile_for("public").name = "x"  # type: ignore[misc]


def test_the_ondc_profile_switches():
    profile = profile_for("public")
    assert profile.spatial_resolver is True
    assert profile.party_id_columns == ("seller_id", "provider_id")
    assert profile.router_prompt and profile.decomposer_prompt


def test_the_neutral_profile_switches():
    for name in NEUTRAL_DATASETS:
        profile = profile_for(name)
        assert profile.spatial_resolver is False
        assert profile.party_id_columns == ()
        assert profile.router_prompt is None and profile.decomposer_prompt is None


def test_sql_domain_per_kind_of_dataset():
    assert profile_for(OWN).sql_domain == "business analytics system over the owner's own PostgreSQL database"
    for name in ("olist", "live", None):
        assert profile_for(name).sql_domain == "business analytics system over a PostgreSQL database"


# ── A1: the ONDC texts are the base commit's ──────────────────────────────────

def test_the_ondc_texts_hash_to_the_base_commits():
    profile = profile_for("public")
    for field, expected in ONDC_HASHES.items():
        assert _sha(getattr(profile, field)) == expected, field


# ── A1: the neutral texts ─────────────────────────────────────────────────────

def test_no_neutral_text_names_ondc_logistics_digipin_or_fulfilment():
    for name in NEUTRAL_DATASETS:
        for label, text in _neutral_texts(name).items():
            for word in BANNED:
                assert word not in text.lower(), (name, label, word)


def test_the_neutral_texts_are_the_ondc_texts_with_only_the_listed_edits():
    ondc = profile_for("public")
    neutral = profile_for("olist")

    def edited(text, *pairs):
        for old, new in pairs:
            assert text.count(old) == 1, old
            text = text.replace(old, new)
        return text

    assert neutral.normalizer_prompt == edited(
        ondc.normalizer_prompt,
        ("You are a language-detection and query-normalization step for an ONDC\n"
         "logistics analytics system.",
         "You are a language-detection and query-normalization step for a data\nanalytics assistant."),
        ("- Any proper noun -- a seller/store name, city name, product name,\n"
         "  logistics provider name, or any other specific named entity -- must be",
         "- Any proper noun -- a person, company, store, product or place name,\n"
         "  or any other specific named entity -- must be"),
    )
    assert neutral.rewrite_prompt == edited(
        ondc.rewrite_prompt,
        (", for an ONDC logistics analytics system.", ", for a data analytics assistant."),
    )
    writer = ("You are a data analytics assistant for an ONDC logistics network. Answer ",
              "You are a data analytics assistant. Answer ")
    assert neutral.synthesizer_prompt == edited(ondc.synthesizer_prompt, writer)
    assert neutral.synthesizer_short_prompt == edited(ondc.synthesizer_short_prompt, writer)


def test_the_short_writer_prompt_keeps_its_placeholder():
    for name in ("public", "olist"):
        assert "{max_sentences}" in profile_for(name).synthesizer_short_prompt


def test_the_two_sql_templates_are_written_with_the_ondc_domain():
    """The framing replaces this phrase; a template that drifted from it would keep ONDC for everyone."""
    ondc_domain = profile_for("public").sql_domain
    for template in (sql_agent._SYSTEM_PROMPT, sql_agent._CORRECTION_SYSTEM_PROMPT):
        assert template.count(ondc_domain) == 1


def test_the_router_and_decomposer_prompts_follow_the_profile():
    ondc = profile_for("public")
    assert supervisor.router_prompt("public") == ondc.router_prompt
    assert uk.decomposer_prompt("public") == ondc.decomposer_prompt
    for name in NEUTRAL_DATASETS:
        assert supervisor.router_prompt(name) == supervisor._GENERAL_PROMPT
        assert uk.decomposer_prompt(name) == uk._GENERAL_DECOMPOSER_PROMPT


# ── A1: the prompts each stage really sends ───────────────────────────────────

class _CaptureLLM:
    def __init__(self, text):
        self.text = text
        self.system = None

    async def complete(self, system, messages, max_tokens):
        self.system = system
        return SimpleNamespace(text=self.text, tokens_in=1, tokens_out=1)


def test_the_normaliser_sends_the_datasets_prompt(monkeypatch):
    seen = {}
    for dataset in ("public", "olist"):
        llm = _CaptureLLM('{"detected_language": "en", "confidence": 0.99, "normalized_query": "q", '
                          '"preserved_entities": {}}')
        monkeypatch.setattr(language_normalizer, "get_agent_llm", lambda role, llm=llm: llm)
        asyncio.run(language_normalizer.PromptNormalizer().normalize("q", dataset))
        seen[dataset] = llm.system
    assert seen["public"] == profile_for("public").normalizer_prompt and "ONDC" in seen["public"]
    assert seen["olist"] == profile_for("olist").normalizer_prompt and "ONDC" not in seen["olist"]


def test_the_rewriter_sends_the_datasets_prompt(monkeypatch):
    class _Session:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    async def history(*args, **kwargs):
        return {"turns": [{"turn_index": 1, "rewritten_query": "Q1", "answer_text": "A1"}], "next_turn_index": 2}

    monkeypatch.setattr(rewrite_mod, "MCPSession", _Session)
    monkeypatch.setattr(rewrite_mod, "history_get_recent_turns", history)
    seen = {}
    for dataset in ("public", "olist"):
        llm = _CaptureLLM('{"needs_context": false, "rewritten_query": ""}')
        monkeypatch.setattr(rewrite_mod, "get_agent_llm", lambda role, llm=llm: llm)
        state = {"tenant_id": "t", "principal_id": "p", "session_id": "s", "query_id": "q",
                 "normalized_query": "and by state?", "target_schema": dataset}
        asyncio.run(rewrite_mod.rewrite_node(state))
        seen[dataset] = llm.system
    assert seen["public"] == profile_for("public").rewrite_prompt and "ONDC" in seen["public"]
    assert seen["olist"] == profile_for("olist").rewrite_prompt and "ONDC" not in seen["olist"]


def test_the_writer_sends_the_datasets_prompt_in_both_styles(monkeypatch):
    for style in ("full", "short"):
        for dataset in ("public", "olist"):
            llm = _CaptureLLM('{"answer_text": "ok", "chart": null, "citations": []}')
            monkeypatch.setattr(synthesizer, "get_agent_llm", lambda role, llm=llm: llm)
            state = {"query_id": "q", "raw_query": "how many?", "sql_rows": [{"n": 1}], "sql_columns": ["n"],
                     "executed_sql": "SELECT 1", "target_schema": dataset,
                     "flags_snapshot": {"pipeline.synth_style": style}}
            asyncio.run(synthesizer.synthesizer_node(state))
            profile = profile_for(dataset)
            expected = (synthesizer._short_system_prompt(dataset) if style == "short"
                        else profile.synthesizer_prompt)
            assert llm.system == expected, (style, dataset)
            assert ("ONDC" in llm.system) == (dataset == "public"), (style, dataset)
    assert "{max_sentences}" not in synthesizer._short_system_prompt("public")


# ── A1: DIGIPIN and party ids ─────────────────────────────────────────────────

class _Resolver:
    def __init__(self):
        self.calls = []

    def resolve(self, query):
        self.calls.append(query)
        return {"digipin": "X", "lat": 1, "lon": 2, "predicate_hint": "p"}


def test_digipin_is_not_called_for_olist_or_live(monkeypatch):
    stub = _Resolver()
    monkeypatch.setattr(sql_agent, "get_spatial_resolver", lambda: stub)
    for name in ("olist", "live", OWN, None):
        assert sql_agent._spatial_resolution("orders near 4FP-23K-79L9", name) is None
    assert stub.calls == []


def test_digipin_is_called_for_the_ondc_sample(monkeypatch):
    stub = _Resolver()
    monkeypatch.setattr(sql_agent, "get_spatial_resolver", lambda: stub)
    assert sql_agent._spatial_resolution("orders near 4FP-23K-79L9", "public")["digipin"] == "X"
    assert stub.calls == ["orders near 4FP-23K-79L9"]


ROWS = [{"seller_id": "s2", "provider_id": "p1"}, {"seller_id": "s1", "provider_id": None}]


def test_party_ids_are_empty_under_the_neutral_profile_even_with_a_seller_id_column():
    for name in NEUTRAL_DATASETS:
        assert uk._extract_party_ids(ROWS, ["seller_id", "provider_id"], name) == []


def test_party_ids_follow_the_ondc_columns():
    assert uk._extract_party_ids(ROWS, ["seller_id", "provider_id"], "public") == ["s1", "s2"]
    assert uk._extract_party_ids(ROWS, ["provider_id"], "public") == ["p1"]
    assert uk._extract_party_ids(ROWS, ["city"], "public") == []


# ── A2 (F-X1): the citation title in A7 ───────────────────────────────────────

def _output(citation):
    return {"answer_text": "ok", "chart": None, "citations": [citation]}


def test_a_chunk_citation_with_a_title_validates():
    ok, error = validate_synthesizer_output(
        _output({"kind": "chunk", "ref": "c1", "page": 3, "title": "Supplier agreement › Penalties"}))
    assert ok and error is None


def test_a_citation_without_a_title_still_validates():
    assert validate_synthesizer_output(_output({"kind": "chunk", "ref": "c1", "page": None}))[0]
    assert validate_synthesizer_output(_output({"kind": "sql", "ref": "q1"}))[0]


def test_an_unknown_citation_key_still_fails():
    citation = {"kind": "chunk", "ref": "c1", "title": "T", "source": "x"}
    ok, error = validate_synthesizer_output(_output(citation))
    assert not ok and "source" in error


def test_a_title_must_be_a_string_within_the_configured_length():
    limit = int(cfg.library.title_max_chars)
    assert validate_synthesizer_output(_output({"kind": "chunk", "ref": "c", "title": "t" * limit}))[0]
    assert not validate_synthesizer_output(_output({"kind": "chunk", "ref": "c", "title": "t" * (limit + 1)}))[0]
    assert not validate_synthesizer_output(_output({"kind": "chunk", "ref": "c", "title": 5}))[0]


def test_nothing_else_in_the_citation_schema_changed():
    from schemas.synthesizer_output import SYNTHESIZER_OUTPUT_SCHEMA
    item = copy.deepcopy(SYNTHESIZER_OUTPUT_SCHEMA["properties"]["citations"]["items"])
    properties = item.pop("properties")
    assert properties.pop("title") == {"type": "string", "maxLength": cfg.library.title_max_chars}
    assert properties == {"kind": {"enum": ["sql", "chunk"]}, "ref": {"type": "string"},
                          "page": {"type": ["integer", "null"]}}
    assert item == {"type": "object", "required": ["kind", "ref"], "additionalProperties": False}
