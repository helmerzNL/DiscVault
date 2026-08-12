"""Token scopes must cover what the server tells its own clients to do.

This is the preparation half of the API-token scoping work. Nothing is enforced
yet -- a token's keys are still merely added to its holder's role -- so none of
these assertions change behaviour today. They exist because the moment the two
sets have to *agree*, a scope that is too narrow stops being invisible and
starts being a 403 on a route the server itself advertises.

The check that matters is therefore not "does the tuple contain key X" but
"does every route in the advertised native contract have a key the native token
carries". That question is answered against the routing table in the source, so
a route whose gate is later tightened fails here rather than in the field.
"""

import ast
import pathlib
import re
import unittest


BACKEND = pathlib.Path(__file__).resolve().parents[1]


def _tuple_literal(source: str, name: str) -> tuple[str, ...]:
    """Read a module-level tuple of string literals without importing."""
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return tuple(
                element.value
                for element in node.value.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            )
    raise AssertionError(f"{name} not found")


AUTH_SOURCE = (BACKEND / "next_auth.py").read_text(encoding="utf-8")
TOKEN_SOURCE = (BACKEND / "next_api_token.py").read_text(encoding="utf-8")

MOBILE = set(_tuple_literal(AUTH_SOURCE, "MOBILE_AUTH_TOKEN_PERMISSIONS"))
REVIEW = set(_tuple_literal(AUTH_SOURCE, "REVIEW_LOGIN_TOKEN_PERMISSIONS"))
GRANTABLE = set(_tuple_literal(TOKEN_SOURCE, "API_TOKEN_GRANTABLE_PERMISSION_KEYS"))

# What `assign_role(conn, reviewer["id"], "media_viewer")` in review_login gives
# the reviewer, per migration 007. A review token wider than this carries keys
# its own role does not hold.
MEDIA_VIEWER_ROLE = {
    "collection.view",
    "containers.view",
    "groups.view",
    "watchlist.manage",
    "lending.request",
}


def _route_permission_index() -> dict[tuple[str, str], set[str]]:
    """Map (method, path) to the permission keys that satisfy its gate.

    Only routes behind require_next_permission / require_any_next_permission
    appear; a route with no permission gate imposes no requirement on a token.
    """
    route_re = re.compile(r'@\w+\.(get|post|put|patch|delete)\(\s*"([^"]+)"')
    perm_re = re.compile(
        r'require_(?:any_)?next_permission\(\s*\w+\s*,\s*(\([^)]*\)|"[^"]+")', re.S
    )
    index: dict[tuple[str, str], set[str]] = {}
    for path in BACKEND.glob("next_*.py"):
        source = path.read_text(encoding="utf-8")
        matches = list(route_re.finditer(source))
        for position, match in enumerate(matches):
            end = (
                matches[position + 1].start()
                if position + 1 < len(matches)
                else min(len(source), match.end() + 6000)
            )
            gate = perm_re.search(source[match.end() : end])
            if not gate:
                continue
            keys = set(re.findall(r'"([a-z][a-z0-9_.]*)"', gate.group(1)))
            if keys:
                index[(match.group(1).upper(), match.group(2))] = keys
    return index


ROUTES = _route_permission_index()

# Every route `mobile_endpoint_contract_payload` advertises to a native client,
# as (method, path). Routes the contract names but that carry no permission gate
# -- the sync snapshot, the profile, preferences -- are absent from ROUTES and
# so impose nothing here.
ADVERTISED_CONTRACT = [
    ("GET", "/api/next/locations"),
    ("GET", "/api/next/locations/<location_id>/qr.svg"),
    ("GET", "/api/next/locations/<location_id>/qr.png"),
    ("POST", "/api/next/metadata/lookup"),
    ("POST", "/api/next/import/movie"),
    ("POST", "/api/next/movies/<movie_id>/metadata/refresh"),
    ("POST", "/api/next/containers/<container_id>/metadata/refresh"),
    ("GET", "/api/next/metadata/jobs"),
    ("GET", "/api/next/loans"),
    ("GET", "/api/next/loans/borrowed"),
    ("POST", "/api/next/loans/<loan_id>/return"),
    ("POST", "/api/next/movies/<movie_id>/loan-requests"),
    ("GET", "/api/next/loan-requests"),
    ("POST", "/api/next/loan-requests/<request_id>/approve"),
    ("POST", "/api/next/loan-requests/<request_id>/decline"),
    ("POST", "/api/next/loan-requests/<request_id>/cancel"),
    ("GET", "/api/next/sync/user/bootstrap"),
    ("GET", "/api/next/sync/user/delta"),
    ("GET", "/api/next/stats/personal"),
]


class AdvertisedContractIsReachableTests(unittest.TestCase):
    """The native token must satisfy every gate the contract sends it through."""

    def test_the_contract_routes_all_exist(self):
        # A renamed route would otherwise make the coverage test below pass by
        # having nothing to check.
        missing = [entry for entry in ADVERTISED_CONTRACT if entry not in ROUTES]
        self.assertEqual(missing, [], "advertised routes no longer found or no longer gated")

    def test_the_native_token_satisfies_every_advertised_gate(self):
        unreachable = {
            f"{method} {path}": sorted(ROUTES[(method, path)])
            for method, path in ADVERTISED_CONTRACT
            if not (ROUTES[(method, path)] & MOBILE)
        }
        self.assertEqual(
            unreachable,
            {},
            "the server advertises these routes to native clients but the token cannot pass them",
        )


class NativeTokenCoherenceTests(unittest.TestCase):
    def test_a_token_that_may_write_the_collection_may_also_read_it(self):
        writes = {"collection.add", "collection.add_own", "collection.edit_all", "collection.import"}
        if MOBILE & writes:
            self.assertIn(
                "collection.view",
                MOBILE,
                "the tuple grants collection writes; without the read key it describes "
                "a client allowed to change a movie it may not fetch",
            )

    def test_every_native_key_is_one_a_user_could_also_grant_by_hand(self):
        # Not a security property -- it is a consistency one. A key the server
        # issues to its own client but refuses in the token UI means the two
        # paths disagree about what a bearer token may be.
        self.assertEqual(
            sorted(MOBILE - GRANTABLE),
            [],
            "the native token carries keys the profile UI refuses to grant",
        )


class ReviewTokenTests(unittest.TestCase):
    def test_the_review_token_covers_what_its_role_is_for(self):
        # media_viewer exists so an account can "view group media and use
        # personal watchlist or borrowing features". The token was missing both
        # halves of the second clause.
        for key in ("watchlist.manage", "lending.request"):
            self.assertIn(key, REVIEW, f"the reviewer's role grants {key}; the token must too")

    def test_the_review_token_grants_nothing_its_role_lacks_beyond_metadata(self):
        # review_login assigns exactly media_viewer, and unlike the native flow
        # the review issuance does not intersect with the role. Anything here
        # outside the role is authority the token adds on its own; the metadata
        # keys are the pre-existing set, deliberately left rather than narrowed.
        beyond = REVIEW - MEDIA_VIEWER_ROLE
        self.assertEqual(
            sorted(beyond),
            ["api.read", "metadata.refresh_bulk", "metadata.refresh_one", "metadata.search"],
            "the review token gained a key its assigned role does not hold",
        )


class GrantableSurfaceTests(unittest.TestCase):
    """What a user may put on a token they create themselves."""

    def test_the_personal_library_is_reachable_by_token(self):
        for key in ("watchlist.manage", "lending.request"):
            self.assertIn(key, GRANTABLE)

    def test_administrative_authority_stays_out_of_bearer_tokens(self):
        forbidden_prefixes = ("security.", "digital_sources.", "metadata.manage_")
        forbidden_exact = {
            "admin.backup",
            "admin.restore_functional",
            "admin.view_audit",
            "admin.view_settings",
            "collection.delete_all",
            "collection.delete_own",
            "collection.delete_group",
            "collection.export_functional",
            "containers.delete",
            "groups.create",
            "groups.invite",
        }
        offenders = sorted(
            key
            for key in GRANTABLE
            if key in forbidden_exact or key.startswith(forbidden_prefixes)
        )
        self.assertEqual(offenders, [], "these do not belong in a bearer token")

    def test_admin_view_jobs_is_the_one_deliberate_admin_exception(self):
        # It is read-only and it is what the metadata job list needs; the rest
        # of admin.* is excluded above. Pinned so the exception stays a decision
        # rather than an oversight.
        admin_keys = sorted(key for key in GRANTABLE if key.startswith("admin."))
        self.assertEqual(admin_keys, ["admin.view_jobs"])


class ScopeLabelTests(unittest.TestCase):
    def test_the_new_keys_produce_a_scope_label(self):
        # The scopes column is what the profile page shows for a token. A key
        # that maps to nothing renders a token with real access as scope-less.
        source = TOKEN_SOURCE[TOKEN_SOURCE.index("def api_token_scopes_for_permissions") :]
        body = source[: source.index("\n\n\n")] if "\n\n\n" in source else source
        for key in ("watchlist.manage", "lending.request"):
            self.assertGreaterEqual(
                body.count(f'"{key}"'), 1, f"{key} maps to no scope label"
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
