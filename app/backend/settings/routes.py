from flask import jsonify, request

try:
    from ..config import (
        OMDB_API_KEY,
        OMDB_ENABLED_DEFAULT,
        TMDB_API_KEY,
        TMDB_ENABLED_DEFAULT,
    )
    from ..db import get_db
    from ..logging_utils import add_log
except ImportError:  # pragma: no cover - supports running app.py directly
    from config import (
        OMDB_API_KEY,
        OMDB_ENABLED_DEFAULT,
        TMDB_API_KEY,
        TMDB_ENABLED_DEFAULT,
    )
    from db import get_db
    from logging_utils import add_log


def register_settings_routes(
    app,
    *,
    require_admin,
    is_source_enabled,
    is_bluray_scrape_enabled,
    is_bluraydiscde_scrape_enabled,
    is_registration_enabled,
):
    @app.route("/api/settings/sources", methods=["GET"])
    def get_source_settings():
        return jsonify({
            "omdb_enabled": is_source_enabled("omdb_enabled", OMDB_ENABLED_DEFAULT),
            "omdb_key_set": bool(OMDB_API_KEY),
            "tmdb_enabled": is_source_enabled("tmdb_enabled", TMDB_ENABLED_DEFAULT),
            "tmdb_key_set": bool(TMDB_API_KEY),
            "bluray_scrape_enabled": is_bluray_scrape_enabled(),
            "bluraydiscde_scrape_enabled": is_bluraydiscde_scrape_enabled(),
        })

    @app.route("/api/settings/sources", methods=["POST"])
    def set_source_settings():
        err = require_admin()
        if err:
            return err
        data = request.json or {}
        source_keys = [
            ("omdb_enabled", "OMDb"),
            ("tmdb_enabled", "TMDb"),
            ("bluray_scrape_enabled", "Blu-ray.com"),
            ("bluraydiscde_scrape_enabled", "bluray-disc.de"),
        ]
        result = {}
        conn = get_db()
        for key, label in source_keys:
            val = bool(data.get(key, False))
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (key, "true" if val else "false"),
            )
            result[key] = val
            add_log(
                "settings",
                f"{label} bron {'ingeschakeld' if val else 'uitgeschakeld'}",
                level="info",
            )
        conn.commit()
        conn.close()
        return jsonify(result)

    @app.route("/api/settings/api-keys", methods=["GET"])
    def get_api_keys_settings():
        err = require_admin()
        if err:
            return err

        def _mask(k):
            k = str(k)
            if not k:
                return ""
            return ("\u2022" * max(len(k) - 4, 0)) + k[-4:]

        tmdb = str(TMDB_API_KEY)
        omdb = str(OMDB_API_KEY)
        return jsonify({
            "tmdb_key_set": bool(tmdb),
            "omdb_key_set": bool(omdb),
            "tmdb_key_masked": _mask(tmdb),
            "omdb_key_masked": _mask(omdb),
            "tmdb_key": tmdb,
            "omdb_key": omdb,
        })

    @app.route("/api/settings/api-keys", methods=["POST"])
    def set_api_keys_settings():
        err = require_admin()
        if err:
            return err
        data = request.get_json(silent=True) or {}
        conn = get_db()
        for service in ("tmdb", "omdb"):
            field = f"{service}_key"
            if field not in data:
                continue
            val = str(data[field]).strip()
            masked = ("\u2022" * max(len(val) - 4, 0)) + val[-4:] if val else ""
            if val:
                conn.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                    (f"{service}_api_key", val),
                )
                add_log("settings", f"{service.upper()} API key opgeslagen ({masked})", level="info")
            else:
                conn.execute("DELETE FROM settings WHERE key=?", (f"{service}_api_key",))
                add_log("settings", f"{service.upper()} API key verwijderd uit database", level="info")
        conn.commit()
        conn.close()
        return jsonify({"status": "ok"})

    @app.route("/api/settings/mcp", methods=["GET"])
    def get_mcp_settings():
        err = require_admin()
        if err:
            return err
        return jsonify({"mcp_enabled": is_source_enabled("mcp_enabled", True)})

    @app.route("/api/settings/mcp", methods=["POST"])
    def set_mcp_settings():
        err = require_admin()
        if err:
            return err
        data = request.json or {}
        val = bool(data.get("mcp_enabled", True))
        conn = get_db()
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            ("mcp_enabled", "true" if val else "false"),
        )
        conn.commit()
        conn.close()
        add_log("settings", f"MCP server {'enabled' if val else 'disabled'}", level="info")
        return jsonify({"mcp_enabled": val})

    @app.route("/api/settings/debug", methods=["GET"])
    def get_debug_settings():
        err = require_admin()
        if err:
            return err
        return jsonify({"debug_enabled": is_source_enabled("debug_enabled", False)})

    @app.route("/api/settings/debug", methods=["POST"])
    def set_debug_settings():
        err = require_admin()
        if err:
            return err
        data = request.json or {}
        val = bool(data.get("debug_enabled", False))
        conn = get_db()
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            ("debug_enabled", "true" if val else "false"),
        )
        conn.commit()
        conn.close()
        add_log("settings", f"Debug modus {'ingeschakeld' if val else 'uitgeschakeld'}", level="info")
        return jsonify({"debug_enabled": val})

    @app.route("/api/settings/display", methods=["GET"])
    def get_display_settings():
        err = require_admin()
        if err:
            return err
        return jsonify({
            "show_local_title": is_source_enabled("show_local_title", True),
            "show_search_button": is_source_enabled("show_search_button", True),
            "show_auto_videos": is_source_enabled("show_auto_videos", True),
        })

    @app.route("/api/settings/display", methods=["POST"])
    def set_display_settings():
        err = require_admin()
        if err:
            return err
        data = request.json or {}
        val = bool(data.get("show_local_title", True))
        val2 = bool(data.get("show_search_button", True))
        val3 = bool(data.get("show_auto_videos", True))
        conn = get_db()
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            ("show_local_title", "true" if val else "false"),
        )
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            ("show_search_button", "true" if val2 else "false"),
        )
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            ("show_auto_videos", "true" if val3 else "false"),
        )
        conn.commit()
        conn.close()
        return jsonify({
            "show_local_title": val,
            "show_search_button": val2,
            "show_auto_videos": val3,
        })

    @app.route("/api/settings/registration", methods=["GET"])
    def get_registration_settings():
        err = require_admin()
        if err:
            return err
        return jsonify({"registration_enabled": is_registration_enabled()})

    @app.route("/api/settings/registration", methods=["POST"])
    def set_registration_settings():
        err = require_admin()
        if err:
            return err
        data = request.json or {}
        val = bool(data.get("registration_enabled", True))
        conn = get_db()
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            ("registration_enabled", "true" if val else "false"),
        )
        conn.commit()
        conn.close()
        add_log(
            "settings",
            f"Gebruikersregistratie {'ingeschakeld' if val else 'uitgeschakeld'}",
            level="info",
        )
        return jsonify({"registration_enabled": val})
