"""Shared helpers for file-backed DiscVault Next collection import plugins."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


SUPPORTED_EXTENSIONS = {".csv", ".tsv", ".json", ".xml"}


def text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(text(item) for item in value if text(item))
    return str(value).strip()


def first_value(row: dict[str, Any], aliases: tuple[str, ...]) -> Any:
    if not isinstance(row, dict):
        return ""
    direct = {str(key): value for key, value in row.items()}
    folded = {str(key).strip().casefold(): value for key, value in row.items()}
    for alias in aliases:
        if alias in direct and direct[alias] not in (None, "", [], {}):
            return direct[alias]
        folded_value = folded.get(alias.casefold())
        if folded_value not in (None, "", [], {}):
            return folded_value
    return ""


def mapped_value(row: dict[str, Any], aliases: tuple[str, ...], column_name: Any = "") -> Any:
    mapped = text(column_name)
    if mapped:
        value = first_value(row, (mapped,))
        if value not in (None, "", [], {}):
            return value
    return first_value(row, aliases)


def parse_year(value: Any) -> str:
    raw = text(value)
    for idx in range(0, max(len(raw) - 3, 0)):
        candidate = raw[idx : idx + 4]
        if candidate.isdigit() and 1800 <= int(candidate) <= 2200:
            return candidate
    return ""


def parse_runtime(value: Any) -> int | None:
    raw = text(value)
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def is_url(value: Any) -> bool:
    raw = text(value).lower()
    return raw.startswith("http://") or raw.startswith("https://")


def bool_value(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    raw = text(value).casefold()
    if raw in {"1", "true", "yes", "y", "on", "watched", "owned"}:
        return True
    if raw in {"0", "false", "no", "n", "off"}:
        return False
    return default


class CollectionImportPlugin:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.plugin_id = config["id"]
        self.name = config["name"]
        self.default_path = config["defaultPath"]
        self.source_kind = config["sourceKind"]
        self.aliases = config["aliases"]
        self.default_format = config.get("defaultFormat", "")

    def settings(self, context: dict[str, Any] | None) -> dict[str, Any]:
        return (context or {}).get("settings") or {}

    def source_path(self, payload: dict[str, Any] | None = None, context: dict[str, Any] | None = None) -> Path:
        payload = payload or {}
        configured = (
            payload.get("sourcePath")
            or payload.get("source_path")
            or payload.get("file")
            or payload.get("path")
            or self.settings(context).get("sourcePath")
            or os.environ.get(f"{self.plugin_id.upper()}_IMPORT_PATH")
            or self.default_path
        )
        return Path(str(configured)).expanduser()

    def column_mapping(self, payload: dict[str, Any] | None = None) -> dict[str, str]:
        raw = (payload or {}).get("columnMapping") or (payload or {}).get("column_mapping") or {}
        if not isinstance(raw, dict):
            return {}
        return {str(key): text(value) for key, value in raw.items() if text(value)}

    def files(self, source_path: Path) -> list[Path]:
        if source_path.is_file() and source_path.suffix.lower() in SUPPORTED_EXTENSIONS:
            return [source_path]
        if not source_path.exists() or not source_path.is_dir():
            return []
        return sorted(
            item
            for item in source_path.rglob("*")
            if item.is_file() and item.suffix.lower() in SUPPORTED_EXTENSIONS
        )

    def digest(self, files: list[Path]) -> str | None:
        if not files:
            return None
        hasher = hashlib.sha256()
        for path in files:
            hasher.update(str(path).encode("utf-8"))
            hasher.update(str(path.stat().st_size).encode("utf-8"))
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    hasher.update(chunk)
        return hasher.hexdigest()

    def read_rows(self, path: Path) -> list[dict[str, Any]]:
        suffix = path.suffix.lower()
        if suffix in {".csv", ".tsv"}:
            return self.read_csv(path, delimiter="\t" if suffix == ".tsv" else None)
        if suffix == ".json":
            return self.read_json(path)
        if suffix == ".xml":
            return self.read_xml(path)
        return []

    def read_csv(self, path: Path, delimiter: str | None = None) -> list[dict[str, Any]]:
        raw = path.read_text(encoding="utf-8-sig", errors="replace")
        sample = raw[:4096]
        dialect = csv.excel_tab if delimiter == "\t" else csv.Sniffer().sniff(sample, delimiters=",;\t|") if sample.strip() else csv.excel
        reader = csv.DictReader(raw.splitlines(), dialect=dialect)
        return [dict(row) for row in reader if any(text(value) for value in row.values())]

    def read_json(self, path: Path) -> list[dict[str, Any]]:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            for key in ("movies", "items", "collection", "results", "data"):
                value = data.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
            return [data]
        return []

    def read_xml(self, path: Path) -> list[dict[str, Any]]:
        root = ET.parse(path).getroot()
        candidates = []
        for element in root.iter():
            tag = element.tag.split("}")[-1].casefold()
            if tag not in {"movie", "title", "item", "entry", "film"}:
                continue
            row: dict[str, Any] = {key: value for key, value in element.attrib.items()}
            for child in list(element):
                key = child.tag.split("}")[-1]
                value = text(child.text)
                if value:
                    row[key] = value
            if row:
                candidates.append(row)
        return candidates

    def normalize_row(
        self,
        row: dict[str, Any],
        source_file: Path,
        index: int,
        column_mapping: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        aliases = self.aliases
        column_mapping = column_mapping or {}
        title = text(mapped_value(row, aliases["title"], column_mapping.get("title")))
        if not title:
            return {}
        barcode = text(mapped_value(row, aliases.get("barcode", ()), column_mapping.get("barcode")))
        media_format = text(mapped_value(row, aliases.get("format", ()), column_mapping.get("format"))) or self.default_format
        poster = text(mapped_value(row, aliases.get("poster", ()), column_mapping.get("poster")))
        backdrop = text(mapped_value(row, aliases.get("backdrop", ()), column_mapping.get("backdrop")))
        source_url = text(mapped_value(row, aliases.get("sourceUrl", ()), column_mapping.get("sourceUrl")))
        collection_title = text(mapped_value(row, aliases.get("collection", ()), column_mapping.get("collectionTitle")))
        box_set_title = text(mapped_value(row, aliases.get("boxSet", ()), column_mapping.get("boxSetTitle")))
        vault_title = text(mapped_value(row, aliases.get("vault", ()), column_mapping.get("vaultTitle")))
        movie = {
            "externalId": text(mapped_value(row, aliases.get("externalId", ()), column_mapping.get("externalId"))) or f"{source_file.name}:{index}",
            "title": title,
            "originalTitle": text(mapped_value(row, aliases.get("originalTitle", ()), column_mapping.get("originalTitle"))),
            "year": parse_year(mapped_value(row, aliases.get("year", ()), column_mapping.get("year"))),
            "releaseDate": text(mapped_value(row, aliases.get("releaseDate", ()), column_mapping.get("releaseDate"))),
            "barcode": barcode,
            "format": media_format,
            "edition": text(mapped_value(row, aliases.get("edition", ()), column_mapping.get("edition"))),
            "country": text(mapped_value(row, aliases.get("country", ()), column_mapping.get("country"))),
            "language": text(mapped_value(row, aliases.get("language", ()), column_mapping.get("language"))),
            "overview": text(mapped_value(row, aliases.get("overview", ()), column_mapping.get("overview"))),
            "runtimeMinutes": parse_runtime(mapped_value(row, aliases.get("runtime", ()), column_mapping.get("runtime"))),
            "rating": text(mapped_value(row, aliases.get("rating", ()), column_mapping.get("rating"))),
            "director": text(mapped_value(row, aliases.get("director", ()), column_mapping.get("director"))),
            "actor": text(mapped_value(row, aliases.get("actor", ()), column_mapping.get("actor"))),
            "genre": text(mapped_value(row, aliases.get("genre", ()), column_mapping.get("genre"))),
            "imdbId": text(mapped_value(row, aliases.get("imdbId", ()), column_mapping.get("imdbId"))),
            "tmdbId": text(mapped_value(row, aliases.get("tmdbId", ()), column_mapping.get("tmdbId"))),
            "posterUrl": poster if is_url(poster) else "",
            "backdropUrl": backdrop if is_url(backdrop) else "",
            "sourceUrl": source_url if is_url(source_url) else "",
            "sourceFile": str(source_file),
            "sourceProvider": self.plugin_id,
            "collectionTitle": collection_title,
            "boxSetTitle": box_set_title,
            "vaultTitle": vault_title,
        }
        watched_at = text(mapped_value(row, aliases.get("watchedAt", ()), column_mapping.get("watchedAt")))
        watchlisted = bool_value(mapped_value(row, aliases.get("watchlisted", ()), column_mapping.get("watchlisted")), default=False)
        tags = text(mapped_value(row, aliases.get("tags", ()), column_mapping.get("tags")))
        if watched_at or watchlisted or tags:
            movie["personal"] = {
                "watchedAt": watched_at,
                "watchlisted": watchlisted,
                "tags": [item.strip() for item in tags.replace(";", ",").split(",") if item.strip()],
            }
        return {key: value for key, value in movie.items() if value not in (None, "", [], {})}

    def load_items(
        self,
        source_path: Path,
        column_mapping: dict[str, str] | None = None,
    ) -> tuple[list[dict[str, Any]], list[str], list[Path], list[str]]:
        warnings: list[str] = []
        items: list[dict[str, Any]] = []
        files = self.files(source_path)
        columns: list[str] = []
        seen_columns: set[str] = set()
        seen: set[tuple[str, str, str]] = set()
        for path in files:
            try:
                rows = self.read_rows(path)
            except Exception as exc:
                warnings.append(f"{path}: {exc}")
                continue
            for index, row in enumerate(rows, start=1):
                for key in row.keys():
                    column = text(key)
                    if column and column not in seen_columns:
                        seen_columns.add(column)
                        columns.append(column)
                item = self.normalize_row(row, path, index, column_mapping)
                if not item:
                    continue
                key = (
                    text(item.get("barcode")),
                    text(item.get("imdbId") or item.get("tmdbId")),
                    f"{text(item.get('title')).casefold()}:{text(item.get('year'))}",
                )
                if key in seen:
                    continue
                seen.add(key)
                items.append(item)
        return items, warnings, files, columns

    def detected_column_mapping(self, columns: list[str]) -> dict[str, str]:
        row = {column: column for column in columns}
        detected: dict[str, str] = {}
        field_aliases = {
            "externalId": self.aliases.get("externalId", ()),
            "title": self.aliases.get("title", ()),
            "originalTitle": self.aliases.get("originalTitle", ()),
            "year": self.aliases.get("year", ()),
            "releaseDate": self.aliases.get("releaseDate", ()),
            "barcode": self.aliases.get("barcode", ()),
            "format": self.aliases.get("format", ()),
            "edition": self.aliases.get("edition", ()),
            "country": self.aliases.get("country", ()),
            "language": self.aliases.get("language", ()),
            "overview": self.aliases.get("overview", ()),
            "runtime": self.aliases.get("runtime", ()),
            "rating": self.aliases.get("rating", ()),
            "director": self.aliases.get("director", ()),
            "actor": self.aliases.get("actor", ()),
            "genre": self.aliases.get("genre", ()),
            "imdbId": self.aliases.get("imdbId", ()),
            "tmdbId": self.aliases.get("tmdbId", ()),
            "poster": self.aliases.get("poster", ()),
            "backdrop": self.aliases.get("backdrop", ()),
            "sourceUrl": self.aliases.get("sourceUrl", ()),
            "collectionTitle": self.aliases.get("collection", ()),
            "boxSetTitle": self.aliases.get("boxSet", ()),
            "vaultTitle": self.aliases.get("vault", ()),
            "watchedAt": self.aliases.get("watchedAt", ()),
            "watchlisted": self.aliases.get("watchlisted", ()),
            "tags": self.aliases.get("tags", ()),
        }
        for field, aliases in field_aliases.items():
            value = text(first_value(row, aliases))
            if value:
                detected[field] = value
        return detected

    def inspect_source(self, payload: dict[str, Any] | None = None, context: dict[str, Any] | None = None) -> dict[str, Any]:
        source_path = self.source_path(payload, context)
        column_mapping = self.column_mapping(payload)
        items, warnings, files, columns = self.load_items(source_path, column_mapping)
        found = bool(files)
        readable = bool(items)
        detected_mapping = self.detected_column_mapping(columns)
        effective_mapping = {**detected_mapping, **column_mapping}
        return {
            "status": "ok" if readable else "not_found" if not found else "empty",
            "sourceKind": self.source_kind,
            "provider": self.plugin_id,
            "sourcePath": str(source_path),
            "dataDir": str(source_path.parent if source_path.is_file() else source_path),
            "found": found,
            "readable": readable,
            "sourceDatabaseHash": self.digest(files),
            "sourceCounts": {
                "files": len(files),
                "movies": len(items),
                "watchlist": sum(1 for item in items if (item.get("personal") or {}).get("watchlisted")),
                "watched": sum(1 for item in items if (item.get("personal") or {}).get("watchedAt")),
            },
            "mediaExtensions": {},
            "options": {
                "includeSecurity": False,
                "includePersonal": True,
                "importMediaReferences": True,
            },
            "sample": items[:5],
            "mapping": {
                "availableColumns": columns,
                "detected": detected_mapping,
                "effective": effective_mapping,
                "fields": [
                    "title",
                    "originalTitle",
                    "year",
                    "barcode",
                    "format",
                    "edition",
                    "country",
                    "language",
                    "overview",
                    "director",
                    "actor",
                    "genre",
                    "imdbId",
                    "tmdbId",
                    "poster",
                    "backdrop",
                    "sourceUrl",
                    "collectionTitle",
                    "boxSetTitle",
                    "vaultTitle",
                    "watchedAt",
                    "watchlisted",
                    "tags",
                ],
            },
            "warnings": warnings,
        }

    def plan_import(self, payload: dict[str, Any] | None = None, context: dict[str, Any] | None = None) -> dict[str, Any]:
        inspection = self.inspect_source(payload, context)
        can_start = bool(inspection.get("readable"))
        return {
            "status": "ready" if can_start else "blocked",
            "canStart": can_start,
            "source": inspection,
            "jobType": "plugin.execute",
            "jobPayload": {
                "pluginId": self.plugin_id,
                "entrypoint": "import_source",
                "payload": {
                    "sourcePath": inspection.get("sourcePath"),
                    "sourceDatabaseHash": inspection.get("sourceDatabaseHash"),
                },
                "importSource": {
                    "pluginId": self.plugin_id,
                    "sourceKind": self.source_kind,
                },
            },
        }

    def import_source(self, payload: dict[str, Any] | None = None, context: dict[str, Any] | None = None) -> dict[str, Any]:
        source_path = self.source_path(payload, context)
        items, warnings, files, columns = self.load_items(source_path, self.column_mapping(payload))
        expected_hash = text((payload or {}).get("sourceDatabaseHash") or (payload or {}).get("source_database_hash"))
        actual_hash = self.digest(files)
        if expected_hash and actual_hash and expected_hash != actual_hash:
            return {
                "status": "blocked",
                "provider": self.plugin_id,
                "error": "Source files changed after planning; inspect the source again.",
            }
        return {
            "status": "completed",
            "provider": self.plugin_id,
            "sourceKind": self.source_kind,
            "sourcePath": str(source_path),
            "sourceDatabaseHash": actual_hash,
            "items": items,
            "counts": {"files": len(files), "movies": len(items)},
            "mapping": {
                "availableColumns": columns,
                "effective": self.column_mapping(payload),
            },
            "warnings": warnings,
        }

    def health_check(self, context: dict[str, Any] | None = None) -> dict[str, Any]:
        inspection = self.inspect_source({}, context or {})
        if inspection["readable"]:
            return {
                "status": "available",
                "message": f"{self.name} export files detected.",
                "sourceCounts": inspection.get("sourceCounts", {}),
            }
        return {
            "status": "no_source",
            "message": f"No readable {self.name} export files detected.",
            "sourcePath": inspection.get("sourcePath"),
        }
