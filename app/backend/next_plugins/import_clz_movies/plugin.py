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
    "id": "import_clz_movies",
    "name": "CLZ Movies Web",
    "sourceKind": "clz_movies_export",
    "defaultPath": "/data/import/clz_movies",
    "aliases": {
        "externalId": ("CLZ ID", "ID", "Movie ID", "Collection Number", "Index"),
        "title": ("Title", "Sort Title", "Display Title", "Name"),
        "originalTitle": ("Original Title", "OriginalTitle"),
        "year": ("Year", "Release Year", "Movie Year"),
        "releaseDate": ("Release Date", "Date"),
        "barcode": ("Barcode", "UPC", "EAN"),
        "format": ("Format", "Media Type", "Medium", "Type"),
        "edition": ("Edition", "Release", "Version"),
        "country": ("Country", "Country Code"),
        "language": ("Language", "Languages"),
        "overview": ("Plot", "Description", "Overview", "Synopsis"),
        "runtime": ("Runtime", "Running Time", "Length"),
        "rating": ("Rating", "My Rating", "IMDb Rating"),
        "director": ("Director", "Directors"),
        "actor": ("Cast", "Actors", "Stars"),
        "genre": ("Genre", "Genres"),
        "imdbId": ("IMDb ID", "IMDB ID", "IMDb", "imdb_id"),
        "tmdbId": ("TMDb ID", "TMDB ID", "tmdb_id"),
        "poster": ("Cover", "Cover URL", "Poster", "Poster URL"),
        "backdrop": ("Backdrop", "Backdrop URL"),
        "sourceUrl": ("URL", "Link", "CLZ URL"),
        "tags": ("Tags", "Labels", "Collection Status"),
        "collection": ("Collection", "List", "Folder", "Group"),
        "boxSet": ("Box Set", "BoxSet", "Boxset", "Set", "Series", "Franchise"),
        "vault": ("Vault", "Vault Title", "Version Group", "Edition Group"),
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
