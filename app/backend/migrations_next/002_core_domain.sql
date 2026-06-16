CREATE TABLE IF NOT EXISTS movies (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    public_id       text UNIQUE NOT NULL,
    barcode         text UNIQUE,
    title           text NOT NULL,
    sort_title      text,
    original_title  text,
    year            text,
    release_date    date,
    format          text,
    edition         text,
    edition_type    text,
    country         text,
    language        text,
    runtime_minutes integer,
    overview        text,
    notes           text,
    rating          text,
    purchase_date   date,
    purchase_price  numeric(10,2),
    location        text,
    owner_id        uuid REFERENCES users(id) ON DELETE SET NULL,
    metadata        jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_movies_title ON movies(lower(title));
CREATE INDEX IF NOT EXISTS idx_movies_sort_title ON movies(lower(sort_title));
CREATE INDEX IF NOT EXISTS idx_movies_barcode ON movies(barcode);
CREATE INDEX IF NOT EXISTS idx_movies_owner ON movies(owner_id);

CREATE TABLE IF NOT EXISTS movie_identifiers (
    movie_id        uuid NOT NULL REFERENCES movies(id) ON DELETE CASCADE,
    provider_id     text NOT NULL,
    identifier      text NOT NULL,
    identifier_type text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (movie_id, provider_id, identifier_type, identifier)
);

CREATE INDEX IF NOT EXISTS idx_movie_identifiers_lookup
    ON movie_identifiers(provider_id, identifier_type, identifier);

CREATE TABLE IF NOT EXISTS movie_localizations (
    movie_id   uuid NOT NULL REFERENCES movies(id) ON DELETE CASCADE,
    lang       text NOT NULL,
    title      text,
    overview   text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (movie_id, lang)
);

CREATE TABLE IF NOT EXISTS movie_technical_specs (
    movie_id        uuid PRIMARY KEY REFERENCES movies(id) ON DELETE CASCADE,
    hdr             text,
    packaging       text,
    screen_ratios   text,
    audio_tracks    jsonb NOT NULL DEFAULT '[]'::jsonb,
    subtitles       jsonb NOT NULL DEFAULT '[]'::jsonb,
    regions         jsonb NOT NULL DEFAULT '[]'::jsonb,
    content_ratings jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS people (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    public_id        text UNIQUE NOT NULL,
    name             text NOT NULL,
    birth_date       date,
    death_date       date,
    place_of_birth   text,
    known_for        text,
    profile_asset_id uuid,
    metadata         jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_people_name ON people(lower(name));

CREATE TABLE IF NOT EXISTS person_identifiers (
    person_id       uuid NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    provider_id     text NOT NULL,
    identifier_type text NOT NULL,
    identifier      text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (person_id, provider_id, identifier_type, identifier)
);

CREATE TABLE IF NOT EXISTS person_localizations (
    person_id  uuid NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    lang       text NOT NULL,
    biography  text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (person_id, lang)
);

CREATE TABLE IF NOT EXISTS movie_credits (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    movie_id    uuid NOT NULL REFERENCES movies(id) ON DELETE CASCADE,
    person_id   uuid NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    credit_type text NOT NULL,
    character   text,
    job         text,
    sort_order  integer NOT NULL DEFAULT 0,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE(movie_id, person_id, credit_type, job, character)
);

CREATE INDEX IF NOT EXISTS idx_movie_credits_movie ON movie_credits(movie_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_movie_credits_person ON movie_credits(person_id);

CREATE TABLE IF NOT EXISTS containers (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    public_id        text UNIQUE NOT NULL,
    container_type   text NOT NULL CHECK (container_type IN ('vault', 'box_set', 'collection')),
    title            text NOT NULL,
    barcode          text,
    badge_label      text,
    year             text,
    description      text,
    primary_movie_id uuid REFERENCES movies(id) ON DELETE SET NULL,
    metadata         jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_containers_type_title ON containers(container_type, lower(title));
CREATE INDEX IF NOT EXISTS idx_containers_barcode ON containers(barcode);

CREATE TABLE IF NOT EXISTS container_identifiers (
    container_id    uuid NOT NULL REFERENCES containers(id) ON DELETE CASCADE,
    provider_id     text NOT NULL,
    identifier_type text NOT NULL,
    identifier      text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (container_id, provider_id, identifier_type, identifier)
);

CREATE TABLE IF NOT EXISTS container_movies (
    container_id uuid NOT NULL REFERENCES containers(id) ON DELETE CASCADE,
    movie_id     uuid NOT NULL REFERENCES movies(id) ON DELETE CASCADE,
    sort_order   integer NOT NULL DEFAULT 0,
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (container_id, movie_id)
);

CREATE INDEX IF NOT EXISTS idx_container_movies_movie ON container_movies(movie_id);

CREATE TABLE IF NOT EXISTS collection_items (
    collection_id uuid NOT NULL REFERENCES containers(id) ON DELETE CASCADE,
    item_type     text NOT NULL CHECK (item_type IN ('movie', 'vault', 'box_set', 'collection')),
    item_id       uuid NOT NULL,
    sort_order    integer NOT NULL DEFAULT 0,
    created_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (collection_id, item_type, item_id)
);

CREATE INDEX IF NOT EXISTS idx_collection_items_item ON collection_items(item_type, item_id);

