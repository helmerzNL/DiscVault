"""DiscVault Next background worker.

The worker is intentionally small at this stage: it proves the runtime pattern
for long-running work without coupling metadata providers or imports into API
requests. Jobs are claimed with PostgreSQL row locks so multiple workers can run
without processing the same job twice.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


STOP = False


def database_url() -> str:
    value = os.environ.get("DATABASE_URL", "").strip()
    if not value:
        raise RuntimeError("DATABASE_URL is required for the DiscVault Next worker.")
    return value


def connect():
    import psycopg

    return psycopg.connect(database_url(), row_factory=dict_row, autocommit=False)


def background_jobs_ready(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.background_jobs') AS table_name")
        row = cur.fetchone()
    return bool(row and row.get("table_name"))


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_ready(item) for item in value]
    if isinstance(value, tuple):
        return [json_ready(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    return value


def claim_job(conn, worker_id: str) -> dict[str, Any] | None:
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id
                FROM background_jobs
                WHERE status='pending'
                ORDER BY created_at ASC
                FOR UPDATE SKIP LOCKED
                LIMIT 1
                """
            )
            row = cur.fetchone()
            if not row:
                return None
            cur.execute(
                """
                UPDATE background_jobs
                SET status='running',
                    started_at=now(),
                    result = result || %s
                WHERE id=%s
                RETURNING
                    id,
                    job_type,
                    status,
                    payload,
                    result,
                    created_at,
                    started_at
                """,
                (Jsonb({"workerId": worker_id}), row["id"]),
            )
            return cur.fetchone()


def complete_job(conn, job_id: UUID, result: dict[str, Any]) -> None:
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE background_jobs
                SET status='completed',
                    result=%s,
                    error=NULL,
                    finished_at=now()
                WHERE id=%s
                """,
                (Jsonb(json_ready(result)), job_id),
            )


def fail_job(conn, job_id: UUID, error: str, result: dict[str, Any] | None = None) -> None:
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE background_jobs
                SET status='failed',
                    result=%s,
                    error=%s,
                    finished_at=now()
                WHERE id=%s
                """,
                (Jsonb(json_ready(result or {})), error, job_id),
            )


def process_job(job: dict[str, Any], worker_id: str) -> dict[str, Any]:
    job_type = job["job_type"]
    payload = job.get("payload") or {}

    if job_type == "sync.noop":
        return {
            "workerId": worker_id,
            "handled": True,
            "jobType": job_type,
            "message": payload.get("message") or "No operation executed.",
            "echo": payload,
        }

    raise RuntimeError(f"Unsupported job type: {job_type}")


def run_once(worker_id: str) -> int:
    with connect() as conn:
        if not background_jobs_ready(conn):
            print(
                json.dumps(
                    {
                        "status": "waiting",
                        "reason": "database schema is not initialized",
                        "missing": "background_jobs",
                    }
                )
            )
            return 2
        job = claim_job(conn, worker_id)
        if not job:
            print("No pending jobs.")
            return 0
        try:
            result = process_job(job, worker_id)
            complete_job(conn, job["id"], result)
            print(json.dumps({"status": "completed", "jobId": str(job["id"]), "result": result}))
            return 0
        except Exception as exc:
            fail_job(conn, job["id"], str(exc), {"workerId": worker_id})
            print(json.dumps({"status": "failed", "jobId": str(job["id"]), "error": str(exc)}))
            return 1


def work_loop(worker_id: str, poll_interval: float) -> int:
    while not STOP:
        run_once(worker_id)
        if STOP:
            break
        time.sleep(poll_interval)
    return 0


def request_stop(_signum, _frame) -> None:
    global STOP
    STOP = True


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DiscVault Next background worker.")
    parser.add_argument("command", choices=("run-once", "work"))
    parser.add_argument(
        "--worker-id",
        default=os.environ.get("DISCVAULT_WORKER_ID") or "next-worker",
        help="Stable worker label used in job results.",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=float(os.environ.get("DISCVAULT_WORKER_POLL_INTERVAL", "2")),
        help="Seconds to wait between polling attempts in work mode.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    if args.command == "run-once":
        return run_once(args.worker_id)
    return work_loop(args.worker_id, args.poll_interval)


if __name__ == "__main__":
    raise SystemExit(main())
