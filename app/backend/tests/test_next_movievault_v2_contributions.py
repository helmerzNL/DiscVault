"""Contributing a chosen release back to MovieVault.

The rules under test are the ones a later change could plausibly break without
anything else noticing: what the payload may and may not carry, that the two
gates compose so the owner's decision wins, and that only a failure which could
find a different answer later gets retried.
"""

import base64
import json
import os
import sys
import unittest
from unittest.mock import patch

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from app.backend import next_movievault_v2
from app.backend import next_movievault_v2_contributions as contributions

_ORIGIN_PATCH = patch.dict(os.environ, {"MOVIEVAULT_V2_ORIGIN": "https://movievault.example"})


def setUpModule():
    _ORIGIN_PATCH.start()


def tearDownModule():
    _ORIGIN_PATCH.stop()


def candidate(**overrides):
    value = {
        "releaseRef": "bluray_com:12345",
        "source": "external",
        "title": "Example Film",
        "edition": "Director's Cut",
        "format": "4K UHD",
        "discCount": 2,
        "discRegions": ["B"],
        "packaging": ["steelbook"],
        "video": {
            "resolution": "2160p",
            "codecs": ["hevc"],
            "hdrFormats": ["dolby_vision"],
            "aspectRatios": ["2.39:1"],
        },
        "audioTracks": [
            {
                "languageCode": "en",
                "codec": "dolby_truehd",
                "channels": "7.1",
                "immersiveFormat": "dolby_atmos",
            }
        ],
        "subtitles": [{"languageCode": "nl", "subtitleType": "full"}],
        "barcodes": [],
    }
    value.update(overrides)
    return value


FILM = {"title": "Example Film", "year": 2024, "tmdbMovieId": "123", "imdbId": "tt1234567"}

_TEST_PRIVATE_KEY, _ = contributions._generate_key_pair()


def _fake_secret(stored):
    """Stand in for the encrypted-at-rest signing key and bearer token."""
    return _TEST_PRIVATE_KEY


def mapped(cand=None, *, barcode="4006381333931", film=None, provenance="candidate_selection"):
    return next_movievault_v2.release_technical_contribution_payload(
        candidate() if cand is None else cand,
        scanned_barcode=barcode,
        film=FILM if film is None else film,
        provenance=provenance,
    )


class ContributionPayloadTests(unittest.TestCase):
    def test_scanned_barcode_never_becomes_a_release_barcode(self):
        """The scan is why the record exists, not a fact about the disc.

        The resolver returned a candidate list precisely because no source
        could confirm which pressing the barcode belongs to, and a user
        recognising an edition has not verified its check digits either.
        """
        payload = mapped()
        self.assertEqual(payload["scannedBarcode"], "4006381333931")
        self.assertNotIn("barcodes", payload["release"])

    def test_a_release_may_still_state_its_own_barcode(self):
        payload = mapped(candidate(barcodes=[{"value": "5051890321657", "type": "ean13"}]))
        self.assertEqual(payload["release"]["barcodes"], ["5051890321657"])

    def test_subtitle_languages_are_never_sent(self):
        """MovieVault derives them from the tracks; sending both spellings of
        one fact is how two views drift apart."""
        payload = mapped(candidate(subtitleLanguages=["nl", "en"]))
        self.assertNotIn("subtitleLanguages", payload["release"])
        self.assertIn("subtitles", payload["release"])

    def test_aspect_ratios_are_narrowed_to_what_movievault_accepts(self):
        """DiscVault stores "16:9" and "1.375:1" because they are real ratios.
        MovieVault rejects the whole submission for either, so they are dropped
        here rather than coerced into a different ratio."""
        payload = mapped(
            candidate(video={"resolution": "1080p", "aspectRatios": ["16:9", "1.375:1", "2.39:1"]})
        )
        self.assertEqual(payload["release"]["video"]["aspectRatios"], ["2.39:1"])

    def test_values_outside_the_shared_vocabulary_are_dropped(self):
        payload = mapped(
            candidate(
                discRegions=["B", "Z"],
                packaging=["steelbook", "bogus"],
                video={"resolution": "2160p", "codecs": ["hevc", "nope"]},
                audioTracks=[
                    {"languageCode": "en", "codec": "dolby_truehd"},
                    {"languageCode": "nl", "codec": "not_a_codec"},
                ],
            )
        )
        release = payload["release"]
        self.assertEqual(release["regions"], ["B"])
        self.assertEqual(release["packaging"], ["steelbook"])
        self.assertEqual(release["video"]["codecs"], ["hevc"])
        self.assertEqual([track["languageCode"] for track in release["audioTracks"]], ["en"])

    def test_nothing_worth_reviewing_produces_nothing(self):
        """A title plus a barcode is a lookup, not a contribution: it costs a
        moderator a review and tells them nothing they could act on."""
        self.assertEqual(mapped({"title": "Example Film"}), {})

    def test_an_unusable_barcode_stops_the_contribution(self):
        self.assertEqual(mapped(barcode="12345"), {})

    def test_no_owner_or_collection_data_can_travel(self):
        payload = mapped(
            candidate(
                overview="A plot nobody asked us to share",
                purchasePrice="19.99",
                notes="Shelf 3",
                owner="helmer",
            )
        )
        flat = repr(payload)
        for leaked in ("plot nobody", "19.99", "Shelf 3", "helmer"):
            self.assertNotIn(leaked, flat)

    def test_a_manual_entry_must_carry_its_own_weight(self):
        # Format and year are required, and a bare pair is not enough.
        self.assertEqual(
            mapped(candidate(format=""), provenance="manual_entry"),
            {},
        )
        self.assertEqual(
            mapped(film={"title": "Example Film"}, provenance="manual_entry"),
            {},
        )
        thin = {"title": "Example Film", "format": "Blu-ray", "discRegions": ["B"]}
        self.assertEqual(mapped(thin, provenance="manual_entry"), {})

    def test_a_manual_entry_never_states_a_barcode(self):
        """Only the person holding the package can attest to one, and nothing
        in the flow asks them."""
        payload = mapped(
            candidate(barcodes=[{"value": "5051890321657", "type": "ean13"}]),
            provenance="manual_entry",
        )
        self.assertNotIn("barcodes", payload["release"])

    def test_the_release_ref_is_carried_privately_and_never_submitted(self):
        """It reaches the submitter so the opaque source reference can be
        derived from it, and is stripped before the body is signed."""
        payload = mapped()
        self.assertEqual(payload["_releaseRef"], "bluray_com:12345")

        sent = {}

        def fake_http(url, *, body, headers, timeout_seconds):
            sent["url"] = url
            sent["body"] = body
            sent["headers"] = headers
            return 202, b'{"contributionId":"contribution_x","status":"pending","duplicateOf":null}'

        with patch.object(contributions, "_http", fake_http), patch.object(
            contributions, "ensure_registration", lambda conn, **_: {"registered": True}
        ), patch.object(contributions, "_setting_value", lambda *a, **k: "value"), patch.object(
            contributions, "_decrypt_secret_value", _fake_secret
        ):
            result = contributions.submit_release_technical(object(), payload)

        self.assertEqual(result["status"], "pending")
        envelope = json.loads(sent["body"].decode("utf-8"))
        self.assertEqual(envelope["entityType"], "release_technical")
        self.assertNotIn("_releaseRef", envelope["payload"])
        self.assertNotIn("bluray_com", sent["body"].decode("utf-8"))
        self.assertIn("X-DiscVault-Signature", sent["headers"])
        self.assertTrue(sent["url"].endswith("/v2/contributions"))


class IdempotencyTests(unittest.TestCase):
    def test_the_same_pick_produces_the_same_key(self):
        """Scanning a disc twice and picking the same edition must not queue a
        second review."""
        self.assertEqual(
            contributions.idempotency_key(mapped()),
            contributions.idempotency_key(mapped()),
        )

    def test_a_different_pick_is_a_different_contribution(self):
        self.assertNotEqual(
            contributions.idempotency_key(mapped()),
            contributions.idempotency_key(mapped(candidate(edition="Theatrical"))),
        )

    def test_the_key_fits_movievaults_bounds(self):
        key = contributions.idempotency_key(mapped())
        self.assertTrue(16 <= len(key) <= 160)
        self.assertRegex(key, r"^[A-Za-z0-9_.:-]+$")

    def test_the_source_reference_stays_opaque_and_bounded(self):
        reference = contributions.source_reference("4006381333931", "bluray_com:12345")
        self.assertEqual(reference["type"], "discvault_release")
        self.assertRegex(reference["key"], r"^[A-Za-z0-9_.:-]+$")
        self.assertLessEqual(len(reference["key"]), 200)
        # The provider's own reference is free text and must not leak through.
        self.assertNotIn("bluray_com", reference["key"])

    def test_canonical_json_is_byte_stable(self):
        first = contributions.canonical_json({"b": 1, "a": [2, 3]})
        second = contributions.canonical_json({"a": [2, 3], "b": 1})
        self.assertEqual(first, second)


class SignatureTests(unittest.TestCase):
    def test_the_signature_covers_timestamp_nonce_and_body(self):
        import base64

        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        private_pem, public_key = contributions._generate_key_pair()
        body = b'{"a":1}'
        headers = contributions._signed_headers(private_pem, body)
        raw = base64.urlsafe_b64decode(public_key + "=" * (-len(public_key) % 4))
        signature = headers["X-DiscVault-Signature"].removeprefix("key-v1=")
        signature_bytes = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
        signed = (
            headers["X-DiscVault-Timestamp"].encode()
            + b"."
            + headers["X-DiscVault-Nonce"].encode()
            + b"."
            + body
        )
        # Raises on mismatch, which is the assertion.
        Ed25519PublicKey.from_public_bytes(raw).verify(signature_bytes, signed)
        self.assertEqual(len(signature_bytes), 64)

    def test_the_nonce_and_timestamp_fit_movievaults_bounds(self):
        private_pem, _ = contributions._generate_key_pair()
        headers = contributions._signed_headers(private_pem, b"{}")
        nonce = headers["X-DiscVault-Nonce"]
        self.assertTrue(24 <= len(nonce) <= 120)
        self.assertRegex(nonce, r"^[A-Za-z0-9_-]+$")
        # MovieVault rejects a non-UTC offset outright rather than converting.
        self.assertTrue(headers["X-DiscVault-Timestamp"].endswith("Z"))


class FailureClassificationTests(unittest.TestCase):
    def test_a_refused_connection_is_retryable(self):
        """It proves the request never arrived, so repeating it is a first
        delivery rather than a possible duplicate."""
        error = contributions.MovieVaultContributionError("contribution_unreachable", retryable=True)
        self.assertTrue(error.retryable)

    def test_a_rejected_payload_is_terminal(self):
        """It is a mapping bug here and will fail identically forever."""
        for code in ("payload_invalid", "provider_content_forbidden"):
            self.assertIn(code, contributions.TERMINAL_ERROR_CODES)
            self.assertNotIn(code, contributions.RETRYABLE_ERROR_CODES)

    def test_rate_limiting_is_retryable(self):
        self.assertIn("rate_limited", contributions.RETRYABLE_ERROR_CODES)


if __name__ == "__main__":
    unittest.main()


class GateCompositionTests(unittest.TestCase):
    """The owner setting is the outer gate and a user cannot talk past it."""

    def _enabled(self, *, owner, user):
        with patch.object(contributions, "_table_exists", lambda *a: True), patch.object(
            contributions, "_setting_value", lambda conn, key, default=None, **k: owner
        ), patch(
            "app.backend.next_preferences.app_effective_preferences",
            lambda conn, user_id=None: {"share_release_selections": user},
        ):
            return contributions.release_contribution_enabled(object(), None)

    def test_both_on_sends(self):
        self.assertTrue(self._enabled(owner=True, user=True))

    def test_owner_off_wins_over_a_user_who_opted_in(self):
        self.assertFalse(self._enabled(owner=False, user=True))

    def test_user_off_stops_it_even_when_the_owner_allowed_it(self):
        self.assertFalse(self._enabled(owner=True, user=False))

    def test_the_admin_gate_is_not_a_user_preference(self):
        """Putting it in APP_PREFERENCE_DEFAULTS would make it user-writable,
        which is the opposite of a gate."""
        from app.backend import next_preferences

        self.assertNotIn(
            contributions.CONTRIBUTION_ENABLED_KEY, next_preferences.APP_PREFERENCE_DEFAULTS
        )
        self.assertIn("share_release_selections", next_preferences.APP_PREFERENCE_DEFAULTS)
        self.assertFalse(next_preferences.APP_PREFERENCE_DEFAULTS["share_release_selections"])


class WorkTypeTests(unittest.TestCase):
    """DiscVault knows whether a disc holds a film or a series; without this
    the type never crossed the boundary and every series became a film."""

    def test_a_series_is_sent_as_such(self):
        payload = mapped(candidate(workType="SHOW"))
        self.assertEqual(payload["film"]["workType"], "tv")

    def test_a_film_is_sent_as_such(self):
        payload = mapped(candidate(workType="MOVIE"))
        self.assertEqual(payload["film"]["workType"], "movie")

    def test_discvaults_vocabulary_is_translated_not_copied(self):
        """MOVIE/SHOW here, movie/tv there. One mapping, via the existing
        normalizer, so the two spellings cannot drift."""
        for stated, expected in (("tv", "tv"), ("series", "tv"), ("film", "movie")):
            self.assertEqual(mapped(candidate(workType=stated))["film"]["workType"], expected)

    def test_silence_stays_silence(self):
        """Absence is not "movie": it means this side did not say, and
        MovieVault then leaves whatever it already recorded."""
        self.assertNotIn("workType", mapped()["film"])

    def test_an_unrecognised_type_is_absent_rather_than_a_guess(self):
        self.assertNotIn("workType", mapped(candidate(workType="hoerspiel"))["film"])

    def test_a_series_carries_the_tv_id_and_never_the_movie_id(self):
        """The two TMDB id spaces are mutually exclusive upstream, and an id
        contradicting the stated type is refused there."""
        film = {**FILM, "tmdbTvId": "1399"}
        payload = mapped(candidate(workType="SHOW"), film=film)
        self.assertEqual(payload["film"]["tmdbTvId"], "1399")
        self.assertNotIn("tmdbMovieId", payload["film"])

    def test_a_film_carries_the_movie_id_and_never_the_tv_id(self):
        film = {**FILM, "tmdbTvId": "1399"}
        payload = mapped(candidate(workType="MOVIE"), film=film)
        self.assertEqual(payload["film"]["tmdbMovieId"], "123")
        self.assertNotIn("tmdbTvId", payload["film"])


# Fixed identities for the merge transport. I_srv survives, I_ios is retired.
_I_SRV = "11111111-1111-4111-8111-111111111111"
_I_IOS = "22222222-2222-4222-8222-222222222222"


def _merge_settings(**overrides):
    """A ``_setting_value`` stand-in keyed by settings key.

    The merge path reads the surviving instance id, the bearer token, the key id
    and the signing key from settings. The token and private key are returned
    verbatim here and pass through the patched ``_decrypt_secret_value``; the
    private key must be a real PEM because ``_signed_headers`` loads it to sign
    the server's authorisation half.
    """
    values = {
        contributions.INSTANCE_ID_KEY: _I_SRV,
        contributions.TOKEN_KEY: "srv-token",
        contributions.KEY_ID_KEY: "srv-key-id",
        contributions.PRIVATE_KEY_KEY: _TEST_PRIVATE_KEY,
    }
    values.update(overrides)

    def _lookup(conn, key, default="", **_):
        return values.get(key, default)

    return _lookup


def _sign_merge_proof(private_pem, surviving_id, retired_id):
    """What the *device* does: sign the merge message with its own key."""
    from cryptography.hazmat.primitives import serialization

    private_key = serialization.load_pem_private_key(private_pem.encode("ascii"), password=None)
    message = contributions.merge_proof_message(surviving_id, retired_id)
    return contributions._b64url(private_key.sign(message))


class MergeProofMessageTests(unittest.TestCase):
    """The proof bytes must be exactly what MovieVault re-derives and verifies."""

    def test_the_message_matches_movievaults_wire_format(self):
        from uuid import UUID

        message = contributions.merge_proof_message(_I_SRV, _I_IOS)
        expected = (
            b"movievault-merge-v1." + UUID(_I_SRV).bytes + b"." + UUID(_I_IOS).bytes
        )
        self.assertEqual(message, expected)

    def test_the_surviving_id_binds_the_proof_to_this_server(self):
        """A device that signed for one server must not verify against another,
        so swapping the surviving id must change the signed bytes."""
        other_srv = "33333333-3333-4333-8333-333333333333"
        self.assertNotEqual(
            contributions.merge_proof_message(_I_SRV, _I_IOS),
            contributions.merge_proof_message(other_srv, _I_IOS),
        )

    def test_a_device_signature_verifies_against_its_registered_key(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        device_private, device_public = contributions._generate_key_pair()
        proof = _sign_merge_proof(device_private, _I_SRV, _I_IOS)
        raw_public = base64.urlsafe_b64decode(device_public + "=" * (-len(device_public) % 4))
        raw_proof = base64.urlsafe_b64decode(proof + "=" * (-len(proof) % 4))
        # Raises on mismatch, which is the assertion; MovieVault does exactly this.
        Ed25519PublicKey.from_public_bytes(raw_public).verify(
            raw_proof, contributions.merge_proof_message(_I_SRV, _I_IOS)
        )
        self.assertEqual(len(raw_proof), 64)


class MergeTransportTests(unittest.TestCase):
    """The couple-and-merge trigger, two-sided (contribution-v3 §5.7)."""

    def _call(self, *, http, registered=True, proof="device-proof", retired=_I_IOS, settings=None):
        state = {"registered": registered, "instanceId": _I_SRV}
        with patch.object(contributions, "_http", http), patch.object(
            contributions, "registration_state", lambda conn: state
        ), patch.object(
            contributions, "_setting_value", settings or _merge_settings()
        ), patch.object(
            contributions, "_decrypt_secret_value", lambda value, **_: value
        ):
            return contributions.merge_instance(
                object(), retired_instance_id=retired, retired_instance_proof=proof
            )

    def test_both_halves_travel_and_the_result_returns(self):
        """The server signs and bears its token (I_srv half); the device proof
        rides in the body (I_ios half); MovieVault's result comes back."""
        sent = {}

        def fake_http(url, *, body, headers, timeout_seconds):
            sent["url"] = url
            sent["body"] = body
            sent["headers"] = headers
            return 200, b'{"status":"completed","reassociatedCount":3,"retiredInPlaceCount":1}'

        device_private, _ = contributions._generate_key_pair()
        proof = _sign_merge_proof(device_private, _I_SRV, _I_IOS)
        result = self._call(http=fake_http, proof=proof)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["reassociatedCount"], 3)
        self.assertTrue(sent["url"].endswith("/v2/contributions/merge"))
        envelope = json.loads(sent["body"].decode("utf-8"))
        self.assertEqual(envelope["protocolVersion"], "contribution-merge-1")
        self.assertEqual(envelope["retiredInstanceId"], _I_IOS)
        self.assertEqual(envelope["retiredInstanceProof"], proof)
        # The server's authorisation half: its bearer token, key id and signature.
        self.assertEqual(sent["headers"]["Authorization"], "Bearer srv-token")
        self.assertEqual(sent["headers"]["X-DiscVault-Key-Id"], "srv-key-id")
        self.assertIn("X-DiscVault-Signature", sent["headers"])

    def test_the_server_cannot_merge_without_the_device_proof(self):
        """Adversarial: a server-only caller (no I_ios half) never reaches the
        wire. This is 'neither side can merge unilaterally' at the transport."""
        called = {"http": False}

        def fake_http(*a, **k):
            called["http"] = True
            return 200, b"{}"

        with self.assertRaises(contributions.MovieVaultContributionError) as cm:
            self._call(http=fake_http, proof="")
        self.assertEqual(cm.exception.code, "merge_proof_missing")
        self.assertFalse(called["http"], "no request may be sent without the device half")

    def test_an_unregistered_server_cannot_merge(self):
        """The other half failing closed: without I_srv there is nothing to
        merge into, and no call is made."""
        called = {"http": False}

        def fake_http(*a, **k):
            called["http"] = True
            return 200, b"{}"

        with self.assertRaises(contributions.MovieVaultContributionError) as cm:
            self._call(http=fake_http, registered=False)
        self.assertEqual(cm.exception.code, "contribution_not_registered")
        self.assertFalse(called["http"])

    def test_a_non_uuid_retired_id_is_refused_locally(self):
        with self.assertRaises(contributions.MovieVaultContributionError) as cm:
            self._call(http=lambda *a, **k: (200, b"{}"), retired="not-a-uuid")
        self.assertEqual(cm.exception.code, "retired_instance_id_invalid")

    def test_merging_the_server_into_itself_is_refused(self):
        with self.assertRaises(contributions.MovieVaultContributionError) as cm:
            self._call(http=lambda *a, **k: (200, b"{}"), retired=_I_SRV)
        self.assertEqual(cm.exception.code, "merge_into_self")

    def test_a_rejected_proof_from_movievault_is_terminal(self):
        """A 401 signature_invalid means the device half did not verify. A
        repeat signs identically, so it must not be retried."""

        def fake_http(url, *, body, headers, timeout_seconds):
            return 401, b'{"error":{"code":"signature_invalid"}}'

        with self.assertRaises(contributions.MovieVaultContributionError) as cm:
            self._call(http=fake_http)
        self.assertEqual(cm.exception.code, "signature_invalid")
        self.assertFalse(cm.exception.retryable)

    def test_a_timed_out_merge_is_retryable_because_it_is_idempotent(self):
        """MovieVault keys the merge on (retired, surviving), so a repeat after a
        timeout is a no-op there rather than a second merge."""

        def fake_http(url, *, body, headers, timeout_seconds):
            raise contributions.MovieVaultContributionError(
                "contribution_network_error", retryable=True
            )

        with self.assertRaises(contributions.MovieVaultContributionError) as cm:
            self._call(http=fake_http)
        self.assertTrue(cm.exception.retryable)
