"""The transport and job half of a field correction.

Separated from `test_next_movievault_v2_field_corrections_postgres.py`, which
tests what a correction *contains*. What is tested here is how it travels and
what happens afterwards: that the envelope MovieVault receives is
contribution-2 rather than contribution-1 wearing a different entity type, that
the second gate is genuinely a second decision, and that the status poll gives
up instead of asking about a moderation queue forever.
"""

import json
import os
import sys
import unittest
from unittest.mock import patch

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend import next_movievault_v2_contributions as contributions

_ORIGIN_PATCH = patch.dict(os.environ, {"MOVIEVAULT_V2_ORIGIN": "https://movievault.example"})


def setUpModule():
    _ORIGIN_PATCH.start()


def tearDownModule():
    _ORIGIN_PATCH.stop()


CORRECTION = {
    "target": {"entityType": "release", "entityId": "10000000-0000-0000-0000-000000000001", "baseRevision": 40},
    "changes": [{"field": "edition", "expected": "Theatrical", "proposed": "Director's Cut"}],
}


class _Sent:
    """Captures one submission without a network or a database."""

    def __init__(self):
        self.url = None
        self.body = None
        self.headers = None

    def __call__(self, url, *, body, headers, timeout_seconds):
        self.url = url
        self.body = body
        self.headers = headers
        return 202, json.dumps({"contributionId": "c-1", "status": "pending"}).encode()


def submit(correction=None):
    private_pem, _ = contributions._generate_key_pair()
    sent = _Sent()
    values = {
        contributions.TOKEN_KEY: "dvt_token",
        contributions.KEY_ID_KEY: "9f0d0000-0000-0000-0000-00000000000a",
        contributions.PRIVATE_KEY_KEY: private_pem,
    }
    with patch.object(contributions, "ensure_registration", lambda conn, **k: {"registered": True}), patch.object(
        contributions, "_setting_value", lambda conn, key, default=None, **k: values.get(key, default)
    ), patch.object(contributions, "_decrypt_secret_value", lambda value: value), patch.object(
        contributions, "_http", sent
    ):
        result = contributions.submit_field_correction(object(), correction or CORRECTION)
    return sent, result


class EnvelopeTests(unittest.TestCase):
    def test_the_envelope_declares_contribution_2(self):
        """The protocol version is what makes MovieVault read `target` and
        `changes` at all; a contribution-1 envelope carrying them is rejected."""
        sent, _ = submit()
        envelope = json.loads(sent.body)
        self.assertEqual(envelope["protocolVersion"], "contribution-2")
        self.assertEqual(envelope["entityType"], "release")
        self.assertEqual(envelope["target"]["baseRevision"], 40)
        self.assertEqual(envelope["changes"], CORRECTION["changes"])

    def test_the_entity_type_is_not_repeated_inside_the_target(self):
        """`entityType` belongs to the envelope. Sending it twice invites two
        answers to one question the moment a caller sets only one of them."""
        envelope = json.loads(submit()[0].body)
        self.assertNotIn("entityType", envelope["target"])

    def test_a_box_set_travels_under_its_own_entity_type(self):
        correction = {
            "target": {"entityType": "box_set", "entityId": "20000000-0000-0000-0000-000000000002", "baseRevision": 7},
            "changes": [{"field": "title", "expected": "Old", "proposed": "New"}],
        }
        envelope = json.loads(submit(correction)[0].body)
        self.assertEqual(envelope["entityType"], "box_set")
        self.assertEqual(envelope["sourceReference"]["type"], "discvault_box_set")

    def test_the_same_correction_twice_is_one_contribution(self):
        """Pressing Upload twice, or a retry after a timeout, must collapse
        rather than put the same diff in front of a moderator twice."""
        first = json.loads(submit()[0].body)["idempotencyKey"]
        second = json.loads(submit()[0].body)["idempotencyKey"]
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("dv-fc2"))
        # A release contribution keyed the same way would collide with this one.
        self.assertNotEqual(first, contributions.idempotency_key(CORRECTION))

    def test_a_different_proposal_is_a_different_contribution(self):
        other = {
            "target": CORRECTION["target"],
            "changes": [{"field": "edition", "expected": "Theatrical", "proposed": "Extended"}],
        }
        self.assertNotEqual(
            json.loads(submit()[0].body)["idempotencyKey"],
            json.loads(submit(other)[0].body)["idempotencyKey"],
        )

    def test_the_source_reference_names_no_local_id(self):
        """It is not secret, but a readable id here would let the moderation
        queue be joined against a catalogue by anyone who saw both."""
        reference = json.loads(submit()[0].body)["sourceReference"]
        self.assertNotIn(CORRECTION["target"]["entityId"], json.dumps(reference))
        self.assertTrue(reference["key"].startswith("fc2."))

    def test_it_signs_and_authenticates_like_every_other_contribution(self):
        sent, _ = submit()
        self.assertEqual(sent.url, "https://movievault.example/v2/contributions")
        self.assertEqual(sent.headers["Authorization"], "Bearer dvt_token")
        self.assertEqual(sent.headers["X-DiscVault-Key-Id"], "9f0d0000-0000-0000-0000-00000000000a")
        self.assertTrue(sent.headers["X-DiscVault-Signature"].startswith("key-v1="))

    def test_an_empty_change_set_never_leaves(self):
        for correction in (
            {"target": CORRECTION["target"], "changes": []},
            {"target": {"entityType": "release", "entityId": "", "baseRevision": 1}, "changes": CORRECTION["changes"]},
        ):
            with self.assertRaises(contributions.MovieVaultContributionError):
                submit(correction)


class ReadBackTests(unittest.TestCase):
    def _read(self, token="dvt_token", key_id="9f0d0000-0000-0000-0000-00000000000a"):
        captured = {}

        def _get(url, *, headers, timeout_seconds):
            captured["url"] = url
            captured["headers"] = headers
            return 200, json.dumps({"contributionId": "c-1", "status": "accepted"}).encode()

        values = {contributions.TOKEN_KEY: token, contributions.KEY_ID_KEY: key_id}
        with patch.object(
            contributions, "_setting_value", lambda conn, key, default=None, **k: values.get(key, default)
        ), patch.object(contributions, "_decrypt_secret_value", lambda value: value), patch.object(
            contributions, "_http_get", _get
        ):
            return captured, contributions.read_contribution(object(), "c-1")

    def test_it_reads_the_contribution_by_id(self):
        captured, result = self._read()
        self.assertEqual(captured["url"], "https://movievault.example/v2/contributions/c-1")
        self.assertEqual(result["status"], "accepted")

    def test_it_carries_the_bearer_token_and_no_signature(self):
        """A GET has no body, and the signature covers the body. MovieVault
        deliberately does not require one here."""
        captured, _ = self._read()
        self.assertEqual(captured["headers"]["Authorization"], "Bearer dvt_token")
        self.assertNotIn("X-DiscVault-Signature", captured["headers"])

    def test_it_refuses_rather_than_registering_a_new_identity(self):
        """Registering here would mint an identity the contribution being asked
        about does not belong to, so the read would answer 404 forever."""
        with self.assertRaises(contributions.MovieVaultContributionError) as caught:
            self._read(token="")
        self.assertEqual(caught.exception.code, "contribution_not_registered")


class GateTests(unittest.TestCase):
    def _enabled(self, *, owner, corrections, releases=True):
        with patch.object(contributions, "_table_exists", lambda *a: True), patch.object(
            contributions, "_setting_value", lambda conn, key, default=None, **k: owner
        ), patch(
            "app.backend.next_preferences.app_effective_preferences",
            lambda conn, user_id=None: {
                "share_field_corrections": corrections,
                "share_release_selections": releases,
            },
        ):
            return contributions.field_correction_enabled(object(), None)

    def test_both_on_sends(self):
        self.assertTrue(self._enabled(owner=True, corrections=True))

    def test_the_owner_gate_still_wins(self):
        self.assertFalse(self._enabled(owner=False, corrections=True))

    def test_agreeing_to_share_releases_is_not_agreeing_to_correct_records(self):
        """Offering the disc you scanned and editing a record everyone else
        reads are different acts. One checkbox for both would consent to the
        second by having agreed to the first."""
        self.assertFalse(self._enabled(owner=True, corrections=False, releases=True))

    def _gate(self, *, owner, corrections):
        with patch.object(contributions, "_table_exists", lambda *a: True), patch.object(
            contributions, "_setting_value", lambda conn, key, default=None, **k: owner
        ), patch(
            "app.backend.next_preferences.app_effective_preferences",
            lambda conn, user_id=None: {"share_field_corrections": corrections},
        ):
            return contributions.field_correction_gate(object(), None)

    def test_the_two_halves_are_reported_apart(self):
        """Composed they answer "may this be sent". Apart they answer "and who
        has to do something about it", which is what a screen needs.

        Collapsed into one boolean, a client offered the user a consent that
        could not possibly help: the owner flag was off, the user agreed, and
        the send was refused with a message that read as though agreeing had
        not registered.
        """
        self.assertEqual(self._gate(owner=False, corrections=True), {"owner": False, "user": True})
        self.assertEqual(self._gate(owner=True, corrections=False), {"owner": True, "user": False})
        self.assertEqual(self._gate(owner=True, corrections=True), {"owner": True, "user": True})

    def test_the_composed_gate_still_needs_both(self):
        for owner, corrections in ((False, True), (True, False), (False, False)):
            self.assertFalse(self._enabled(owner=owner, corrections=corrections))

    def test_the_preferences_default_to_off(self):
        from app.backend import next_preferences

        self.assertFalse(next_preferences.APP_PREFERENCE_DEFAULTS["share_field_corrections"])
        self.assertFalse(next_preferences.APP_PREFERENCE_DEFAULTS["confirm_every_upload"])
        self.assertNotIn(
            contributions.CONTRIBUTION_ENABLED_KEY, next_preferences.APP_PREFERENCE_DEFAULTS
        )


class StatusPollTests(unittest.TestCase):
    """The poll must end. A contribution nobody decides is a fact about
    MovieVault, not a reason to keep a job pending in every DiscVault forever.
    """

    def setUp(self):
        from app.backend import next_worker

        self.worker = next_worker

    def _poll(self, status, attempts):
        """Runs the handler with the log write captured rather than stubbed out.

        The write is part of what the poll is *for* -- the outcome has to
        outlive the job -- so swallowing it here would leave the one behaviour
        the user sees untested.
        """
        self.logged = []
        with patch.object(self.worker, "connect", lambda *a, **k: _NullConnection()), patch.object(
            self.worker, "read_contribution", lambda conn, cid, **k: {"contributionId": cid, "status": status}
        ), patch.object(
            self.worker,
            "update_contribution_by_contribution_id",
            lambda conn, cid, **values: self.logged.append((cid, values)),
        ):
            return self.worker.process_movievault_v2_contribution_status(
                {"attempts": attempts}, {"contributionId": "c-1"}, "worker-1"
            )

    def test_every_answer_is_written_to_the_log(self):
        """Including the ones that are not decisions. A screen showing
        "awaiting moderation" is only honest if that state was actually
        confirmed rather than assumed from the absence of news."""
        for status in ("pending", "accepted"):
            try:
                self._poll(status, 0)
            except self.worker.JobRetry:
                pass
            self.assertEqual(self.logged[0][0], "c-1")
            self.assertEqual(self.logged[0][1]["status"], status)

    def test_a_pending_answer_is_asked_again(self):
        with self.assertRaises(self.worker.JobRetry) as caught:
            self._poll("pending", 0)
        self.assertEqual(
            caught.exception.delay_seconds,
            self.worker.CONTRIBUTION_STATUS_BACKOFF_MINUTES[0] * 60,
        )

    def test_each_attempt_waits_longer_than_the_last(self):
        ladder = self.worker.CONTRIBUTION_STATUS_BACKOFF_MINUTES
        self.assertEqual(list(ladder[:4]), sorted(ladder[:4]))
        self.assertGreaterEqual(ladder[0], 60)

    def test_a_terminal_answer_ends_the_job(self):
        for status in ("accepted", "partially_accepted", "rejected"):
            summary = self._poll(status, 0)
            self.assertEqual(summary["status"], status)
            self.assertNotIn("gaveUp", summary)

    def test_it_gives_up_rather_than_polling_forever(self):
        summary = self._poll("pending", len(self.worker.CONTRIBUTION_STATUS_BACKOFF_MINUTES))
        self.assertTrue(summary["gaveUp"])

    def test_giving_up_is_not_a_failure(self):
        """`gaveUp` completes the job. Failing it would put a red row in front
        of an owner because a moderator was slow."""
        summary = self._poll("pending", 99)
        self.assertTrue(summary["handled"])

    def test_an_unknown_contribution_is_terminal(self):
        from app.backend import next_movievault_v2_contributions as c

        def _raise(conn, cid, **k):
            raise c.MovieVaultContributionError("contribution_not_found")

        with patch.object(self.worker, "connect", lambda *a, **k: _NullConnection()), patch.object(
            self.worker, "read_contribution", _raise
        ), patch.object(
            self.worker, "update_contribution_by_contribution_id", lambda *a, **k: None
        ):
            with self.assertRaises(RuntimeError):
                self.worker.process_movievault_v2_contribution_status(
                    {"attempts": 0}, {"contributionId": "c-1"}, "worker-1"
                )

    def test_the_terminal_set_matches_movievaults_own_vocabulary(self):
        """Read out of MovieVault's migration 0041 rather than guessed. A status
        this side invents is unreachable; one it misses reads as "not yet" and
        is polled to the cap - a silent week of pointless requests.
        """
        from app.backend import next_movievault_v2_contributions as c

        movievault_statuses = {
            "pending",
            "quarantined",
            "accepted",
            "partially_accepted",
            "rejected",
        }
        self.assertTrue(c.TERMINAL_CONTRIBUTION_STATUSES <= movievault_statuses)
        self.assertEqual(
            c.TERMINAL_CONTRIBUTION_STATUSES,
            {"accepted", "partially_accepted", "rejected"},
        )

    def test_a_quarantined_contribution_is_still_asked_about(self):
        """A moderator may review a quarantined contribution, so this is the
        moment the answer is most likely still to change."""
        with self.assertRaises(self.worker.JobRetry):
            self._poll("quarantined", 0)


class _NullConnection:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


if __name__ == "__main__":
    unittest.main()
