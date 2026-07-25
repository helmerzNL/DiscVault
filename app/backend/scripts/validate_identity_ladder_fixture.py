#!/usr/bin/env python3
"""Fail closed when the server identity fixture and dispatcher drift."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys


if __package__ and __package__ != "scripts":
    from ..identity_fixture_contract import IDENTITY_FIXTURE_SHA256_CRLF
    from ..tests.test_identity_ladder_fixture import (
        FIXTURE_PATH,
        IdentityLadderFixtureRunner,
    )
else:  # pragma: no cover - exercised by the CI command
    backend_dir = Path(__file__).resolve().parents[1]
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    from identity_fixture_contract import IDENTITY_FIXTURE_SHA256_CRLF
    from tests.test_identity_ladder_fixture import (
        FIXTURE_PATH,
        IdentityLadderFixtureRunner,
    )


def canonical_crlf_sha256(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    canonical = text.replace("\r\n", "\n").replace("\n", "\r\n")
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, default=FIXTURE_PATH)
    args = parser.parse_args(argv)

    try:
        executed = IdentityLadderFixtureRunner.load(args.fixture).run_all()
        actual_sha256 = canonical_crlf_sha256(args.fixture)
        if actual_sha256 != IDENTITY_FIXTURE_SHA256_CRLF:
            raise AssertionError(
                "Pinned identity fixture SHA-256 mismatch after canonical CRLF "
                f"normalization: expected={IDENTITY_FIXTURE_SHA256_CRLF}, "
                f"actual={actual_sha256}."
            )
    except (
        AssertionError,
        AttributeError,
        json.JSONDecodeError,
        KeyError,
        OSError,
        TypeError,
    ) as exc:
        print(
            f"Identity ladder fixture coverage failed for {args.fixture}: {exc}",
            file=sys.stderr,
        )
        return 1

    counts = ", ".join(
        f"{category}={len(case_ids)}" for category, case_ids in executed.items()
    )
    print(
        "Identity ladder fixture coverage ok: "
        f"{counts}; sha256={IDENTITY_FIXTURE_SHA256_CRLF}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
