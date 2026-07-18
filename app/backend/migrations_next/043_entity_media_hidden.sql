ALTER TABLE entity_media
    ADD COLUMN IF NOT EXISTS hidden_at timestamptz;

CREATE INDEX IF NOT EXISTS idx_entity_media_active_hidden_artwork
    ON entity_media(entity_type, entity_id, role, hidden_at, is_primary, sort_order)
    WHERE deleted_at IS NULL;
