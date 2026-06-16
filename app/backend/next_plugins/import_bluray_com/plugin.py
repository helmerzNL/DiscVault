try:
    from next_plugins._collection_import_base import CollectionImportPlugin
except ImportError:  # pragma: no cover
    try:
        from app.backend.next_plugins._collection_import_base import CollectionImportPlugin
    except ImportError:
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from _collection_import_base import CollectionImportPlugin


SOURCE = {
    "id": "import_bluray_com",
    "name": "Blu-ray.com",
    "sourceKind": "bluray_com_collection_export",
    "defaultPath": "/data/import/bluray_com",
    "aliases": {
        "externalId": ("ID", "Movie ID", "Release ID", "Blu-ray.com ID"),
        "title": ("Title", "Movie", "Name"),
        "originalTitle": ("Original Title", "OriginalTitle"),
        "year": ("Year", "Release Year", "Movie Year"),
        "releaseDate": ("Release Date", "Edition Release Date"),
        "barcode": ("UPC", "EAN", "Barcode"),
        "format": ("Format", "Media", "Media Type", "Type"),
        "edition": ("Edition", "Packaging", "Release"),
        "country": ("Country", "Country Code", "Locality"),
        "language": ("Language", "Audio Language"),
        "overview": ("Overview", "Plot", "Description"),
        "runtime": ("Runtime", "Running Time"),
        "rating": ("Rating", "User Rating", "IMDb Rating"),
        "director": ("Director", "Directors"),
        "actor": ("Cast", "Actors"),
        "genre": ("Genre", "Genres"),
        "imdbId": ("IMDb ID", "IMDB ID", "IMDb", "imdb_id"),
        "tmdbId": ("TMDb ID", "TMDB ID", "tmdb_id"),
        "poster": ("Cover", "Cover URL", "Poster", "Poster URL"),
        "backdrop": ("Backdrop", "Backdrop URL"),
        "sourceUrl": ("URL", "Detail URL", "Blu-ray.com URL"),
        "tags": ("Tags", "Collection"),
        "collection": ("Collection", "List", "Folder", "Group"),
        "boxSet": ("Box Set", "BoxSet", "Boxset", "Set", "Series", "Franchise"),
        "vault": ("Vault", "Vault Title", "Version Group", "Edition Group"),
    },
    "defaultFormat": "Blu-ray",
}

PLUGIN = CollectionImportPlugin(SOURCE)


def health_check(context=None):
    return PLUGIN.health_check(context)


def inspect_source(payload=None, context=None):
    return PLUGIN.inspect_source(payload, context)


def plan_import(payload=None, context=None):
    return PLUGIN.plan_import(payload, context)


def import_source(payload=None, context=None):
    return PLUGIN.import_source(payload, context)
