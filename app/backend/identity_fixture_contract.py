"""Authoritative server registry for identity-ladder fixture dispatch."""

from types import MappingProxyType


IDENTITY_FIXTURE_SOURCE_COMMIT = "f9b22b88ddb3fadd043b4eada33ed8b0c64edd43"
IDENTITY_FIXTURE_SOURCE_PR = "https://github.com/Flux76HQ/App-Guidance/pull/22"
IDENTITY_FIXTURE_SHA256_CRLF = (
    "6adc13a87272bfec621c6d98c1e96d2acb34d51384c058d20cabbbf64a236d4c"
)

IDENTITY_FIXTURE_RUNNERS = MappingProxyType(
    {
        "cases": "_run_ladder_cases",
        "identifier_cases": "_run_identifier_cases",
        "merge_winner_cases": "_run_merge_winner_cases",
        "container_cases": "_run_container_cases",
        "container_merge_winner_cases": "_run_container_merge_winner_cases",
    }
)

IDENTITY_FIXTURE_METADATA_KEYS = frozenset(
    {"$schema_note", "version", "generated_from"}
)
