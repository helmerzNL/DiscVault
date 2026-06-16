"""Shared helpers for file-backed DiscVault Next collection import plugins."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path
from typing import Any


SUPPORTED_EXTENSIONS = {".csv", ".tsv", ".json", ".xml"}

COMMON_ALIASES: dict[str, tuple[str, ...]] = {
    "externalId": ("ID", "Id", "Movie ID", "MovieId", "Film ID", "Collection Number", "Nummer", "Nr", "No"),
    "title": ("Title", "Titel", "Name", "Naam", "Movie", "Movie Title", "Film", "Filmnaam", "Film Titel"),
    "originalTitle": ("Original Title", "Originele titel", "OriginalTitle", "Original Name", "Originele Naam"),
    "year": ("Year", "Jaar", "Release Year", "Movie Year", "Film Year", "Productiejaar", "Releasejaar", "Year Released"),
    "releaseDate": ("Release Date", "Releasedatum", "Date", "Datum", "ReleaseDate", "Release Datum"),
    "barcode": ("Barcode", "UPC", "EAN", "UPC/EAN", "EAN/UPC", "Streepjescode", "UPC EAN", "EAN UPC"),
    "format": ("Format", "Formaat", "Media Type", "Medium", "Type", "Media", "Drager", "Disc Type"),
    "edition": ("Edition", "Editie", "Release", "Version", "Versie", "Uitgave"),
    "country": ("Country", "Land", "Country Code", "Landcode", "Region", "Regio"),
    "language": ("Language", "Taal", "Languages", "Talen", "Audio Language", "Audio Taal"),
    "overview": ("Plot", "Description", "Beschrijving", "Overview", "Synopsis", "Omschrijving", "Samenvatting"),
    "runtime": ("Runtime", "Running Time", "Length", "Speelduur", "Duur", "Minutes", "Minuten"),
    "rating": ("Rating", "Beoordeling", "My Rating", "IMDb Rating", "IMDB Rating"),
    "director": ("Director", "Directors", "Regisseur", "Regisseurs", "Director(s)"),
    "actor": ("Cast", "Actors", "Acteurs", "Stars", "Starring", "Cast Members"),
    "genre": ("Genre", "Genres"),
    "imdbId": ("IMDb ID", "IMDB ID", "IMDb", "IMDB", "IMDb Number", "IMDB Number", "IMDb URL", "IMDB URL", "imdb_id"),
    "tmdbId": ("TMDb ID", "TMDB ID", "TMDb", "TMDB", "TMDb URL", "TMDB URL", "tmdb_id"),
    "poster": ("Poster", "Poster URL", "Cover", "Cover URL", "Afbeelding", "Hoes", "Cover Image"),
    "backdrop": ("Backdrop", "Backdrop URL", "Background", "Achtergrond"),
    "sourceUrl": ("URL", "Link", "Source URL", "Bron URL"),
    "tags": ("Tags", "Labels", "Status"),
    "collection": ("Collection", "Collectie", "List", "Lijst", "Folder", "Map", "Group", "Groep"),
    "boxSet": ("Box Set", "BoxSet", "Boxset", "Set", "Series", "Serie", "Franchise"),
    "isBoxSet": ("IsBoxSet", "Is Box Set", "Box Set?", "Boxset?", "Is Boxset", "Container Type"),
    "boxSetMembers": ("BoxSetMembers", "Box Set Members", "Boxset Members", "Members", "Member Titles", "Box Set Titles"),
    "vault": ("Vault", "Vault Title", "Version Group", "Edition Group"),
    "watchedAt": ("Watched Date", "Bekeken op", "Date Watched", "Viewed At"),
    "watchlisted": ("Watchlist", "Watchlisted", "In Watchlist", "Kijklijst"),
    "verified": ("Verified", "Verified?", "Geverifieerd", "Confirmed"),
}

IMPORT_FIELDS = (
    "externalId",
    "title",
    "originalTitle",
    "year",
    "releaseDate",
    "barcode",
    "format",
    "edition",
    "country",
    "language",
    "overview",
    "runtime",
    "rating",
    "director",
    "actor",
    "genre",
    "imdbId",
    "tmdbId",
    "poster",
    "backdrop",
    "sourceUrl",
    "collection",
    "boxSet",
    "isBoxSet",
    "boxSetMembers",
    "vault",
    "watchedAt",
    "watchlisted",
    "tags",
    "verified",
)


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


def merge_aliases(field: str, aliases: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
    values: list[str] = []
    for alias in tuple(aliases or ()) + COMMON_ALIASES.get(field, ()):
        value = text(alias)
        if value and value not in values:
            values.append(value)
    return tuple(values)


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


def parse_release_date(value: Any) -> str:
    raw = text(value)
    if not raw or re.fullmatch(r"\d{4}", raw):
        return ""
    normalized = raw.replace("/", "-").strip()
    for pattern, order in (
        (r"^(\d{4})-(\d{1,2})-(\d{1,2})$", "ymd"),
        (r"^(\d{1,2})-(\d{1,2})-(\d{4})$", "dmy"),
    ):
        match = re.match(pattern, normalized)
        if not match:
            continue
        parts = [int(part) for part in match.groups()]
        year, month, day = parts if order == "ymd" else (parts[2], parts[1], parts[0])
        try:
            if not 1800 <= year <= 2200:
                return ""
            return date(year, month, day).isoformat()
        except ValueError:
            return ""
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


def split_member_titles(value: Any) -> list[str]:
    raw = text(value)
    if not raw:
        return []
    parts = re.split(r"\s*(?:\||;|\r?\n)\s*", raw)
    members: list[str] = []
    seen: set[str] = set()
    for part in parts:
        title = re.sub(r"\s+", " ", text(part)).strip()
        if not title:
            continue
        key = title.casefold()
        if key in seen:
            continue
        seen.add(key)
        members.append(title)
    return members


def extract_imdb_id(value: Any) -> str:
    raw = text(value)
    if not raw:
        return ""
    match = re.search(r"tt\d{6,10}", raw, flags=re.IGNORECASE)
    if match:
        return match.group(0).lower()
    return raw if re.fullmatch(r"tt\d{6,10}", raw, flags=re.IGNORECASE) else ""


def extract_tmdb_id(value: Any) -> str:
    raw = text(value)
    if not raw:
        return ""
    if raw.isdigit():
        return raw
    match = re.search(r"(?:themoviedb\.org/movie/|tmdb[:#/ ]+)(\d+)", raw, flags=re.IGNORECASE)
    return match.group(1) if match else ""


class CollectionImportPlugin:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.plugin_id = config["id"]
        self.name = config["name"]
        self.default_path = config["defaultPath"]
        self.source_kind = config["sourceKind"]
        self.aliases = config["aliases"]
        self.default_format = config.get("defaultFormat", "")
        self.recognition = config.get("recognition") if isinstance(config.get("recognition"), dict) else {}

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

    def field_aliases(self, field: str) -> tuple[str, ...]:
        return merge_aliases(field, self.aliases.get(field, ()))

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
        column_mapping = column_mapping or {}
        aliases = {field: self.field_aliases(field) for field in IMPORT_FIELDS}
        title = text(mapped_value(row, aliases["title"], column_mapping.get("title")))
        if not title:
            return {}
        barcode = text(mapped_value(row, aliases["barcode"], column_mapping.get("barcode")))
        media_format = text(mapped_value(row, aliases["format"], column_mapping.get("format"))) or self.default_format
        poster = text(mapped_value(row, aliases["poster"], column_mapping.get("poster")))
        backdrop = text(mapped_value(row, aliases["backdrop"], column_mapping.get("backdrop")))
        source_url = text(mapped_value(row, aliases["sourceUrl"], column_mapping.get("sourceUrl")))
        collection_title = text(mapped_value(row, aliases["collection"], column_mapping.get("collectionTitle")))
        box_set_title = text(mapped_value(row, aliases["boxSet"], column_mapping.get("boxSetTitle")))
        member_titles = split_member_titles(mapped_value(row, aliases["boxSetMembers"], column_mapping.get("boxSetMembers")))
        is_box_set = bool_value(mapped_value(row, aliases["isBoxSet"], column_mapping.get("isBoxSet")), default=bool(member_titles))
        verified = bool_value(mapped_value(row, aliases["verified"], column_mapping.get("verified")), default=False)
        if is_box_set and not box_set_title:
            box_set_title = title
        vault_title = text(mapped_value(row, aliases["vault"], column_mapping.get("vaultTitle")))
        imdb_id = extract_imdb_id(mapped_value(row, aliases["imdbId"], column_mapping.get("imdbId")))
        tmdb_id = extract_tmdb_id(mapped_value(row, aliases["tmdbId"], column_mapping.get("tmdbId")))
        raw_year = mapped_value(row, aliases["year"], column_mapping.get("year"))
        raw_release_date = mapped_value(row, aliases["releaseDate"], column_mapping.get("releaseDate"))
        year = parse_year(raw_year) or parse_year(raw_release_date)
        movie = {
            "externalId": text(mapped_value(row, aliases["externalId"], column_mapping.get("externalId"))) or f"{source_file.name}:{index}",
            "title": title,
            "originalTitle": text(mapped_value(row, aliases["originalTitle"], column_mapping.get("originalTitle"))),
            "year": year,
            "releaseDate": parse_release_date(raw_release_date),
            "barcode": barcode,
            "format": media_format,
            "edition": text(mapped_value(row, aliases["edition"], column_mapping.get("edition"))),
            "country": text(mapped_value(row, aliases["country"], column_mapping.get("country"))),
            "language": text(mapped_value(row, aliases["language"], column_mapping.get("language"))),
            "overview": text(mapped_value(row, aliases["overview"], column_mapping.get("overview"))),
            "runtimeMinutes": parse_runtime(mapped_value(row, aliases["runtime"], column_mapping.get("runtime"))),
            "rating": text(mapped_value(row, aliases["rating"], column_mapping.get("rating"))),
            "director": text(mapped_value(row, aliases["director"], column_mapping.get("director"))),
            "actor": text(mapped_value(row, aliases["actor"], column_mapping.get("actor"))),
            "genre": text(mapped_value(row, aliases["genre"], column_mapping.get("genre"))),
            "imdbId": imdb_id,
            "tmdbId": tmdb_id,
            "posterUrl": poster if is_url(poster) else "",
            "backdropUrl": backdrop if is_url(backdrop) else "",
            "sourceUrl": source_url if is_url(source_url) else "",
            "sourceFile": str(source_file),
            "sourceProvider": self.plugin_id,
            "collectionTitle": collection_title,
            "boxSetTitle": box_set_title,
            "vaultTitle": vault_title,
        }
        if is_box_set:
            members = [
                {
                    "title": member_title,
                    "format": media_format,
                    "source": self.name,
                    "sourceProvider": self.plugin_id,
                    "memberConfidence": "file_explicit" if verified else "needs_member_confirmation",
                    "sortOrder": member_index,
                    "sort_order": member_index,
                }
                for member_index, member_title in enumerate(member_titles, start=1)
            ]
            evidence = {
                "barcodeMatch": bool(barcode),
                "entityType": "box_set",
                "memberSource": "import_file",
                "memberConfidence": "file_explicit" if verified and members else "needs_member_confirmation",
                "memberCount": len(members),
                "membersAreExplicit": bool(members),
                "detectedWithoutMembers": not bool(members),
                "format": media_format,
                "sourceRef": f"{source_file.name}:{index}",
            }
            proposal = {
                "title": box_set_title or title,
                "name": box_set_title or title,
                "provider": self.plugin_id,
                "source": self.name,
                "sourceRef": f"{source_file.name}:{index}",
                "barcode": barcode,
                "year": year,
                "format": media_format,
                "members": members,
                "movies": members,
                "memberCount": len(members),
                "member_count": len(members),
                "memberSource": evidence["memberSource"],
                "member_source": evidence["memberSource"],
                "memberConfidence": evidence["memberConfidence"],
                "member_confidence": evidence["memberConfidence"],
                "membersAreExplicit": evidence["membersAreExplicit"],
                "members_are_explicit": evidence["membersAreExplicit"],
                "detectedWithoutMembers": evidence["detectedWithoutMembers"],
                "detected_without_members": evidence["detectedWithoutMembers"],
                "boxSetEvidence": evidence,
                "box_set_evidence": evidence,
            }
            movie.update(
                {
                    "itemType": "box_set",
                    "entityType": "box_set",
                    "isBoxSet": True,
                    "verified": verified,
                    "boxSetMemberTitles": member_titles,
                    "boxSetMembers": members,
                    "boxSetProposal": {key: value for key, value in proposal.items() if value not in (None, "", [], {})},
                    "containers": [
                        {
                            "containerType": "box_set",
                            "title": box_set_title or title,
                            "barcode": barcode,
                            "format": media_format,
                            "memberCount": len(members),
                            "membersAreExplicit": bool(members),
                        }
                    ],
                }
            )
        watched_at = text(mapped_value(row, aliases["watchedAt"], column_mapping.get("watchedAt")))
        watchlisted = bool_value(mapped_value(row, aliases["watchlisted"], column_mapping.get("watchlisted")), default=False)
        tags = text(mapped_value(row, aliases["tags"], column_mapping.get("tags")))
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
            "externalId": self.field_aliases("externalId"),
            "title": self.field_aliases("title"),
            "originalTitle": self.field_aliases("originalTitle"),
            "year": self.field_aliases("year"),
            "releaseDate": self.field_aliases("releaseDate"),
            "barcode": self.field_aliases("barcode"),
            "format": self.field_aliases("format"),
            "edition": self.field_aliases("edition"),
            "country": self.field_aliases("country"),
            "language": self.field_aliases("language"),
            "overview": self.field_aliases("overview"),
            "runtime": self.field_aliases("runtime"),
            "rating": self.field_aliases("rating"),
            "director": self.field_aliases("director"),
            "actor": self.field_aliases("actor"),
            "genre": self.field_aliases("genre"),
            "imdbId": self.field_aliases("imdbId"),
            "tmdbId": self.field_aliases("tmdbId"),
            "poster": self.field_aliases("poster"),
            "backdrop": self.field_aliases("backdrop"),
            "sourceUrl": self.field_aliases("sourceUrl"),
            "collectionTitle": self.field_aliases("collection"),
            "boxSetTitle": self.field_aliases("boxSet"),
            "isBoxSet": self.field_aliases("isBoxSet"),
            "boxSetMembers": self.field_aliases("boxSetMembers"),
            "vaultTitle": self.field_aliases("vault"),
            "watchedAt": self.field_aliases("watchedAt"),
            "watchlisted": self.field_aliases("watchlisted"),
            "tags": self.field_aliases("tags"),
        }
        for field, aliases in field_aliases.items():
            value = text(first_value(row, aliases))
            if value:
                detected[field] = value
        return detected

    def recognition_profile(
        self,
        *,
        source_path: Path,
        files: list[Path],
        columns: list[str],
        items: list[dict[str, Any]],
        detected_mapping: dict[str, str],
    ) -> dict[str, Any]:
        evidence: list[str] = []
        score = 0
        filenames = " ".join([source_path.name, *(path.name for path in files)]).casefold()
        for hint in self.recognition.get("fileNameHints") or ():
            if text(hint).casefold() and text(hint).casefold() in filenames:
                score += 18
                evidence.append(f"filename:{hint}")
                break
        folded_columns = {column.casefold(): column for column in columns}
        for hint in self.recognition.get("columnHints") or ():
            hint_text = text(hint).casefold()
            if hint_text and hint_text in folded_columns:
                score += 12
                evidence.append(f"column:{folded_columns[hint_text]}")
        for field in self.recognition.get("requiredMappedFields") or ():
            if text(field) in detected_mapping:
                score += 10
                evidence.append(f"field:{field}")
        if items:
            score += 18
            evidence.append("readable_items")
        if len(items) >= 5:
            score += 8
            evidence.append("multiple_items")
        score = max(0, min(score, 100))
        label = "strong" if score >= 70 else "possible" if score >= 35 else "generic"
        return {"score": score, "label": label, "evidence": evidence[:10]}

    def inspect_source(self, payload: dict[str, Any] | None = None, context: dict[str, Any] | None = None) -> dict[str, Any]:
        source_path = self.source_path(payload, context)
        column_mapping = self.column_mapping(payload)
        items, warnings, files, columns = self.load_items(source_path, column_mapping)
        found = bool(files)
        readable = bool(items)
        detected_mapping = self.detected_column_mapping(columns)
        effective_mapping = {**detected_mapping, **column_mapping}
        recognition = self.recognition_profile(
            source_path=source_path,
            files=files,
            columns=columns,
            items=items,
            detected_mapping=detected_mapping,
        )
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
                "boxSets": sum(1 for item in items if item.get("itemType") == "box_set" or item.get("isBoxSet") is True),
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
                    "isBoxSet",
                    "boxSetMembers",
                    "vaultTitle",
                    "watchedAt",
                    "watchlisted",
                    "tags",
                ],
            },
            "recognition": recognition,
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
            "counts": {
                "files": len(files),
                "movies": len(items),
                "boxSets": sum(1 for item in items if item.get("itemType") == "box_set" or item.get("isBoxSet") is True),
            },
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
