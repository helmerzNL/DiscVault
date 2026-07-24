-- Immutable, multi-worker-safe canonical reports for authenticated Admin dedup.

CREATE TABLE IF NOT EXISTS admin_dedup_reports (
    id               uuid PRIMARY KEY,
    report_hash      text NOT NULL,
    collection_hash  text NOT NULL,
    issued_at        timestamptz NOT NULL,
    expires_at       timestamptz NOT NULL,
    report            jsonb NOT NULL,
    created_by       uuid,
    source_report_id uuid REFERENCES admin_dedup_reports(id),
    consumed_at      timestamptz,
    consumed_by      uuid,
    CONSTRAINT admin_dedup_report_hash_format
        CHECK (report_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT admin_dedup_collection_hash_format
        CHECK (collection_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT admin_dedup_report_expiry_order
        CHECK (expires_at > issued_at),
    CONSTRAINT admin_dedup_report_consumption_pair
        CHECK ((consumed_at IS NULL) = (consumed_by IS NULL))
);

CREATE INDEX IF NOT EXISTS idx_admin_dedup_reports_expires_at
    ON admin_dedup_reports(expires_at);

CREATE INDEX IF NOT EXISTS idx_admin_dedup_reports_active
    ON admin_dedup_reports(expires_at)
    WHERE consumed_at IS NULL;

CREATE OR REPLACE FUNCTION prevent_admin_dedup_report_payload_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.report_hash IS DISTINCT FROM OLD.report_hash
       OR NEW.collection_hash IS DISTINCT FROM OLD.collection_hash
       OR NEW.issued_at IS DISTINCT FROM OLD.issued_at
       OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
       OR NEW.report IS DISTINCT FROM OLD.report
       OR NEW.created_by IS DISTINCT FROM OLD.created_by
       OR NEW.source_report_id IS DISTINCT FROM OLD.source_report_id
       OR (
           OLD.consumed_at IS NOT NULL
           AND (
               NEW.consumed_at IS DISTINCT FROM OLD.consumed_at
               OR NEW.consumed_by IS DISTINCT FROM OLD.consumed_by
           )
       )
    THEN
        RAISE EXCEPTION 'admin_dedup_reports canonical payload is immutable';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_admin_dedup_report_payload_immutable
    ON admin_dedup_reports;
CREATE TRIGGER trg_admin_dedup_report_payload_immutable
BEFORE UPDATE ON admin_dedup_reports
FOR EACH ROW
EXECUTE FUNCTION prevent_admin_dedup_report_payload_mutation();
