CREATE TABLE IF NOT EXISTS media_groups (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    public_id    text UNIQUE NOT NULL,
    name         text NOT NULL,
    created_by   uuid REFERENCES users(id) ON DELETE SET NULL,
    hide_digital boolean NOT NULL DEFAULT false,
    metadata     jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_media_groups_name ON media_groups(lower(name));
CREATE INDEX IF NOT EXISTS idx_media_groups_created_by ON media_groups(created_by);

CREATE TABLE IF NOT EXISTS media_group_members (
    group_id   uuid NOT NULL REFERENCES media_groups(id) ON DELETE CASCADE,
    user_id    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role       text NOT NULL DEFAULT 'member',
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (group_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_media_group_members_user ON media_group_members(user_id);

CREATE TABLE IF NOT EXISTS media_group_movies (
    group_id   uuid NOT NULL REFERENCES media_groups(id) ON DELETE CASCADE,
    movie_id   uuid NOT NULL REFERENCES movies(id) ON DELETE CASCADE,
    metadata   jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (group_id, movie_id)
);

CREATE INDEX IF NOT EXISTS idx_media_group_movies_movie ON media_group_movies(movie_id);

CREATE TABLE IF NOT EXISTS media_group_invites (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    group_id   uuid NOT NULL REFERENCES media_groups(id) ON DELETE CASCADE,
    inviter_id uuid REFERENCES users(id) ON DELETE SET NULL,
    invitee_id uuid REFERENCES users(id) ON DELETE SET NULL,
    status     text NOT NULL DEFAULT 'pending',
    metadata   jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_media_group_invites_group ON media_group_invites(group_id);
CREATE INDEX IF NOT EXISTS idx_media_group_invites_invitee ON media_group_invites(invitee_id);

CREATE TABLE IF NOT EXISTS media_group_digital_access (
    group_id   uuid NOT NULL REFERENCES media_groups(id) ON DELETE CASCADE,
    user_id    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (group_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_media_group_digital_access_user ON media_group_digital_access(user_id);
