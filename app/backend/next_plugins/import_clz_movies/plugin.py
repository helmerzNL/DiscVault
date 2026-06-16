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
        "externalId": ("CLZ ID", "CLZ Movie ID", "ID", "Movie ID", "Collection Number", "Index", "Nr", "No"),
        "title": ("Title", "Sort Title", "Display Title", "Name", "Movie", "Movie Title"),
        "originalTitle": ("Original Title", "OriginalTitle", "Original Name"),
        "year": ("Year", "Release Year", "Movie Year", "Year Released", "ReleaseYear"),
        "releaseDate": ("Release Date", "Date", "ReleaseDate", "Released"),
        "barcode": ("Barcode", "UPC", "EAN", "UPC/EAN", "EAN/UPC"),
        "format": ("Format", "Media Type", "Medium", "Type", "Media", "Format Type"),
        "edition": ("Edition", "Release", "Version", "Edition Type"),
        "country": ("Country", "Country Code", "Locality"),
        "language": ("Language", "Languages", "Audio Language"),
        "overview": ("Plot", "Description", "Overview", "Synopsis"),
        "runtime": ("Runtime", "Running Time", "Length", "Minutes"),
        "rating": ("Rating", "My Rating", "IMDb Rating", "IMDB Rating", "Personal Rating"),
        "director": ("Director", "Directors"),
        "actor": ("Cast", "Actors", "Stars"),
        "genre": ("Genre", "Genres"),
        "imdbId": ("IMDb ID", "IMDB ID", "IMDb", "IMDB", "IMDb Number", "IMDB Number", "IMDb URL", "IMDB URL", "imdb_id"),
        "tmdbId": ("TMDb ID", "TMDB ID", "TMDb", "TMDB", "TMDb URL", "TMDB URL", "tmdb_id"),
        "poster": ("Cover", "Cover URL", "Poster", "Poster URL"),
        "backdrop": ("Backdrop", "Backdrop URL"),
        "sourceUrl": ("URL", "Link", "CLZ URL", "Movie URL", "Details URL"),
        "tags": ("Tags", "Labels", "Collection Status", "Status", "Storage Device", "Location"),
        "collection": ("Collection", "List", "Folder", "Group"),
        "boxSet": ("Box Set", "BoxSet", "Boxset", "Set", "Series", "Franchise"),
        "vault": ("Vault", "Vault Title", "Version Group", "Edition Group"),
    },
    "recognition": {
        "fileNameHints": ("clz", "collectorz", "movie collector", "clz movies"),
        "columnHints": (
            "CLZ ID",
            "CLZ Movie ID",
            "Collection Number",
            "Collection Status",
            "IMDb Number",
            "IMDb URL",
            "Storage Device",
            "Cover URL",
        ),
        "requiredMappedFields": ("title", "year", "barcode", "format"),
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
