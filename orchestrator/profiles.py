"""
orchestrator/profiles.py
Which prompts and switches a dataset gets (routing_docs_fixes.md F-O).

The prompts in the core modules are neutral: they name no dataset. The ONDC sample (the datasets
in [profiles].ondc_schemas, `public` as shipped) is the one exception, a *profile* of its own
(profiles_ondc.py) holding the texts it was written and measured with, its DIGIPIN resolver and
its party-id columns. Every other dataset (olist, live, an owner's own database, no dataset at
all) gets the neutral profile. Everything that depends on the dataset asks profile_for(target_schema)
and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import profiles_ondc
from config_loader import cfg
from datasets import _NEUTRAL_DOMAIN, _OWN_DOMAIN, is_connection_dataset


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


def _ondc_profile() -> DatasetProfile:
    return DatasetProfile(
        name="ondc",
        normalizer_prompt=profiles_ondc.NORMALIZER_PROMPT,
        rewrite_prompt=profiles_ondc.REWRITE_PROMPT,
        synthesizer_prompt=profiles_ondc.SYNTHESIZER_PROMPT,
        synthesizer_short_prompt=profiles_ondc.SYNTHESIZER_SHORT_PROMPT,
        sql_domain=profiles_ondc.SQL_DOMAIN,
        router_prompt=profiles_ondc.ROUTER_PROMPT,
        decomposer_prompt=profiles_ondc.DECOMPOSER_PROMPT,
        spatial_resolver=profiles_ondc.SPATIAL_RESOLVER,
        party_id_columns=profiles_ondc.PARTY_ID_COLUMNS,
    )


def _neutral_profile() -> DatasetProfile:
    # The neutral texts live in the core modules, which themselves ask for a profile, so they are
    # imported here, when one is asked for, not at module load.
    import language_normalizer
    from nodes import rewrite, synthesizer
    return DatasetProfile(
        name="neutral",
        normalizer_prompt=language_normalizer._SYSTEM_PROMPT,
        rewrite_prompt=rewrite._SYSTEM_PROMPT,
        synthesizer_prompt=synthesizer._SYSTEM_PROMPT,
        synthesizer_short_prompt=synthesizer._SYSTEM_PROMPT_SHORT,
        sql_domain=_NEUTRAL_DOMAIN,
        router_prompt=None,
        decomposer_prompt=None,
        spatial_resolver=False,
        party_id_columns=(),
    )


def profile_for(dataset: str | None) -> DatasetProfile:
    """The ONDC profile for the datasets in [profiles].ondc_schemas, the neutral one for every
    other dataset. An owner's own database keeps its own SQL domain phrase."""
    if isinstance(dataset, str) and dataset in list(cfg.profiles.ondc_schemas):
        return _ondc_profile()
    profile = _neutral_profile()
    if is_connection_dataset(dataset):
        return replace(profile, sql_domain=_OWN_DOMAIN)
    return profile
