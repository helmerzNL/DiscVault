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
    "id": "import_mymovies_dk",
    "name": "My Movies.dk",
    "sourceKind": "mymovies_dk_export",
    "defaultPath": "/data/import/mymovies",
    "aliases": {
        "externalId": ("Id", "ID", "Collection Number", "CollectionNumber", "Movie ID", "MovieId"),
        "title": ("Title", "Local Title", "Sort Title", "Name", "MovieTitle"),
        "originalTitle": ("Original Title", "OriginalTitle", "Original Name"),
        "year": ("Year", "Production Year", "Release Year", "ReleaseYear"),
        "releaseDate": ("Release Date", "ReleaseDate", "Local Release Date"),
        "barcode": ("Barcode", "UPC", "EAN", "Disc Barcode"),
        "format": ("Format", "Media Type", "MediaType", "Type"),
        "edition": ("Edition", "Edition Type", "Version"),
        "country": ("Country", "CountryCode", "Locality"),
        "language": ("Language", "Audio Language"),
        "overview": ("Description", "Overview", "Plot", "Synopsis"),
        "runtime": ("Runtime", "Running Time", "Minutes"),
        "rating": ("Rating", "IMDB Rating", "IMDb Rating", "Personal Rating"),
        "director": ("Director", "Directors"),
        "actor": ("Actors", "Cast", "Actor"),
        "genre": ("Genres", "Genre", "Categories"),
        "imdbId": ("IMDB", "IMDb", "IMDb Id", "IMDB ID", "imdb_id"),
        "tmdbId": ("TMDb", "TMDb Id", "TMDB ID", "tmdb_id"),
        "poster": ("Poster", "Cover", "Cover Url", "Cover URL", "Poster URL"),
        "backdrop": ("Backdrop", "Backdrop URL", "Background"),
        "sourceUrl": ("URL", "Web", "Link"),
        "tags": ("Tags", "Categories"),
        "collection": ("Collection", "List", "Folder", "Group", "Category"),
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
