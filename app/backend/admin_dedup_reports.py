"""Immutable canonical report storage for Admin dedup execution."""

from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb


DEFAULT_REPORT_TTL_SECONDS = 900
MIN_REPORT_TTL_SECONDS = 60
MAX_REPORT_TTL_SECONDS = 3600


class CanonicalReportError(ValueError):
    def __init__(self, message: str, *, code: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def report_ttl_seconds() -> int:
    raw = os.environ.get("DISCVAULT_ADMIN_DEDUP_REPORT_TTL_SECONDS", "").strip()
    if not raw:
        return DEFAULT_REPORT_TTL_SECONDS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_REPORT_TTL_SECONDS
    return max(MIN_REPORT_TTL_SECONDS, min(value, MAX_REPORT_TTL_SECONDS))


def _json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, set):
        return sorted((_json_value(item) for item in value), key=_canonical_json)
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (UUID, Decimal)):
        return str(value)
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _json_value(value),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _group_id(group: dict[str, Any]) -> str:
    member_ids = sorted(
        str(member.get("id"))
        for member in group.get("members", [])
        if isinstance(member, dict) and member.get("id")
    )
    return _digest({"tier": group.get("tier"), "members": member_ids})[:32]


def canonical_report_payload(report: dict[str, Any]) -> dict[str, Any]:
    payload = deepcopy(_json_value(report))
    groups = []
    for raw_group in payload.get("groups", []):
        if not isinstance(raw_group, dict):
            continue
        group = deepcopy(raw_group)
        group["group_id"] = _group_id(group)
        group["members"] = sorted(
            [member for member in group.get("members", []) if isinstance(member, dict)],
            key=lambda member: str(member.get("id") or ""),
        )
        group["losers"] = sorted(str(value) for value in group.get("losers", []))
        groups.append(group)
    payload["groups"] = sorted(groups, key=lambda group: group["group_id"])
    payload["merge_group_count"] = len(groups)
    payload["movies_to_tombstone"] = sum(len(group["losers"]) for group in groups)
    return payload


def report_hash(report: dict[str, Any]) -> str:
    return _digest(canonical_report_payload(report))


def collection_fingerprint(report: dict[str, Any]) -> str:
    groups = []
    for group in canonical_report_payload(report).get("groups", []):
        members = []
        for member in group.get("members", []):
            members.append(
                {
                    key: value
                    for key, value in member.items()
                    if key not in {"score_breakdown"}
                }
            )
        groups.append(
            {
                "group_id": group["group_id"],
                "tier": group.get("tier"),
                "key": group.get("key"),
                "members": members,
                "score_breakdowns": {
                    str(member.get("id")): member.get("score_breakdown", {})
                    for member in group.get("members", [])
                },
            }
        )
    return _digest({"groups": groups})


def _record(row: dict[str, Any]) -> dict[str, Any]:
    payload = canonical_report_payload(row["report"])
    stored_hash = str(row["report_hash"])
    if report_hash(payload) != stored_hash:
        raise CanonicalReportError(
            "Canonical dedup report integrity check failed",
            code="admin_dedup_report_integrity_failed",
            status_code=409,
        )
    return {
        "id": str(row["id"]),
        "reportHash": stored_hash,
        "collectionHash": str(row["collection_hash"]),
        "issuedAt": row["issued_at"],
        "expiresAt": row["expires_at"],
        "consumedAt": row.get("consumed_at"),
        "report": payload,
    }


def create_report(
    conn,
    report: dict[str, Any],
    *,
    created_by: Any,
    source_report_id: str | UUID | None = None,
    collection_hash: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    issued_at = now or datetime.now(timezone.utc)
    expires_at = issued_at + timedelta(seconds=report_ttl_seconds())
    payload = canonical_report_payload(report)
    content_hash = report_hash(payload)
    current_collection_hash = collection_hash or collection_fingerprint(payload)
    report_id = uuid4()
    source_id = UUID(str(source_report_id)) if source_report_id else None
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO admin_dedup_reports (
                id,
                report_hash,
                collection_hash,
                issued_at,
                expires_at,
                report,
                created_by,
                source_report_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                report_id,
                content_hash,
                current_collection_hash,
                issued_at,
                expires_at,
                Jsonb(payload),
                created_by,
                source_id,
            ),
        )
    return {
        "id": str(report_id),
        "reportHash": content_hash,
        "collectionHash": current_collection_hash,
        "issuedAt": issued_at,
        "expiresAt": expires_at,
        "consumedAt": None,
        "report": payload,
    }


def parse_report_id(value: Any) -> UUID:
    try:
        return UUID(str(value or ""))
    except (TypeError, ValueError, AttributeError) as exc:
        raise CanonicalReportError(
            "Malformed canonical dedup report id",
            code="admin_dedup_report_id_malformed",
            status_code=400,
        ) from exc


def load_report(
    conn,
    report_id: Any,
    *,
    for_update: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    parsed_id = parse_report_id(report_id)
    lock_clause = " FOR UPDATE" if for_update else ""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT
                id,
                report_hash,
                collection_hash,
                issued_at,
                expires_at,
                consumed_at,
                report
            FROM admin_dedup_reports
            WHERE id=%s{lock_clause}
            """,
            (parsed_id,),
        )
        row = cur.fetchone()
    if not row:
        raise CanonicalReportError(
            "Canonical dedup report was not found",
            code="admin_dedup_report_unknown",
            status_code=404,
        )
    current_time = now or datetime.now(timezone.utc)
    if row.get("consumed_at") is not None:
        raise CanonicalReportError(
            "Canonical dedup report was already consumed",
            code="admin_dedup_report_consumed",
            status_code=409,
        )
    if row["expires_at"] <= current_time:
        raise CanonicalReportError(
            "Canonical dedup report has expired",
            code="admin_dedup_report_expired",
            status_code=410,
        )
    return _record(row)


def apply_winner_selections(
    source_report: dict[str, Any],
    selections: Any,
) -> dict[str, Any]:
    if selections is None:
        selections = []
    if not isinstance(selections, list):
        raise CanonicalReportError(
            "winnerSelections must be an array",
            code="admin_dedup_selection_invalid",
            status_code=400,
        )
    selected: dict[str, str] = {}
    for selection in selections:
        if not isinstance(selection, dict) or set(selection) != {"groupId", "winnerId"}:
            raise CanonicalReportError(
                "Each winner selection requires only groupId and winnerId",
                code="admin_dedup_selection_invalid",
                status_code=400,
            )
        group_id = str(selection.get("groupId") or "").strip()
        winner_id = str(selection.get("winnerId") or "").strip()
        if not group_id or not winner_id or group_id in selected:
            raise CanonicalReportError(
                "Duplicate or malformed winner selection",
                code="admin_dedup_selection_invalid",
                status_code=400,
            )
        selected[group_id] = winner_id

    report = canonical_report_payload(source_report)
    groups_by_id = {group["group_id"]: group for group in report.get("groups", [])}
    if any(group_id not in groups_by_id for group_id in selected):
        raise CanonicalReportError(
            "Winner selection references an unknown duplicate group",
            code="admin_dedup_selection_invalid",
            status_code=400,
        )

    for group_id, winner_id in selected.items():
        group = groups_by_id[group_id]
        member_ids = {str(member.get("id")) for member in group.get("members", [])}
        if winner_id not in member_ids:
            raise CanonicalReportError(
                "Selected winner is not a member of the duplicate group",
                code="admin_dedup_selection_invalid",
                status_code=400,
            )
        group["winner"] = winner_id
        group["losers"] = sorted(member_id for member_id in member_ids if member_id != winner_id)
        selected_member = next(
            member for member in group["members"] if str(member.get("id")) == winner_id
        )
        group["winner_score"] = (
            selected_member.get("score_breakdown") or {}
        ).get("total_score")
        group["winner_reason"] = "selected by authenticated admin"
    return canonical_report_payload(report)


def consume_report(conn, report_id: Any, *, consumed_by: Any) -> datetime:
    parsed_id = parse_report_id(report_id)
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE admin_dedup_reports
            SET consumed_at=now(), consumed_by=%s
            WHERE id=%s AND consumed_at IS NULL
            RETURNING consumed_at
            """,
            (consumed_by, parsed_id),
        )
        row = cur.fetchone()
    if not row:
        raise CanonicalReportError(
            "Canonical dedup report was already consumed",
            code="admin_dedup_report_consumed",
            status_code=409,
        )
    return row["consumed_at"]
