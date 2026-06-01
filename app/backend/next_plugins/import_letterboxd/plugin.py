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
    "id": "import_letterboxd",
    "name": "Letterboxd",
    "sourceKind": "letterboxd_export",
    "defaultPath": "/data/import/letterboxd",
    "aliases": {
        "externalId": ("Letterboxd URI", "Letterboxd URI ", "URI", "URL"),
        "title": ("Name", "Title", "Film"),
        "originalTitle": ("Original Title", "OriginalTitle"),
        "year": ("Year", "Release Year"),
        "releaseDate": ("Release Date", "Watched Date", "Date"),
        "rating": ("Rating", "Rated"),
        "overview": ("Review", "Description"),
        "sourceUrl": ("Letterboxd URI", "URI", "URL"),
        "tags": ("Tags", "Tag"),
        "collection": ("Collection", "List", "List Name", "Folder"),
        "boxSet": ("Box Set", "BoxSet", "Boxset", "Set", "Series", "Franchise"),
        "vault": ("Vault", "Vault Title", "Version Group", "Edition Group"),
        "watchedAt": ("Watched Date", "Date"),
        "watchlisted": ("Watchlist", "In Watchlist", "Watchlisted"),
    },
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
