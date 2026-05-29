import re
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup


def _normalize_format(value):
    text = str(value or "").strip().lower()
    if re.search(r"4k|uhd|ultra\s*hd", text):
        return "4K UHD"
    if re.search(r"blu[- ]?ray", text):
        return "Blu-ray"
    if re.search(r"\bdvd\b", text):
        return "DVD"
    return ""


def _headers():
    return {
        "User-Agent": "Mozilla/5.0 (DiscVault Next; +https://discvault.eu)",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://www.blu-ray.com/",
    }


def _abs_url(value):
    value = str(value or "").strip()
    if value.startswith("//"):
        return "https:" + value
    if value.startswith("/"):
        return "https://www.blu-ray.com" + value
    return value


def _is_release_url(value):
    return bool(re.search(r"blu-ray\.com/(?:movies|dvd)/", value or "", flags=re.I))


def _sections(query, preferred_format=""):
    preferred = _normalize_format(preferred_format)
    if preferred == "DVD":
        return ["dvdmovies"]
    if preferred in {"Blu-ray", "4K UHD"}:
        return ["bluraymovies"]
    text = str(query or "").lower()
    if re.fullmatch(r"\d{8,14}", text) or re.search(r"\bdvd\b", text):
        return ["dvdmovies", "bluraymovies"]
    return ["bluraymovies", "dvdmovies"]


def _release_urls(query, preferred_format="", limit=8):
    urls = []

    def add_url(value):
        value = _abs_url(str(value or "").strip().strip("'\""))
        if _is_release_url(value) and value not in urls:
            urls.append(value)

    for section in _sections(query, preferred_format):
        try:
            response = requests.post(
                "https://www.blu-ray.com/search/quicksearch.php",
                data={"section": section, "userid": "-1", "country": "all", "keyword": str(query or "").strip()},
                headers=_headers(),
                timeout=8,
            )
            if response.status_code == 200:
                match = re.search(r"var\s+urls\s*=\s*new\s+Array\(([^)]+)\)", response.text)
                if match:
                    for item in match.group(1).split(","):
                        add_url(item)
            if urls:
                break
        except Exception:
            pass

    if not urls:
        for section in _sections(query, preferred_format):
            try:
                response = requests.get(
                    "https://www.blu-ray.com/search/?quicksearch=1"
                    f"&quicksearch_country=all&section={section}&quicksearch_keyword={quote_plus(str(query or '').strip())}",
                    headers=_headers(),
                    timeout=8,
                )
                if response.status_code == 200:
                    soup = BeautifulSoup(response.text, "html.parser")
                    for link in soup.select('a[href*="/movies/"], a[href*="/dvd/"]'):
                        add_url(link.get("href") or "")
                        if len(urls) >= limit:
                            break
                if urls:
                    break
            except Exception:
                pass
    return urls[:limit]


def _clean_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _extract_hdr(value):
    text = str(value or "")
    tokens = []
    for label in ("Dolby Vision", "HDR10+", "HDR10", "HDR"):
        if re.search(re.escape(label), text, re.I) and label not in tokens:
            tokens.append(label)
    return ", ".join(tokens)


def _split_tracks(value):
    text = _clean_text(value)
    if not text:
        return []
    parts = re.split(r"\s{2,}|(?:\s(?=[A-Z][a-z]+:))", text)
    parts = [_clean_text(part) for part in parts if _clean_text(part)]
    return parts or [text]


def _page_text_by_id(soup, *ids):
    for node_id in ids:
        node = soup.find(id=node_id)
        if node:
            text = _clean_text(node.get_text(" ", strip=True).replace(" less", ""))
            if text:
                return text
    return ""


def _format_from_url_title_text(url, title, text):
    if "/dvd/" in str(url or "").lower():
        return "DVD"
    return _normalize_format(" ".join([str(url or ""), str(title or ""), str(text or "")[:2500]]))


def _parse_page(url):
    response = requests.get(url, headers=_headers(), timeout=10)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    og_title = soup.find("meta", attrs={"property": "og:title"})
    title = _clean_text(og_title.get("content") if og_title else "")
    if not title and soup.find("h1"):
        title = _clean_text(soup.find("h1").get_text(" ", strip=True))
    og_image = soup.find("meta", attrs={"property": "og:image"})
    poster = _abs_url(og_image.get("content") if og_image else "")
    audio = _page_text_by_id(soup, "shortaudio", "longaudio")
    subs = _page_text_by_id(soup, "shortsubs", "longsubs")
    video = _page_text_by_id(soup, "shortvideo", "longvideo")
    if not audio or not subs or not video:
        for row in soup.select("tr"):
            th = row.find("th")
            td = row.find("td")
            if not th or not td:
                continue
            label = th.get_text(" ", strip=True).lower()
            value = _clean_text(td.get_text(" ", strip=True))
            if "audio" in label and not audio:
                audio = value
            elif "subtitle" in label and not subs:
                subs = value
            elif "video" in label and not video:
                video = value
    page_text = soup.get_text(" ", strip=True)
    release_format = _format_from_url_title_text(url, title, page_text)
    year = ""
    match = re.search(r"\((\d{4})\)", title)
    if match:
        year = match.group(1)
    return {
        "status": "hit",
        "provider": "bluray_com",
        "sourceLabel": "Blu-ray.com",
        "sourceRef": url,
        "sourceUrl": url,
        "format": release_format,
        "movie": {
            "title": re.sub(r"\s+\((\d{4})\).*$", "", title).strip() or title,
            "year": year,
            "posterUrl": poster,
            "format": release_format,
        },
        "release": {
            "format": release_format,
            "posterUrl": poster,
        },
        "technicalSpecs": {
            "format": release_format,
            "hdr": _extract_hdr(video or page_text[:8000]),
            "audioTracks": _split_tracks(audio),
            "subtitles": _split_tracks(subs),
        },
    }


def _first_page(query, preferred_format=""):
    urls = _release_urls(query, preferred_format, limit=1)
    return urls[0] if urls else ""


def health_check(context=None):
    return {"status": "available", "message": "Blu-ray.com quicksearch runtime is available."}


def search_title(payload, context=None):
    title = str((payload or {}).get("title") or "").strip()
    year = str((payload or {}).get("year") or "").strip()
    preferred_format = str((payload or {}).get("format") or "").strip()
    query = f"{title} {year}".strip()
    if not query:
        return {"status": "skipped", "provider": "bluray_com", "items": []}
    items = []
    for url in _release_urls(query, preferred_format, limit=8):
        match = re.search(r"/(?:movies|dvd)/([^/]+)/(\d+)", url)
        raw_title = re.sub(r"[-_]+", " ", match.group(1)).strip() if match else ""
        items.append(
            {
                "provider": "bluray_com",
                "providerLabel": "Blu-ray.com",
                "id": match.group(2) if match else url,
                "title": raw_title,
                "sourceUrl": url,
                "format": _normalize_format(url),
            }
        )
    return {"status": "hit" if items else "miss", "provider": "bluray_com", "items": items}


def search_barcode(payload, context=None):
    barcode = str((payload or {}).get("barcode") or "").strip()
    preferred_format = str((payload or {}).get("format") or "").strip()
    if not barcode:
        return {"status": "skipped", "provider": "bluray_com"}
    url = _first_page(barcode, preferred_format)
    if not url:
        return {"status": "miss", "provider": "bluray_com", "barcode": barcode}
    return _parse_page(url)


def technical_specs(payload, context=None):
    title = str((payload or {}).get("title") or "").strip()
    year = str((payload or {}).get("year") or "").strip()
    barcode = str((payload or {}).get("externalBarcode") or (payload or {}).get("barcode") or "").strip()
    preferred_format = str((payload or {}).get("format") or "").strip()
    query = barcode or f"{title} {year}".strip()
    if not query:
        return {"status": "skipped", "provider": "bluray_com"}
    url = _first_page(query, preferred_format)
    if not url:
        for box_set in (payload or {}).get("parentBoxSets") or []:
            parent_barcode = str((box_set or {}).get("barcode") or "").strip()
            if parent_barcode:
                url = _first_page(parent_barcode, preferred_format)
                if url:
                    parsed = _parse_page(url)
                    parsed["sourceContext"] = "box_set_parent"
                    return parsed
        return {"status": "miss", "provider": "bluray_com"}
    return _parse_page(url)


def movie_details(payload, context=None):
    return technical_specs(payload, context)


def box_set_candidates(payload, context=None):
    result = technical_specs(payload, context)
    if result.get("status") != "hit":
        return result
    raw = result.get("movie") or {}
    title = raw.get("title") or str((payload or {}).get("title") or "").strip()
    return {
        "status": "hit",
        "provider": "bluray_com",
        "boxSetProposal": {
            "title": title,
            "source": "Blu-ray.com",
            "detailUrl": result.get("sourceUrl"),
            "detectedWithoutMembers": True,
            "memberConfidence": "candidate",
            "movies": [],
        },
    }
