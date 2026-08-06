"""Authoritative server registry for identity-ladder fixture dispatch."""

from types import MappingProxyType


IDENTITY_FIXTURE_SOURCE_COMMIT = "3604cd059058fb0b6e9864502d78a94bd523a142"
IDENTITY_FIXTURE_SOURCE_PR = "https://github.com/Flux76HQ/App-Guidance/pull/22"
IDENTITY_FIXTURE_SHA256_CRLF = (
    "36688cceaada3ac8a9bc4063196d33183be6fb5286af61a19b923f1a3562141b"
)

# NOTE ON DRIFT. The vendored copy in this repository is *not* a byte-for-byte
# copy of App-Guidance's file right now. Upstream is at 1.4; this copy carries
# 1.2 plus the six `mediatype-*` cases, hence the "1.2+mediatype" version marker.
#
# The gap is the five cases App-Guidance added in 1.3, four of which this server
# genuinely fails: the tier-2 tmdb-id and year vetoes, the GTIN-length validity
# test, and normalizing structural edition markers such as `Box-set member`
# away. Those are four real merge defects with their own fix, deliberately not
# folded into the media-type work so that a merge regression stays attributable
# to one change rather than two. Once they are fixed, re-vendor the upstream
# file wholesale and this marker goes away.

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
