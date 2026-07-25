"""Authoritative server registry for identity-ladder fixture dispatch."""

from types import MappingProxyType


IDENTITY_FIXTURE_SOURCE_COMMIT = "fb78a04cdc1908757becf88e3a551da9ff7c7ffe"
IDENTITY_FIXTURE_SOURCE_PR = "https://github.com/Flux76HQ/App-Guidance/pull/17"
IDENTITY_FIXTURE_SHA256_CRLF = (
    "afa4f3c00877cce61fb4237e4ccf0a93f99c3149adab8b6b2466365182b550d6"
)

IDENTITY_FIXTURE_RUNNERS = MappingProxyType(
    {
        "cases": "_run_ladder_cases",
        "identifier_cases": "_run_identifier_cases",
        "merge_winner_cases": "_run_merge_winner_cases",
    }
)

IDENTITY_FIXTURE_METADATA_KEYS = frozenset(
    {"$schema_note", "version", "generated_from"}
)
