ALTER TABLE digital_media_items
    ADD COLUMN IF NOT EXISTS playback_url text;

ALTER TABLE digital_media_items
    ADD COLUMN IF NOT EXISTS matched_movie_id uuid REFERENCES movies(id) ON DELETE SET NULL;

ALTER TABLE digital_media_items
    ADD COLUMN IF NOT EXISTS metadata jsonb NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE digital_media_items
    ADD COLUMN IF NOT EXISTS synced_at timestamptz NOT NULL DEFAULT now();

ALTER TABLE digital_media_items
    ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now();

ALTER TABLE digital_media_items
    ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();

CREATE INDEX IF NOT EXISTS idx_digital_media_items_match
    ON digital_media_items(matched_movie_id);

