from flask import jsonify, request

try:
    from ..config import (
        METADATA_SOURCE_ORDER_DEFAULT,
        MOVIEVAULT_API_KEY,
        MOVIEVAULT_API_TOKEN,
        MOVIEVAULT_BASE_URL,
        MOVIEVAULT_CONTRIBUTION_ENABLED_DEFAULT,
        MOVIEVAULT_CONTRIBUTION_URL,
        MOVIEVAULT_INGEST_URL,
        MOVIEVAULT_SEARCH_URL,
        MOVIEVAULT_SHARING_MODE,
        OMDB_API_KEY,
        OMDB_ENABLED_DEFAULT,
        TMDB_API_KEY,
        TMDB_ENABLED_DEFAULT,
    )
    from ..db import get_db
    from ..logging_utils import add_log
except ImportError:  # pragma: no cover - supports running app.py directly
    from config import (
        METADATA_SOURCE_ORDER_DEFAULT,
        MOVIEVAULT_API_KEY,
        MOVIEVAULT_API_TOKEN,
        MOVIEVAULT_BASE_URL,
        MOVIEVAULT_CONTRIBUTION_ENABLED_DEFAULT,
        MOVIEVAULT_CONTRIBUTION_URL,
        MOVIEVAULT_INGEST_URL,
        MOVIEVAULT_SEARCH_URL,
        MOVIEVAULT_SHARING_MODE,
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
    metadata_source_labels = {
        "movievault": "MovieVault",
        "tmdb": "TMDb",
        "omdb": "OMDb",
        "bluray_com": "Blu-ray.com",
        "bluray_disc_de": "bluray-disc.de",
    }

    def _setting_value(key: str, default: str = "") -> str:
        try:
            conn = get_db()
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            conn.close()
            if row and row[0] is not None:
                return str(row[0])
        except Exception:
            pass
        return default

    def _bool_setting(key: str, default: bool) -> bool:
        value = _setting_value(key, "true" if default else "false")
        return str(value).strip().lower() == "true"

    def _movievault_search_url() -> str:
        return (str(MOVIEVAULT_SEARCH_URL).strip() or str(MOVIEVAULT_BASE_URL).strip()).rstrip("/")

    def _movievault_ingest_url() -> str:
        return str(MOVIEVAULT_INGEST_URL).strip().rstrip("/")

    def _movievault_api_token() -> str:
        return str(MOVIEVAULT_API_TOKEN).strip() or str(MOVIEVAULT_API_KEY).strip()

    def _movievault_sharing_mode() -> str:
        mode = _setting_value("movievault_sharing_mode", str(MOVIEVAULT_SHARING_MODE) or "opt_in").strip().lower()
        if mode not in {"opt_in", "opt_out", "disabled"}:
            return "opt_in"
        return mode

    def _metadata_source_order() -> list[str]:
        raw = _setting_value("metadata_source_order", METADATA_SOURCE_ORDER_DEFAULT)
        requested = [x.strip().lower() for x in str(raw).replace(";", ",").split(",")]
        valid = list(metadata_source_labels.keys())
        order = []
        for source in requested:
            if source in valid and source not in order:
                order.append(source)
        for source in valid:
            if source not in order:
                order.append(source)
        return order

    @app.route("/api/settings/sources", methods=["GET"])
    def get_source_settings():
        return jsonify({
            "omdb_enabled": is_source_enabled("omdb_enabled", OMDB_ENABLED_DEFAULT),
            "omdb_key_set": bool(OMDB_API_KEY),
            "tmdb_enabled": is_source_enabled("tmdb_enabled", TMDB_ENABLED_DEFAULT),
            "tmdb_key_set": bool(TMDB_API_KEY),
            "movievault_enabled": True,
            "movievault_url_set": bool(_movievault_search_url()),
            "movievault_key_set": bool(_movievault_api_token()),
            "movievault_contribution_enabled": _bool_setting(
                "movievault_contribution_enabled",
                MOVIEVAULT_CONTRIBUTION_ENABLED_DEFAULT,
            ),
            "movievault_sharing_mode": _movievault_sharing_mode(),
            "metadata_source_order": _metadata_source_order(),
            "metadata_source_order_value": ",".join(_metadata_source_order()),
            "metadata_source_labels": metadata_source_labels,
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
        sharing_mode = str(data.get("movievault_sharing_mode") or "opt_in").strip().lower()
        if sharing_mode not in {"opt_in", "opt_out", "disabled"}:
            sharing_mode = "opt_in"
        contribution_enabled = sharing_mode != "disabled" and bool(data.get("movievault_contribution_enabled", True))
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            ("movievault_contribution_enabled", "true" if contribution_enabled else "false"),
        )
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            ("movievault_sharing_mode", sharing_mode),
        )
        raw_order = str(data.get("metadata_source_order") or "").strip()
        if raw_order:
            requested = [x.strip().lower() for x in raw_order.replace(";", ",").split(",")]
            valid = list(metadata_source_labels.keys())
            order = []
            for source in requested:
                if source in valid and source not in order:
                    order.append(source)
            for source in valid:
                if source not in order:
                    order.append(source)
            order_value = ",".join(order)
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                ("metadata_source_order", order_value),
            )
            result["metadata_source_order"] = order
            result["metadata_source_order_value"] = order_value
        result["movievault_contribution_enabled"] = contribution_enabled
        result["movievault_sharing_mode"] = sharing_mode
        add_log(
            "settings",
            f"MovieVault delen {'ingeschakeld' if contribution_enabled else 'uitgeschakeld'}",
            f"Sharing mode: {sharing_mode}",
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
        movievault_token = _movievault_api_token()
        movievault_search = _movievault_search_url()
        movievault_ingest = _movievault_ingest_url()
        movievault_contribution = str(MOVIEVAULT_CONTRIBUTION_URL).strip()
        return jsonify({
            "tmdb_key_set": bool(tmdb),
            "omdb_key_set": bool(omdb),
            "movievault_key_set": bool(movievault_token),
            "movievault_token_set": bool(movievault_token),
            "movievault_url_set": bool(movievault_search),
            "movievault_search_url_set": bool(movievault_search),
            "movievault_ingest_url_set": bool(movievault_ingest),
            "movievault_contribution_url_set": bool(movievault_contribution),
            "tmdb_key_masked": _mask(tmdb),
            "omdb_key_masked": _mask(omdb),
            "movievault_key_masked": _mask(movievault_token),
            "movievault_token_masked": _mask(movievault_token),
            "tmdb_key": tmdb,
            "omdb_key": omdb,
            "movievault_key": movievault_token,
            "movievault_api_token": movievault_token,
            "movievault_base_url": movievault_search,
            "movievault_search_url": movievault_search,
            "movievault_ingest_url": movievault_ingest,
            "movievault_contribution_url": movievault_contribution,
            "movievault_sharing_mode": _movievault_sharing_mode(),
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
        if any(k in data for k in ("movievault_api_token", "movievault_token", "movievault_key")):
            val = str(
                data.get("movievault_api_token")
                if "movievault_api_token" in data
                else data.get("movievault_token", data.get("movievault_key", ""))
            ).strip()
            masked = ("\u2022" * max(len(val) - 4, 0)) + val[-4:] if val else ""
            if val:
                conn.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                    ("movievault_api_token", val),
                )
                conn.execute("DELETE FROM settings WHERE key=?", ("movievault_api_key",))
                add_log("settings", f"MovieVault API token opgeslagen ({masked})", level="info")
            else:
                conn.execute("DELETE FROM settings WHERE key IN (?, ?)", ("movievault_api_token", "movievault_api_key"))
                add_log("settings", "MovieVault API token verwijderd uit database", level="info")
        if any(k in data for k in ("movievault_search_url", "movievault_base_url")):
            val = str(data.get("movievault_search_url", data.get("movievault_base_url", ""))).strip().rstrip("/")
            if val:
                conn.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                    ("movievault_search_url", val),
                )
                conn.execute("DELETE FROM settings WHERE key=?", ("movievault_base_url",))
                add_log("settings", f"MovieVault search URL opgeslagen: {val}", level="info")
            else:
                conn.execute("DELETE FROM settings WHERE key IN (?, ?)", ("movievault_search_url", "movievault_base_url"))
                add_log("settings", "MovieVault search URL verwijderd uit database", level="info")
        if any(k in data for k in ("movievault_ingest_url", "movievault_contribution_url")):
            val = str(data.get("movievault_ingest_url", data.get("movievault_contribution_url", ""))).strip().rstrip("/")
            if val:
                conn.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                    ("movievault_ingest_url", val),
                )
                add_log("settings", f"MovieVault ingest URL opgeslagen: {val}", level="info")
            else:
                conn.execute("DELETE FROM settings WHERE key=?", ("movievault_ingest_url",))
                add_log("settings", "MovieVault ingest URL verwijderd uit database", level="info")
        if "movievault_sharing_mode" in data:
            mode = str(data.get("movievault_sharing_mode") or "opt_in").strip().lower()
            if mode not in {"opt_in", "opt_out", "disabled"}:
                mode = "opt_in"
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                ("movievault_sharing_mode", mode),
            )
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
