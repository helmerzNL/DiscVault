"""Canonical TMDB movie genre catalog shared by the plugin, metadata write
pipeline, legacy-data migration, and API layers.

TMDB genre ids are the source of truth: a movie's genre associations are
stored as stable ``key`` strings that map 1:1 to TMDB's numeric genre ids, so
the persisted value never depends on the language TMDB (or a legacy import)
happened to return. Locale labels are only resolved at render/display time
from the frontend i18n catalogs (``genre.<key>`` keys).

This module also carries a best-effort multilingual alias table used to
migrate legacy free-text ``movies.metadata->>'genre'`` values (which were
joined with commas, sometimes in non-English locales, sometimes hand typed)
into the new relational model. Unrecognized tokens are never silently
dropped: callers should keep them in an audit trail.
"""

from __future__ import annotations

import re
from typing import Any

# The 19 TMDB movie genres. `id` is the TMDB numeric genre id (source of
# truth). `key` is the stable, ASCII, snake_case identifier stored in the
# `genres`/`movie_genres` tables. `label` is the English reference label,
# used only as a fallback and for generating other catalogs/tests -- it is
# never persisted as the "current" display value.
GENRE_CATALOG: tuple[dict[str, Any], ...] = (
    {"id": 28, "key": "action", "label": "Action"},
    {"id": 12, "key": "adventure", "label": "Adventure"},
    {"id": 16, "key": "animation", "label": "Animation"},
    {"id": 35, "key": "comedy", "label": "Comedy"},
    {"id": 80, "key": "crime", "label": "Crime"},
    {"id": 99, "key": "documentary", "label": "Documentary"},
    {"id": 18, "key": "drama", "label": "Drama"},
    {"id": 10751, "key": "family", "label": "Family"},
    {"id": 14, "key": "fantasy", "label": "Fantasy"},
    {"id": 36, "key": "history", "label": "History"},
    {"id": 27, "key": "horror", "label": "Horror"},
    {"id": 10402, "key": "music", "label": "Music"},
    {"id": 9648, "key": "mystery", "label": "Mystery"},
    {"id": 10749, "key": "romance", "label": "Romance"},
    {"id": 878, "key": "science_fiction", "label": "Science Fiction"},
    {"id": 10770, "key": "tv_movie", "label": "TV Movie"},
    {"id": 53, "key": "thriller", "label": "Thriller"},
    {"id": 10752, "key": "war", "label": "War"},
    {"id": 37, "key": "western", "label": "Western"},
)

GENRE_KEYS: tuple[str, ...] = tuple(item["key"] for item in GENRE_CATALOG)
GENRE_ID_TO_KEY: dict[int, str] = {item["id"]: item["key"] for item in GENRE_CATALOG}
GENRE_KEY_TO_ID: dict[str, int] = {item["key"]: item["id"] for item in GENRE_CATALOG}
GENRE_KEY_TO_LABEL: dict[str, str] = {item["key"]: item["label"] for item in GENRE_CATALOG}


def canonical_genre_key(value: Any) -> str:
    """Return the canonical genre key for a TMDB numeric genre id, or ''."""
    try:
        genre_id = int(value)
    except (TypeError, ValueError):
        return ""
    return GENRE_ID_TO_KEY.get(genre_id, "")


def genre_keys_from_tmdb_ids(ids: Any) -> list[str]:
    """Convert a list of TMDB genre ids into a deduplicated canonical key list.

    Order follows the TMDB response order; unknown ids are dropped (TMDB's 19
    movie genres are a closed set, so an unknown id most likely indicates a
    future catalog addition we don't yet know about).
    """
    if ids is None:
        return []
    if not isinstance(ids, (list, tuple, set)):
        ids = [ids]
    keys: list[str] = []
    seen: set[str] = set()
    for raw in ids:
        key = canonical_genre_key(raw)
        if key and key not in seen:
            seen.add(key)
            keys.append(key)
    return keys


def normalize_genre_keys(values: Any) -> list[str]:
    """Return a deduplicated, catalog-validated list of canonical genre keys."""
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple, set)):
        return []
    keys: list[str] = []
    seen: set[str] = set()
    for raw in values:
        key = str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")
        if key in GENRE_KEY_TO_ID and key not in seen:
            seen.add(key)
            keys.append(key)
    return keys


# Best-effort translations of the 19 canonical genre labels, keyed by the
# BCP-47 locale codes DiscVault ships under app/frontend/i18n/next/. These
# feed both the i18n catalogs (via scripts/tooling) and the legacy migration
# alias table below, so a translated label always doubles as a recognized
# alias for that language.
GENRE_TRANSLATIONS: dict[str, dict[str, str]] = {
    "en-US": {
        "action": "Action", "adventure": "Adventure", "animation": "Animation",
        "comedy": "Comedy", "crime": "Crime", "documentary": "Documentary",
        "drama": "Drama", "family": "Family", "fantasy": "Fantasy",
        "history": "History", "horror": "Horror", "music": "Music",
        "mystery": "Mystery", "romance": "Romance", "science_fiction": "Science Fiction",
        "tv_movie": "TV Movie", "thriller": "Thriller", "war": "War", "western": "Western",
    },
    "nl-NL": {
        "action": "Actie", "adventure": "Avontuur", "animation": "Animatie",
        "comedy": "Komedie", "crime": "Misdaad", "documentary": "Documentaire",
        "drama": "Drama", "family": "Familie", "fantasy": "Fantasie",
        "history": "Geschiedenis", "horror": "Horror", "music": "Muziek",
        "mystery": "Mysterie", "romance": "Romantiek", "science_fiction": "Sciencefiction",
        "tv_movie": "Tv-film", "thriller": "Thriller", "war": "Oorlogsfilm", "western": "Western",
    },
    "de-DE": {
        "action": "Action", "adventure": "Abenteuer", "animation": "Animation",
        "comedy": "Komödie", "crime": "Krimi", "documentary": "Dokumentarfilm",
        "drama": "Drama", "family": "Familie", "fantasy": "Fantasy",
        "history": "Historie", "horror": "Horror", "music": "Musik",
        "mystery": "Mystery", "romance": "Liebesfilm", "science_fiction": "Science Fiction",
        "tv_movie": "TV-Film", "thriller": "Thriller", "war": "Kriegsfilm", "western": "Western",
    },
    "fr-FR": {
        "action": "Action", "adventure": "Aventure", "animation": "Animation",
        "comedy": "Comédie", "crime": "Crime", "documentary": "Documentaire",
        "drama": "Drame", "family": "Familial", "fantasy": "Fantastique",
        "history": "Histoire", "horror": "Horreur", "music": "Musique",
        "mystery": "Mystère", "romance": "Romance", "science_fiction": "Science-Fiction",
        "tv_movie": "Téléfilm", "thriller": "Thriller", "war": "Guerre", "western": "Western",
    },
    "es-ES": {
        "action": "Acción", "adventure": "Aventura", "animation": "Animación",
        "comedy": "Comedia", "crime": "Crimen", "documentary": "Documental",
        "drama": "Drama", "family": "Familia", "fantasy": "Fantasía",
        "history": "Historia", "horror": "Terror", "music": "Música",
        "mystery": "Misterio", "romance": "Romance", "science_fiction": "Ciencia ficción",
        "tv_movie": "Película de televisión", "thriller": "Suspense", "war": "Bélica", "western": "Western",
    },
    "it-IT": {
        "action": "Azione", "adventure": "Avventura", "animation": "Animazione",
        "comedy": "Commedia", "crime": "Crime", "documentary": "Documentario",
        "drama": "Drammatico", "family": "Famiglia", "fantasy": "Fantasy",
        "history": "Storia", "horror": "Horror", "music": "Musica",
        "mystery": "Mistero", "romance": "Romantico", "science_fiction": "Fantascienza",
        "tv_movie": "Film TV", "thriller": "Thriller", "war": "Guerra", "western": "Western",
    },
    "pt-PT": {
        "action": "Ação", "adventure": "Aventura", "animation": "Animação",
        "comedy": "Comédia", "crime": "Crime", "documentary": "Documentário",
        "drama": "Drama", "family": "Família", "fantasy": "Fantasia",
        "history": "História", "horror": "Terror", "music": "Música",
        "mystery": "Mistério", "romance": "Romance", "science_fiction": "Ficção científica",
        "tv_movie": "Telefilme", "thriller": "Thriller", "war": "Guerra", "western": "Faroeste",
    },
    "pl-PL": {
        "action": "Akcja", "adventure": "Przygodowy", "animation": "Animacja",
        "comedy": "Komedia", "crime": "Kryminał", "documentary": "Dokumentalny",
        "drama": "Dramat", "family": "Familijny", "fantasy": "Fantasy",
        "history": "Historyczny", "horror": "Horror", "music": "Muzyczny",
        "mystery": "Tajemnica", "romance": "Romans", "science_fiction": "Fantastyka naukowa",
        "tv_movie": "Film TV", "thriller": "Thriller", "war": "Wojenny", "western": "Western",
    },
    "cs-CZ": {
        "action": "Akční", "adventure": "Dobrodružný", "animation": "Animovaný",
        "comedy": "Komedie", "crime": "Krimi", "documentary": "Dokumentární",
        "drama": "Drama", "family": "Rodinný", "fantasy": "Fantasy",
        "history": "Historický", "horror": "Horor", "music": "Hudební",
        "mystery": "Mysteriózní", "romance": "Romantický", "science_fiction": "Sci-Fi",
        "tv_movie": "Televizní film", "thriller": "Thriller", "war": "Válečný", "western": "Western",
    },
    "sk-SK": {
        "action": "Akčný", "adventure": "Dobrodružný", "animation": "Animovaný",
        "comedy": "Komédia", "crime": "Krimi", "documentary": "Dokumentárny",
        "drama": "Dráma", "family": "Rodinný", "fantasy": "Fantasy",
        "history": "Historický", "horror": "Horor", "music": "Hudobný",
        "mystery": "Mysteriózny", "romance": "Romantický", "science_fiction": "Sci-Fi",
        "tv_movie": "Televízny film", "thriller": "Thriller", "war": "Vojnový", "western": "Western",
    },
    "hu-HU": {
        "action": "Akció", "adventure": "Kaland", "animation": "Animációs",
        "comedy": "Vígjáték", "crime": "Bűnügyi", "documentary": "Dokumentumfilm",
        "drama": "Drámai", "family": "Családi", "fantasy": "Fantasy",
        "history": "Történelmi", "horror": "Horror", "music": "Musical",
        "mystery": "Rejtély", "romance": "Romantikus", "science_fiction": "Sci-Fi",
        "tv_movie": "Tévéfilm", "thriller": "Thriller", "war": "Háborús", "western": "Western",
    },
    "ro-RO": {
        "action": "Acțiune", "adventure": "Aventură", "animation": "Animație",
        "comedy": "Comedie", "crime": "Crimă", "documentary": "Documentar",
        "drama": "Dramă", "family": "Familie", "fantasy": "Fantezie",
        "history": "Istoric", "horror": "Groază", "music": "Muzical",
        "mystery": "Mister", "romance": "Romantic", "science_fiction": "Science Fiction",
        "tv_movie": "Film TV", "thriller": "Thriller", "war": "Război", "western": "Western",
    },
    "bg-BG": {
        "action": "Екшън", "adventure": "Приключенски", "animation": "Анимация",
        "comedy": "Комедия", "crime": "Криминален", "documentary": "Документален",
        "drama": "Драма", "family": "Семеен", "fantasy": "Фентъзи",
        "history": "Исторически", "horror": "Хорър", "music": "Музикален",
        "mystery": "Мистерия", "romance": "Романтичен", "science_fiction": "Научна фантастика",
        "tv_movie": "Телевизионен филм", "thriller": "Трилър", "war": "Военен", "western": "Уестърн",
    },
    "uk-UA": {
        "action": "Бойовик", "adventure": "Пригоди", "animation": "Анімація",
        "comedy": "Комедія", "crime": "Кримінал", "documentary": "Документальний",
        "drama": "Драма", "family": "Сімейний", "fantasy": "Фентезі",
        "history": "Історичний", "horror": "Жахи", "music": "Музика",
        "mystery": "Детектив", "romance": "Романтика", "science_fiction": "Наукова фантастика",
        "tv_movie": "Телефільм", "thriller": "Трилер", "war": "Військовий", "western": "Вестерн",
    },
    "hr-HR": {
        "action": "Akcija", "adventure": "Pustolovni", "animation": "Animirani",
        "comedy": "Komedija", "crime": "Kriminalistički", "documentary": "Dokumentarni",
        "drama": "Drama", "family": "Obiteljski", "fantasy": "Fantastika",
        "history": "Povijesni", "horror": "Horor", "music": "Glazbeni",
        "mystery": "Misterija", "romance": "Ljubavni", "science_fiction": "Znanstvena fantastika",
        "tv_movie": "TV film", "thriller": "Triler", "war": "Ratni", "western": "Western",
    },
    "sl-SI": {
        "action": "Akcija", "adventure": "Pustolovščina", "animation": "Animirani",
        "comedy": "Komedija", "crime": "Kriminalka", "documentary": "Dokumentarni",
        "drama": "Drama", "family": "Družinski", "fantasy": "Fantazija",
        "history": "Zgodovinski", "horror": "Grozljivka", "music": "Glasba",
        "mystery": "Skrivnostni", "romance": "Romantika", "science_fiction": "Znanstvena fantastika",
        "tv_movie": "TV film", "thriller": "Triler", "war": "Vojni", "western": "Western",
    },
    "et-EE": {
        "action": "Märulifilm", "adventure": "Seiklusfilm", "animation": "Animatsioon",
        "comedy": "Komöödia", "crime": "Kriminaal", "documentary": "Dokumentaalfilm",
        "drama": "Draama", "family": "Perefilm", "fantasy": "Fantaasia",
        "history": "Ajalooline", "horror": "Õudusfilm", "music": "Muusika",
        "mystery": "Mõistatus", "romance": "Romantika", "science_fiction": "Ulme",
        "tv_movie": "TV-film", "thriller": "Triller", "war": "Sõjafilm", "western": "Vestern",
    },
    "lv-LV": {
        "action": "Akcijas filma", "adventure": "Piedzīvojumu filma", "animation": "Animācijas filma",
        "comedy": "Komēdija", "crime": "Noziegumu filma", "documentary": "Dokumentālā filma",
        "drama": "Drāma", "family": "Ģimenes filma", "fantasy": "Fantāzija",
        "history": "Vēsturiska filma", "horror": "Šausmu filma", "music": "Mūzikls",
        "mystery": "Mistērija", "romance": "Romantika", "science_fiction": "Zinātniskā fantastika",
        "tv_movie": "TV filma", "thriller": "Triller", "war": "Kara filma", "western": "Vesterns",
    },
    "lt-LT": {
        "action": "Veiksmo", "adventure": "Nuotykių", "animation": "Animacinis",
        "comedy": "Komedija", "crime": "Kriminalinis", "documentary": "Dokumentinis",
        "drama": "Drama", "family": "Šeimos", "fantasy": "Fantastinis",
        "history": "Istorinis", "horror": "Siaubo", "music": "Muzikinis",
        "mystery": "Detektyvas", "romance": "Romantinis", "science_fiction": "Mokslinė fantastika",
        "tv_movie": "TV filmas", "thriller": "Trileris", "war": "Karo", "western": "Vesternas",
    },
    "fi-FI": {
        "action": "Toiminta", "adventure": "Seikkailu", "animation": "Animaatio",
        "comedy": "Komedia", "crime": "Rikos", "documentary": "Dokumentti",
        "drama": "Draama", "family": "Perhe", "fantasy": "Fantasia",
        "history": "Historia", "horror": "Kauhu", "music": "Musiikki",
        "mystery": "Mysteeri", "romance": "Romantiikka", "science_fiction": "Tieteiselokuva",
        "tv_movie": "TV-elokuva", "thriller": "Jännitys", "war": "Sota", "western": "Western",
    },
    "sv-SE": {
        "action": "Action", "adventure": "Äventyr", "animation": "Animerat",
        "comedy": "Komedi", "crime": "Kriminal", "documentary": "Dokumentär",
        "drama": "Drama", "family": "Familj", "fantasy": "Fantasy",
        "history": "Historia", "horror": "Skräck", "music": "Musik",
        "mystery": "Mysterium", "romance": "Romantik", "science_fiction": "Science Fiction",
        "tv_movie": "TV-film", "thriller": "Thriller", "war": "Krig", "western": "Western",
    },
    "nb-NO": {
        "action": "Action", "adventure": "Eventyr", "animation": "Animasjon",
        "comedy": "Komedie", "crime": "Krim", "documentary": "Dokumentar",
        "drama": "Drama", "family": "Familie", "fantasy": "Fantasy",
        "history": "Historie", "horror": "Skrekk", "music": "Musikk",
        "mystery": "Mysterie", "romance": "Romantikk", "science_fiction": "Science fiction",
        "tv_movie": "TV-film", "thriller": "Thriller", "war": "Krig", "western": "Western",
    },
    "da-DK": {
        "action": "Action", "adventure": "Eventyr", "animation": "Animation",
        "comedy": "Komedie", "crime": "Krimi", "documentary": "Dokumentar",
        "drama": "Drama", "family": "Familiefilm", "fantasy": "Fantasy",
        "history": "Historisk", "horror": "Gyser", "music": "Musik",
        "mystery": "Mysterium", "romance": "Romantisk", "science_fiction": "Science fiction",
        "tv_movie": "Tv-film", "thriller": "Thriller", "war": "Krig", "western": "Western",
    },
    "el-GR": {
        "action": "Δράση", "adventure": "Περιπέτεια", "animation": "Κινούμενα σχέδια",
        "comedy": "Κωμωδία", "crime": "Έγκλημα", "documentary": "Ντοκιμαντέρ",
        "drama": "Δράμα", "family": "Οικογενειακή", "fantasy": "Φαντασία",
        "history": "Ιστορική", "horror": "Τρόμου", "music": "Μουσική",
        "mystery": "Μυστηρίου", "romance": "Ρομαντική", "science_fiction": "Επιστημονική φαντασία",
        "tv_movie": "Τηλεοπτική ταινία", "thriller": "Θρίλερ", "war": "Πολεμική", "western": "Γουέστερν",
    },
    "tr-TR": {
        "action": "Aksiyon", "adventure": "Macera", "animation": "Animasyon",
        "comedy": "Komedi", "crime": "Suç", "documentary": "Belgesel",
        "drama": "Drama", "family": "Aile", "fantasy": "Fantastik",
        "history": "Tarih", "horror": "Korku", "music": "Müzik",
        "mystery": "Gizem", "romance": "Romantik", "science_fiction": "Bilim kurgu",
        "tv_movie": "TV filmi", "thriller": "Gerilim", "war": "Savaş", "western": "Vahşi batı",
    },
    "ja-JP": {
        "action": "アクション", "adventure": "アドベンチャー", "animation": "アニメーション",
        "comedy": "コメディ", "crime": "犯罪", "documentary": "ドキュメンタリー",
        "drama": "ドラマ", "family": "ファミリー", "fantasy": "ファンタジー",
        "history": "歴史", "horror": "ホラー", "music": "音楽",
        "mystery": "ミステリー", "romance": "恋愛", "science_fiction": "SF",
        "tv_movie": "TVムービー", "thriller": "スリラー", "war": "戦争", "western": "西部劇",
    },
    "ko-KR": {
        "action": "액션", "adventure": "어드벤처", "animation": "애니메이션",
        "comedy": "코미디", "crime": "범죄", "documentary": "다큐멘터리",
        "drama": "드라마", "family": "가족", "fantasy": "판타지",
        "history": "역사", "horror": "공포", "music": "음악",
        "mystery": "미스터리", "romance": "로맨스", "science_fiction": "SF",
        "tv_movie": "TV 영화", "thriller": "스릴러", "war": "전쟁", "western": "서부",
    },
    "zh-CN": {
        "action": "动作", "adventure": "冒险", "animation": "动画",
        "comedy": "喜剧", "crime": "犯罪", "documentary": "纪录",
        "drama": "剧情", "family": "家庭", "fantasy": "奇幻",
        "history": "历史", "horror": "恐怖", "music": "音乐",
        "mystery": "悬疑", "romance": "爱情", "science_fiction": "科幻",
        "tv_movie": "电视电影", "thriller": "惊悚", "war": "战争", "western": "西部",
    },
    "zh-TW": {
        "action": "動作", "adventure": "冒險", "animation": "動畫",
        "comedy": "喜劇", "crime": "犯罪", "documentary": "紀錄片",
        "drama": "劇情", "family": "家庭", "fantasy": "奇幻",
        "history": "歷史", "horror": "恐怖", "music": "音樂",
        "mystery": "懸疑", "romance": "愛情", "science_fiction": "科幻",
        "tv_movie": "電視電影", "thriller": "驚悚", "war": "戰爭", "western": "西部",
    },
}

# Extra spellings/abbreviations that show up in hand-typed or older TMDB
# payloads but aren't the "current" translation captured above.
EXTRA_GENRE_ALIASES: dict[str, str] = {
    "sci-fi": "science_fiction",
    "sci fi": "science_fiction",
    "scifi": "science_fiction",
    "science fiction": "science_fiction",
    "science-fiction": "science_fiction",
    "sciencefiction": "science_fiction",
    "sf": "science_fiction",
    "tv film": "tv_movie",
    "tv-film": "tv_movie",
    "tvfilm": "tv_movie",
    "tv movie": "tv_movie",
    "television film": "tv_movie",
    "romantic": "romance",
    "romantiek": "romance",
    "war film": "war",
    "war movie": "war",
    "oorlog": "war",
    "misdaadfilm": "crime",
    "muziekfilm": "music",
    "musical": "music",
    "documentaire film": "documentary",
    "avonturenfilm": "adventure",
    "actiefilm": "action",
    "komedie film": "comedy",
    "griezelfilm": "horror",
    "detective": "mystery",
    "krimi": "crime",
}


def _normalize_alias(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().casefold())


def _build_legacy_aliases() -> dict[str, str]:
    aliases: dict[str, str] = {}
    for key, label in GENRE_KEY_TO_LABEL.items():
        aliases[_normalize_alias(label)] = key
    for locale_labels in GENRE_TRANSLATIONS.values():
        for key, label in locale_labels.items():
            normalized = _normalize_alias(label)
            aliases.setdefault(normalized, key)
    for alias, key in EXTRA_GENRE_ALIASES.items():
        aliases.setdefault(_normalize_alias(alias), key)
    return aliases


# casefolded/whitespace-normalized alias text -> canonical genre key. Built
# once at import time from every known translation plus a handful of
# well-known variant spellings.
LEGACY_GENRE_ALIASES: dict[str, str] = _build_legacy_aliases()

_LEGACY_SPLIT_PATTERN = re.compile(
    r"\s*(?:,|/|\||;|&|\u3001|\u30fb)\s*"
    r"|\s+(?:and|en|und|et)\s+",
    re.IGNORECASE,
)


def split_legacy_genre_text(raw: str) -> list[str]:
    """Split a legacy combined genre string into individual tokens."""
    text = str(raw or "").strip()
    if not text:
        return []
    parts = [part.strip() for part in _LEGACY_SPLIT_PATTERN.split(text)]
    return [part for part in parts if part]


def map_legacy_genre_text(raw: str) -> tuple[list[str], list[str]]:
    """Split and alias-map a legacy `metadata->>'genre'` value.

    Returns a ``(matched_keys, unmatched_parts)`` tuple. ``matched_keys`` is a
    deduplicated, catalog-validated list of canonical genre keys in first-seen
    order. ``unmatched_parts`` preserves the original (unmapped) token text
    for audit purposes; it is never treated as a genre.
    """
    matched: list[str] = []
    seen: set[str] = set()
    unmatched: list[str] = []
    for part in split_legacy_genre_text(raw):
        key = LEGACY_GENRE_ALIASES.get(_normalize_alias(part))
        if key and key not in seen:
            seen.add(key)
            matched.append(key)
        elif not key:
            unmatched.append(part)
    return matched, unmatched
