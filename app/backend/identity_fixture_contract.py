"""Authoritative server registry for identity-ladder fixture dispatch."""

from types import MappingProxyType


IDENTITY_FIXTURE_RUNNERS = MappingProxyType(
    {
        "cases": "_run_ladder_cases",
        "merge_winner_cases": "_run_merge_winner_cases",
        "identifier_cases": "_run_identifier_cases",
    }
)

IDENTITY_FIXTURE_METADATA_KEYS = frozenset(
    {"$schema_note", "version", "generated_from"}
)
