import re
from urllib.parse import quote_plus

import requests

try:
    from bs4 import BeautifulSoup
except Exception:  # pragma: no cover - dependency is present in the container image.
    BeautifulSoup = None


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
                if response.status_code == 200 and BeautifulSoup is not None:
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


def _movie_title_from_release_title(value):
    title = _clean_text(value)
    if not title:
        return ""
    title = re.sub(r"\s+\((\d{4})\).*$", "", title).strip()
    title = re.sub(r"\s+\((?:SteelBook|Steelbook|France|Germany|Italy|Spain|UK|US|USA|Canada|Netherlands|Import)\)\s*$", "", title, flags=re.I).strip()
    title = re.sub(r"\s+\((?:SteelBook|Steelbook|France|Germany|Italy|Spain|UK|US|USA|Canada|Netherlands|Import)\)\s*$", "", title, flags=re.I).strip()
    title = re.sub(
        r"\s+(?:4K\s*)?(?:Ultra\s*HD\s*)?Blu[- ]?ray(?:\s*3D)?(?:\s*\+\s*Blu[- ]?ray)?\s*(?:Review)?\s*$",
        "",
        title,
        flags=re.I,
    ).strip()
    title = re.sub(r"\s+(?:DVD|HD DVD|LaserDisc|VCD/SVCD|Digital|Review)\s*$", "", title, flags=re.I).strip()
    return title or _clean_text(value)


def _member_title_from_url(value):
    match = re.search(r"/(?:movies|dvd)/([^/]+)/\d+", str(value or ""), flags=re.I)
    if not match:
        return ""
    return _movie_title_from_release_title(re.sub(r"[-_]+", " ", match.group(1)).strip())


def _is_box_set_candidate(title, page_text):
    text = f"{title or ''} {page_text or ''}"[:5000].casefold()
    markers = (
        "box set",
        "boxset",
        "collection",
        "trilogy",
        "quadrilogy",
        "anthology",
        "complete",
        "movie set",
        "film set",
        "bundle",
    )
    if any(marker in text for marker in markers):
        return True
    return bool(re.search(r"\b\d+\s*(?:movie|film|disc|dvd)s?\b", text, flags=re.I))


def _has_explicit_member_text(soup):
    movie_info = soup.select_one("#movie_info")
    if not movie_info:
        return False
    text = _clean_text(movie_info.get_text(" ", strip=True)).casefold()
    explicit_markers = (
        "this blu-ray bundle includes the following titles",
        "the blu-ray bundle includes the following titles",
        "this bundle includes the following titles",
        "bundle includes the following titles",
        "includes the following titles",
    )
    return any(marker in text for marker in explicit_markers)


def _link_is_in_related_block(link):
    related_markers = ("similar", "customers who bought", "related", "votesimilar", "automatic_")
    node = link
    while node is not None:
        node_id = str(node.get("id") or "").casefold() if hasattr(node, "get") else ""
        node_class = " ".join(str(item) for item in (node.get("class") or [])).casefold() if hasattr(node, "get") else ""
        if any(marker in node_id or marker in node_class for marker in related_markers):
            return True
        node = getattr(node, "parent", None)
    return False


def _extract_explicit_box_set_members(soup, *, current_url="", parent_title="", release_format=""):
    members = []
    seen = set()
    parent_key = _movie_title_from_release_title(parent_title).casefold()
    current_url = _abs_url(current_url)

    movie_info = soup.select_one("#movie_info")
    if not movie_info or not _has_explicit_member_text(soup):
        return []

    def add_member(link, index):
        href = _abs_url(link.get("href") or "")
        if not re.search(r"/movies/", href, flags=re.I):
            return
        if "hoverlink" not in (link.get("class") or []):
            return
        if not link.get("data-globalparentid") or not link.get("data-productid"):
            return
        image = link.find("img")
        if image is None:
            return
        poster = _abs_url(image.get("src") or image.get("data-src") or image.get("data-original") or "")
        title = _movie_title_from_release_title(
            link.get("title")
            or image.get("alt")
            or link.get_text(" ", strip=True)
            or _member_title_from_url(href)
        )
        href = _abs_url(href)
        if not title:
            return
        if _link_is_in_related_block(link):
            return
        key = (title.casefold(), href)
        if key in seen:
            return
        if href and href == current_url:
            return
        if parent_key and title.casefold() == parent_key:
            return
        seen.add(key)
        match = re.search(r"/(?:movies|dvd)/[^/]+/(\d+)", href, flags=re.I)
        label = _clean_text(link.get_text(" ", strip=True))
        disc_match = re.search(r"\bdisc\s*(\d+)\b", label or "", flags=re.I)
        members.append(
            {
                "title": title,
                "year": (re.search(r"\((\d{4})\)", link.get("title") or image.get("alt") or label or "") or ["", ""])[1],
                "posterUrl": poster,
                "format": release_format,
                "source": "Blu-ray.com",
                "sourceUrl": href,
                "sourceRef": match.group(1) if match else href,
                "sortOrder": index,
                "sort_order": index,
                "discNumber": disc_match.group(1) if disc_match else "",
                "disc_number": disc_match.group(1) if disc_match else "",
                "memberConfidence": "needs_member_confirmation",
            }
        )

    for index, link in enumerate(
        movie_info.select('a.hoverlink[data-globalparentid][data-productid][href*="/movies/"]'),
        start=1,
    ):
        add_member(link, len(members) + 1)
        if len(members) >= 30:
            break

    return [member for member in members if member.get("title")][:30]


def _candidate_members_from_search(title, preferred_format="", current_url=""):
    clean_title = _movie_title_from_release_title(title)
    if not clean_title:
        return []
    members = []
    seen = set()
    for url in _release_urls(clean_title, preferred_format, limit=12):
        url = _abs_url(url)
        if current_url and url == _abs_url(current_url):
            continue
        member_title = _member_title_from_url(url)
        if not member_title:
            continue
        key = member_title.casefold()
        if key in seen:
            continue
        seen.add(key)
        match = re.search(r"/(?:movies|dvd)/[^/]+/(\d+)", url, flags=re.I)
        members.append(
            {
                "title": member_title,
                "format": _normalize_format(url) or _normalize_format(preferred_format),
                "source": "Blu-ray.com candidate search",
                "sourceUrl": url,
                "sourceRef": match.group(1) if match else url,
                "sortOrder": len(members) + 1,
                "sort_order": len(members) + 1,
                "memberConfidence": "candidate",
            }
        )
    return members[:30]


def _box_set_evidence(*, members, result, payload=None, explicit=False, candidate=False):
    payload = payload or {}
    members = members if isinstance(members, list) else []
    source_ref = result.get("sourceRef") or result.get("sourceUrl") or result.get("source_url") or ""
    return {
        "barcodeMatch": False,
        "entityType": "box_set",
        "memberSource": "Blu-ray.com release page" if explicit else "metadata_candidates",
        "memberConfidence": "needs_member_confirmation" if explicit else "candidate",
        "memberCount": len(members),
        "membersAreExplicit": bool(explicit and members),
        "detectedWithoutMembers": bool(candidate or not explicit),
        "format": result.get("format") or payload.get("format") or payload.get("mediaFormat") or payload.get("media_type") or "",
        "sourceRef": source_ref,
    }


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
    if BeautifulSoup is None:
        raise RuntimeError("BeautifulSoup is required for Blu-ray.com page parsing.")
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
    is_box_set_candidate = _is_box_set_candidate(title, page_text[:5000])
    box_set_members = _extract_explicit_box_set_members(
        soup,
        current_url=url,
        parent_title=title,
        release_format=release_format,
    ) if is_box_set_candidate else []
    year = ""
    match = re.search(r"\((\d{4})\)", title)
    if match:
        year = match.group(1)
    parsed = {
        "status": "hit",
        "provider": "bluray_com",
        "sourceLabel": "Blu-ray.com",
        "sourceRef": url,
        "sourceUrl": url,
        "format": release_format,
        "releaseTitle": title,
        "movie": {
            "title": _movie_title_from_release_title(title),
            "year": year,
            "posterUrl": poster,
            "format": release_format,
        },
        "release": {
            "title": title,
            "format": release_format,
            "posterUrl": poster,
        },
        "technicalSpecs": {
            "format": release_format,
            "hdr": _extract_hdr(video or page_text[:8000]),
            "audioTracks": _split_tracks(audio),
            "subtitles": _split_tracks(subs),
        },
        "isBoxSetCandidate": is_box_set_candidate,
        "boxSetMembers": box_set_members,
    }
    if is_box_set_candidate:
        parsed["boxSetEvidence"] = _box_set_evidence(
            members=box_set_members,
            result=parsed,
            explicit=bool(len(box_set_members) >= 2),
            candidate=bool(len(box_set_members) < 2),
        )
    return parsed


def _first_page(query, preferred_format=""):
    urls = _release_urls(query, preferred_format, limit=1)
    return urls[0] if urls else ""


def health_check(context=None):
    if BeautifulSoup is None:
        return {"status": "unavailable", "message": "BeautifulSoup is required for Blu-ray.com parsing."}
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
    if not result.get("isBoxSetCandidate"):
        return {"status": "miss", "provider": "bluray_com", "boxSetProposal": {}}
    members = result.get("boxSetMembers") or []
    if len(members) < 2:
        members = _candidate_members_from_search(title, result.get("format") or (payload or {}).get("format"), result.get("sourceUrl"))
        if len(members) < 2:
            return {"status": "miss", "provider": "bluray_com", "boxSetProposal": {}}
        evidence = _box_set_evidence(members=members, result=result, payload=payload, candidate=True)
        return {
            "status": "hit",
            "provider": "bluray_com",
            "boxSetProposal": {
                "title": title,
                "source": "Blu-ray.com",
                "detailUrl": result.get("sourceUrl"),
                "posterUrl": result.get("movie", {}).get("posterUrl") or result.get("release", {}).get("posterUrl"),
                "poster_url": result.get("movie", {}).get("posterUrl") or result.get("release", {}).get("posterUrl"),
                "poster": result.get("movie", {}).get("posterUrl") or result.get("release", {}).get("posterUrl"),
                "detectedWithoutMembers": True,
                "memberConfidence": "candidate",
                "memberSource": "metadata_candidates",
                "member_source": "metadata_candidates",
                "detectedMemberHintCount": len(members),
                "members": members,
                "movies": members,
                "memberCount": len(members),
                "member_count": len(members),
                "boxSetEvidence": evidence,
                "box_set_evidence": evidence,
                "membersAreExplicit": False,
                "members_are_explicit": False,
            },
        }
    evidence = _box_set_evidence(members=members, result=result, payload=payload, explicit=True)
    return {
        "status": "hit",
        "provider": "bluray_com",
        "boxSetProposal": {
            "title": title,
            "source": "Blu-ray.com",
            "detailUrl": result.get("sourceUrl"),
            "posterUrl": result.get("movie", {}).get("posterUrl") or result.get("release", {}).get("posterUrl"),
            "poster_url": result.get("movie", {}).get("posterUrl") or result.get("release", {}).get("posterUrl"),
            "poster": result.get("movie", {}).get("posterUrl") or result.get("release", {}).get("posterUrl"),
            "detectedWithoutMembers": False,
            "memberConfidence": "needs_member_confirmation",
            "memberSource": "Blu-ray.com release page",
            "member_source": "Blu-ray.com release page",
            "movies": members,
            "members": members,
            "memberCount": len(members),
            "member_count": len(members),
            "boxSetEvidence": evidence,
            "box_set_evidence": evidence,
            "membersAreExplicit": True,
            "members_are_explicit": True,
        },
    }
