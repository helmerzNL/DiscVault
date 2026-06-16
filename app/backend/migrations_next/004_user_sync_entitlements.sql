CREATE TABLE IF NOT EXISTS watchlist_items (
    user_id  uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    movie_id uuid NOT NULL REFERENCES movies(id) ON DELETE CASCADE,
    added_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, movie_id)
);

CREATE TABLE IF NOT EXISTS watch_history (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    uuid REFERENCES users(id) ON DELETE SET NULL,
    movie_id   uuid REFERENCES movies(id) ON DELETE SET NULL,
    watched_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    snapshot   jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_watch_history_user_watched
    ON watch_history(user_id, watched_at DESC);
CREATE INDEX IF NOT EXISTS idx_watch_history_movie ON watch_history(movie_id);

CREATE TABLE IF NOT EXISTS push_subscriptions (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    endpoint     text NOT NULL UNIQUE,
    p256dh       text NOT NULL,
    auth         text NOT NULL,
    user_agent   text,
    device_label text,
    is_primary   boolean NOT NULL DEFAULT false,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_push_subscriptions_user ON push_subscriptions(user_id);

CREATE TABLE IF NOT EXISTS notification_preferences (
    user_id    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    pref_key   text NOT NULL,
    enabled    boolean NOT NULL DEFAULT true,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, pref_key)
);

CREATE TABLE IF NOT EXISTS user_notifications (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    event_id   uuid REFERENCES domain_events(id) ON DELETE SET NULL,
    title      text NOT NULL,
    body       text,
    payload    jsonb NOT NULL DEFAULT '{}'::jsonb,
    read_at    timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_user_notifications_user_created
    ON user_notifications(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_user_notifications_unread
    ON user_notifications(user_id, created_at DESC) WHERE read_at IS NULL;

CREATE TABLE IF NOT EXISTS user_preferences (
    user_id    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key        text NOT NULL,
    value      jsonb NOT NULL DEFAULT 'null'::jsonb,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, key)
);

CREATE TABLE IF NOT EXISTS client_id_mappings (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid REFERENCES users(id) ON DELETE CASCADE,
    client_id       text NOT NULL,
    entity_type     text NOT NULL,
    entity_id       uuid NOT NULL,
    idempotency_key text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE(user_id, client_id, entity_type)
);

CREATE INDEX IF NOT EXISTS idx_client_id_mappings_client
    ON client_id_mappings(user_id, client_id, entity_type);

CREATE TABLE IF NOT EXISTS idempotency_records (
    key         text PRIMARY KEY,
    user_id     uuid REFERENCES users(id) ON DELETE SET NULL,
    operation   text NOT NULL,
    status      text NOT NULL,
    entity_type text,
    entity_id   uuid,
    response    jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sync_state (
    id         text PRIMARY KEY DEFAULT 'global',
    revision   bigint NOT NULL DEFAULT 0,
    updated_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO sync_state (id, revision)
VALUES ('global', 0)
ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS sync_changes (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    revision    bigint NOT NULL,
    entity_type text NOT NULL,
    entity_id   text NOT NULL,
    operation   text NOT NULL,
    payload     jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sync_changes_revision ON sync_changes(revision);

CREATE TABLE IF NOT EXISTS license_state (
    id              text PRIMARY KEY DEFAULT 'instance',
    plan_key        text NOT NULL DEFAULT 'free',
    status          text NOT NULL DEFAULT 'unknown',
    subject         text,
    issued_at       timestamptz,
    expires_at      timestamptz,
    grace_until     timestamptz,
    last_checked_at timestamptz,
    payload         jsonb NOT NULL DEFAULT '{}'::jsonb,
    signature_ref   text,
    updated_at      timestamptz NOT NULL DEFAULT now()
);

INSERT INTO license_state (id, plan_key, status)
VALUES ('instance', 'beta', 'beta_enabled')
ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS feature_entitlements (
    feature_key text PRIMARY KEY,
    enabled     boolean NOT NULL DEFAULT false,
    source      text NOT NULL DEFAULT 'license',
    limits      jsonb NOT NULL DEFAULT '{}'::jsonb,
    expires_at  timestamptz,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS feature_usage_counters (
    feature_key text NOT NULL,
    period_key  text NOT NULL,
    subject_id  text NOT NULL DEFAULT 'instance',
    usage_count integer NOT NULL DEFAULT 0,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (feature_key, period_key, subject_id)
);

CREATE TABLE IF NOT EXISTS migration_runs (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_kind          text NOT NULL,
    source_version       text,
    source_database_hash text,
    export_manifest      jsonb NOT NULL DEFAULT '{}'::jsonb,
    status               text NOT NULL DEFAULT 'pending',
    result               jsonb NOT NULL DEFAULT '{}'::jsonb,
    error                text,
    started_at           timestamptz NOT NULL DEFAULT now(),
    completed_at         timestamptz
);

CREATE TABLE IF NOT EXISTS import_id_mappings (
    migration_run_id uuid NOT NULL REFERENCES migration_runs(id) ON DELETE CASCADE,
    source_table     text NOT NULL,
    source_id        text NOT NULL,
    target_table     text NOT NULL,
    target_id        uuid NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (migration_run_id, source_table, source_id, target_table)
);

