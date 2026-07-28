"""Static frontend script routes for the DiscVault Next backend.

The Next SPA is still emitted as one large inline document by
``next_views_ui.py``. New frontend behaviour is deliberately built as separate
files under ``app/frontend/js/`` instead of growing that module further, and
this route serves them.

Only explicitly whitelisted filenames are served; the route never accepts a
path segment, so it cannot be used to traverse the frontend directory.
"""

from __future__ import annotations

from pathlib import Path

from flask import Flask, send_file

try:  # pragma: no cover - exercised indirectly by both layouts
    from .next_common import NextApiError
except ImportError:  # pragma: no cover - supports gunicorn next_app:app
    from next_common import NextApiError


NEXT_SCRIPT_ASSETS = {
    "library-paging.js",
}

NEXT_SCRIPT_URL_PREFIX = "/api/next/app/js"


def _next_app():
    try:  # pragma: no cover - import shape depends on runtime layout
        from . import next_app
    except ImportError:  # pragma: no cover - supports gunicorn next_app:app
        import next_app
    return next_app


def next_script_url(name: str) -> str:
    """Public URL for a whitelisted frontend script."""
    if name not in NEXT_SCRIPT_ASSETS:
        raise KeyError(name)
    return f"{NEXT_SCRIPT_URL_PREFIX}/{name}"


def next_script_path(name: str) -> Path:
    return _next_app().next_frontend_dir() / "js" / name


def register_next_static_routes(flask_app: Flask, *, connect=None) -> None:  # pragma: no cover - Flask integration
    @flask_app.get(f"{NEXT_SCRIPT_URL_PREFIX}/<script_name>")
    def next_frontend_script(script_name: str):
        safe_name = Path(script_name).name
        if safe_name != script_name or safe_name not in NEXT_SCRIPT_ASSETS:
            raise NextApiError("Script not found", 404)
        path = next_script_path(safe_name)
        if not path.exists() or not path.is_file():
            raise NextApiError("Script not found", 404)
        result = send_file(path, mimetype="application/javascript")
        result.headers["Cache-Control"] = "no-cache"
        return result
