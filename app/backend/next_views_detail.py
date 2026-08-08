"""Movie/container detail view renderers (extracted from next_app).

Move-only extraction: function bodies are unchanged. Shared helpers are
imported back from next_app. next_app re-imports these renderers near the end
of its module body (after every helper is defined), so there is no
circular-import problem at load time.
"""
from __future__ import annotations

try:  # package import (Flask app factory)
    from .next_app import (
        Any,
        app_href,
        container_detail_image,
        detail_fields,
        detail_tags,
        first_usable_image,
        h,
        json_lib,
        json_ready,
        media_asset_public_url,
        movie_detail_image,
        pwa_head_tags,
    )
    from .next_genres import GENRE_KEY_TO_LABEL
except ImportError:  # pragma: no cover - supports gunicorn next_app:app
    from next_app import (
        Any,
        app_href,
        container_detail_image,
        detail_fields,
        detail_tags,
        first_usable_image,
        h,
        json_lib,
        json_ready,
        media_asset_public_url,
        movie_detail_image,
        pwa_head_tags,
    )
    from next_genres import GENRE_KEY_TO_LABEL


def container_detail_movie_cards(movies: list[dict[str, Any]]) -> str:
    if not movies:
        return '<div class="empty">No member movies imported yet.</div>'
    cards = []
    for movie in movies:
        metadata = movie.get("metadata") or {}
        poster = first_usable_image(
            movie.get("poster_url"),
            metadata.get("poster_url"),
            metadata.get("posterUrl"),
            metadata.get("poster"),
            metadata.get("posters"),
        )
        poster_html = f'<img src="{h(poster)}" alt="">' if poster else "<span>No poster</span>"
        movie_href = h(app_href(f"/movies/{movie.get('id')}"))
        cards.append(
            f"""
          <a class="item-card" href="{movie_href}">
            <div class="thumb">{poster_html}</div>
            <div class="item-body">
              <strong>{h(movie.get("title") or "Untitled")}</strong>
              <div class="tags">
                {detail_tags(movie.get("year"), movie.get("format"), movie.get("barcode"))}
              </div>
            </div>
          </a>
            """.strip()
        )
    return "\n".join(cards)


def container_detail_collection_item_cards(items: list[dict[str, Any]]) -> str:
    if not items:
        return '<div class="empty">No collection items imported yet.</div>'
    cards = []
    for item in items:
        metadata = item.get("metadata") or {}
        poster = first_usable_image(
            item.get("poster_url"),
            metadata.get("poster_url"),
            metadata.get("posterUrl"),
            metadata.get("poster"),
            metadata.get("posters"),
        )
        poster_html = f'<img src="{h(poster)}" alt="">' if poster else "<span>No poster</span>"
        entity_type = item.get("entity_type")
        href = app_href(f"/movies/{item.get('entity_id')}")
        if entity_type == "container":
            href = app_href(f"/containers/{item.get('entity_id')}")
        type_label = item.get("container_type") or item.get("item_type") or entity_type
        cards.append(
            f"""
          <a class="item-card" href="{h(href)}">
            <div class="thumb">{poster_html}</div>
            <div class="item-body">
              <strong>{h(item.get("title") or "Untitled")}</strong>
              <div class="tags">
                {detail_tags(str(type_label).replace("_", " "), item.get("year"), item.get("format"), item.get("barcode"))}
              </div>
            </div>
          </a>
            """.strip()
        )
    return "\n".join(cards)


def container_detail_artwork_cards(media_assets: list[dict[str, Any]], kind: str) -> str:
    assets = [asset for asset in media_assets if asset.get("kind") == kind]
    if not assets:
        return '<div class="empty small">No artwork linked yet.</div>'
    cards = []
    for asset in assets:
        display_url = media_asset_public_url(asset)
        preview = f'<img src="{h(display_url)}" alt="">' if display_url else "<span>No preview</span>"
        is_primary = bool(asset.get("is_primary"))
        tags = [
            asset.get("provider_id") or asset.get("storage_backend"),
            asset.get("role"),
            asset.get("variant"),
            "Primary" if is_primary else None,
        ]
        tags_html = "".join(f'<span class="tag">{h(tag)}</span>' for tag in tags if tag)
        storage_label = asset.get("source_url") or asset.get("storage_key") or asset.get("sha256") or "Local media asset"
        cards.append(
            f"""
        <article class="container-art-card">
          <div class="container-art-preview">{preview}</div>
          <div class="container-art-body">
            <div class="tags">{tags_html}</div>
            <strong title="{h(storage_label)}">{h(storage_label)}</strong>
          </div>
        </article>
            """.strip()
        )
    return "\n".join(cards)


def container_detail_media_gallery(media_assets: list[dict[str, Any]]) -> str:
    if not media_assets:
        return '<div class="empty small">No media assets linked yet.</div>'
    return f"""
      <div class="container-artwork-sections">
        <section class="container-artwork-section">
          <h3>Posters</h3>
          <div class="container-art-grid">
            {container_detail_artwork_cards(media_assets, "poster")}
          </div>
        </section>
        <section class="container-artwork-section">
          <h3>Backdrops</h3>
          <div class="container-art-grid backdrops">
            {container_detail_artwork_cards(media_assets, "backdrop")}
          </div>
        </section>
      </div>
    """.strip()


def movie_artwork_upload_form(kind: str) -> str:
    label = "Poster" if kind == "poster" else "Backdrop"
    return f"""
      <form class="upload-form" data-upload-kind="{h(kind)}">
        <label class="file-input">
          <span>{h(label)} file</span>
          <input type="file" name="file" accept="image/*" required>
        </label>
        <label class="check-row">
          <input type="checkbox" name="primary" checked>
          <span>Set as primary</span>
        </label>
        <button class="button" type="submit">Upload {h(label.lower())}</button>
      </form>
    """.strip()


def movie_artwork_asset_cards(media_assets: list[dict[str, Any]], kind: str) -> str:
    assets = [asset for asset in media_assets if asset.get("kind") == kind]
    if not assets:
        return '<div class="empty small">No artwork options linked yet.</div>'
    cards = []
    for asset in assets:
        display_url = media_asset_public_url(asset)
        preview = f'<img src="{h(display_url)}" alt="">' if display_url else "<span>No preview</span>"
        is_primary = bool(asset.get("is_primary"))
        tags = [
            asset.get("provider_id") or asset.get("storage_backend"),
            asset.get("variant"),
            "Primary" if is_primary else None,
        ]
        tags_html = "".join(f'<span class="tag">{h(tag)}</span>' for tag in tags if tag)
        action_html = (
            '<button class="button" type="button" disabled>Primary</button>'
            if is_primary
            else (
                f'<button class="button" type="button" data-media-primary '
                f'data-kind="{h(kind)}" data-media-id="{h(asset.get("id"))}">Set primary</button>'
            )
        )
        storage_label = asset.get("source_url") or asset.get("storage_key") or asset.get("sha256")
        cards.append(
            f"""
        <article class="art-card">
          <div class="art-preview">{preview}</div>
          <div class="art-body">
            <div class="tags">{tags_html}</div>
            <strong>{h(storage_label or "Local media asset")}</strong>
            {action_html}
          </div>
        </article>
            """.strip()
        )
    return "\n".join(cards)


def movie_artwork_manager_html(movie: dict[str, Any], media_assets: list[dict[str, Any]]) -> str:
    movie_id = h(movie.get("id") or "")
    return f"""
      <div class="media-message" id="mediaMessage" role="status" aria-live="polite"></div>
      <div class="artwork-columns" data-movie-id="{movie_id}">
        <section class="artwork-column">
          <div class="section-head">
            <h3>Posters</h3>
            {movie_artwork_upload_form("poster")}
          </div>
          <div class="art-grid">
            {movie_artwork_asset_cards(media_assets, "poster")}
          </div>
        </section>
        <section class="artwork-column">
          <div class="section-head">
            <h3>Backdrops</h3>
            {movie_artwork_upload_form("backdrop")}
          </div>
          <div class="art-grid backdrops">
            {movie_artwork_asset_cards(media_assets, "backdrop")}
          </div>
        </section>
      </div>
    """.strip()


def movie_detail_credit_cards(credits: list[dict[str, Any]]) -> str:
    if not credits:
        return '<div class="empty">No credits imported yet.</div>'
    cards = []
    for credit in credits:
        role = credit.get("character") or credit.get("job") or credit.get("credit_type")
        cards.append(
            f"""
          <div class="item-card plain">
            <div class="item-body">
              <strong>{h(credit.get("name") or "Unknown")}</strong>
              <div class="tags">{detail_tags(role, credit.get("known_for"))}</div>
            </div>
          </div>
            """.strip()
        )
    return "\n".join(cards)


def movie_detail_container_cards(containers: list[dict[str, Any]]) -> str:
    if not containers:
        return '<div class="empty small">No containers linked yet.</div>'
    cards = []
    for container in containers:
        container_href = h(app_href(f"/containers/{container.get('id')}"))
        label = str(container.get("container_type") or "container").replace("_", " ")
        cards.append(
            f"""
          <a class="item-card plain" href="{container_href}">
            <div class="item-body">
              <strong>{h(container.get("title") or "Untitled")}</strong>
              <div class="tags">{detail_tags(label, container.get("relationship"), container.get("year"))}</div>
            </div>
          </a>
            """.strip()
        )
    return "\n".join(cards)


def movie_detail_digital_cards(items: list[dict[str, Any]]) -> str:
    if not items:
        return '<div class="empty small">No digital source matches yet.</div>'
    cards = []
    for item in items:
        href = item.get("playback_url") or ""
        variant_count = int(item.get("variant_count") or item.get("variantCount") or 0)
        tag_html = detail_tags(
            item.get("source_name"),
            item.get("media_type"),
            item.get("year"),
            variant_count > 1 and f"{variant_count} variants",
            item.get("tmdb_id") and f"TMDb {item.get('tmdb_id')}",
            item.get("imdb_id"),
        )
        body = f"""
            <div class="item-body">
              <strong>{h(item.get("title") or "Untitled")}</strong>
              <div class="tags">{tag_html}</div>
            </div>
        """.strip()
        if href:
            cards.append(f'<a class="item-card plain" href="{h(href)}">{body}</a>')
        else:
            cards.append(f'<div class="item-card plain">{body}</div>')
    return "\n".join(cards)


def movie_detail_group_cards(groups: list[dict[str, Any]]) -> str:
    if not groups:
        return '<div class="empty small">No media groups linked yet.</div>'
    cards = []
    for group in groups:
        group_href = f"/api/next/media-groups/{group.get('id')}"
        owner = group.get("created_by_display_name") or group.get("created_by_username")
        tag_html = detail_tags(
            group.get("member_count") and f"{group.get('member_count')} members",
            group.get("movie_count") and f"{group.get('movie_count')} movies",
            owner and f"Owner {owner}",
            group.get("hide_digital") and "digital hidden",
        )
        cards.append(
            f"""
          <a class="item-card plain" href="{h(group_href)}">
            <div class="item-body">
              <strong>{h(group.get("name") or "Untitled group")}</strong>
              <div class="tags">{tag_html}</div>
            </div>
          </a>
            """.strip()
        )
    return "\n".join(cards)


def movie_detail_html(detail: dict[str, Any]) -> str:
    movie = detail.get("movie") or {}
    metadata = movie.get("metadata") or {}
    specs = detail.get("technicalSpecs") or {}
    identifiers = detail.get("identifiers") or []
    containers = detail.get("containers") or []
    media_groups = detail.get("mediaGroups") or []
    credits = detail.get("credits") or []
    media_assets = detail.get("mediaAssets") or []
    digital_items = detail.get("digitalItems") or []
    title = movie.get("title") or "Untitled"
    poster = movie_detail_image(media_assets, metadata, "poster")
    backdrop = movie_detail_image(media_assets, metadata, "backdrop")
    poster_html = f'<img src="{h(poster)}" alt="">' if poster else "<span>No poster</span>"
    hero_style = ""
    if backdrop:
        hero_style = (
            ' style="background-image: linear-gradient(180deg, rgba(16,17,22,.18), '
            f'var(--surface)), url(&quot;{h(backdrop)}&quot;)"'
        )
    identifier_html = (
        "".join(
            f'<div class="field"><span>{h(item.get("provider_id"))} {h(item.get("identifier_type"))}</span>'
            f'<strong>{h(item.get("identifier"))}</strong></div>'
            for item in identifiers
        )
        or '<div class="empty small">No identifiers imported yet.</div>'
    )
    metadata_text = json_lib.dumps(json_ready(metadata), indent=2, sort_keys=True, ensure_ascii=True)

    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no, viewport-fit=cover, interactive-widget=resizes-content">
  <title>""" + h(title) + """ - DiscVault Next</title>
""" + pwa_head_tags() + """
  <style>
    :root {
      color-scheme: dark;
      --bg: #090F1A;
      --surface: #111A2A;
      --surface-2: #1A2438;
      --line: #2B3852;
      --text: #EAF0F7;
      --muted: #9AA7B8;
      --accent: #3C7CE1;
      --blue: #6CB4FC;
      --green: #34D399;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    main {
      width: min(1220px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 22px 0 46px;
    }
    a { color: inherit; }
    .topbar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      margin-bottom: 14px;
    }
    .button {
      border: 1px solid var(--line);
      border-radius: 8px;
      min-height: 38px;
      padding: 0 13px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      text-decoration: none;
      background: var(--surface-2);
      white-space: nowrap;
    }
    button.button {
      color: inherit;
      font: inherit;
      cursor: pointer;
    }
    .button:disabled {
      opacity: .58;
      cursor: default;
    }
    .actions { display: flex; gap: 9px; flex-wrap: wrap; }
    .hero {
      min-height: 270px;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: linear-gradient(135deg, #242938, #11141d);
      background-size: cover;
      background-position: center;
      overflow: hidden;
    }
    .summary {
      display: grid;
      grid-template-columns: 190px minmax(0, 1fr);
      gap: 18px;
      margin-top: -112px;
      padding: 0 18px 18px;
      position: relative;
    }
    .poster {
      aspect-ratio: 2 / 3;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface-2);
      display: flex;
      align-items: center;
      justify-content: center;
      color: var(--muted);
      overflow: hidden;
    }
    .poster img {
      width: 100%;
      height: 100%;
      object-fit: cover;
      display: block;
    }
    .summary-main {
      min-width: 0;
      padding-top: 110px;
    }
    h1, h2, p { margin: 0; }
    h1 {
      font-size: clamp(1.7rem, 4vw, 3rem);
      line-height: 1.05;
      overflow-wrap: anywhere;
    }
    h2 { font-size: 1rem; }
    p {
      color: var(--muted);
      line-height: 1.58;
      margin-top: 12px;
      max-width: 78ch;
    }
    .tags {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 9px;
    }
    .tag {
      border: 1px solid rgba(255,255,255,.14);
      border-radius: 999px;
      color: var(--muted);
      font-size: .74rem;
      padding: 3px 8px;
    }
    .layout {
      display: grid;
      grid-template-columns: minmax(0, 1.1fr) minmax(320px, .9fr);
      gap: 14px;
      margin-top: 14px;
    }
    .panel {
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      min-width: 0;
    }
    .panel.full { grid-column: 1 / -1; }
    .items {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
      gap: 10px;
      margin-top: 12px;
    }
    .item-card {
      display: grid;
      grid-template-columns: 58px minmax(0, 1fr);
      gap: 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface-2);
      padding: 9px;
      color: inherit;
      text-decoration: none;
      min-width: 0;
    }
    .item-card.plain {
      display: block;
    }
    h3 {
      margin: 0;
      font-size: .9rem;
    }
    .item-body { min-width: 0; }
    .item-body strong {
      display: block;
      line-height: 1.28;
      overflow-wrap: anywhere;
    }
    .field-list {
      display: grid;
      gap: 8px;
      margin-top: 10px;
    }
    .field {
      display: grid;
      grid-template-columns: minmax(110px, .7fr) minmax(0, 1.3fr);
      gap: 10px;
      border-bottom: 1px solid rgba(255,255,255,.08);
      padding-bottom: 8px;
    }
    .field:last-child { border-bottom: 0; padding-bottom: 0; }
    .field span {
      color: var(--muted);
      font-size: .82rem;
    }
    .field strong {
      font-weight: 560;
      overflow-wrap: anywhere;
    }
    .artwork-columns {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
      margin-top: 12px;
    }
    .artwork-column {
      min-width: 0;
    }
    .section-head {
      display: grid;
      gap: 10px;
      margin-bottom: 10px;
    }
    .upload-form {
      display: grid;
      grid-template-columns: minmax(180px, 1fr) auto auto;
      gap: 8px;
      align-items: end;
    }
    .file-input,
    .check-row {
      display: grid;
      gap: 5px;
      color: var(--muted);
      font-size: .78rem;
    }
    .file-input input {
      width: 100%;
      min-height: 38px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface-2);
      color: var(--text);
      padding: 7px;
    }
    .check-row {
      grid-template-columns: auto 1fr;
      align-items: center;
      min-height: 38px;
    }
    .art-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
      gap: 10px;
    }
    .art-grid.backdrops {
      grid-template-columns: repeat(auto-fill, minmax(210px, 1fr));
    }
    .art-card {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface-2);
      overflow: hidden;
      min-width: 0;
    }
    .art-preview {
      aspect-ratio: 2 / 3;
      background: #151923;
      display: flex;
      align-items: center;
      justify-content: center;
      color: var(--muted);
      font-size: .78rem;
      overflow: hidden;
    }
    .backdrops .art-preview {
      aspect-ratio: 16 / 9;
    }
    .art-preview img {
      width: 100%;
      height: 100%;
      display: block;
      object-fit: cover;
    }
    .art-body {
      display: grid;
      gap: 8px;
      padding: 10px;
      min-width: 0;
    }
    .art-body strong {
      color: var(--muted);
      font-size: .75rem;
      font-weight: 520;
      overflow-wrap: anywhere;
      line-height: 1.35;
    }
    .media-message {
      min-height: 24px;
      color: var(--muted);
      margin-top: 10px;
      font-size: .86rem;
    }
    .media-message.error { color: #ff9b9b; }
    .media-message.ok { color: var(--green); }
    pre {
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      color: var(--muted);
      font-size: .78rem;
      margin: 12px 0 0;
      max-height: 420px;
      overflow: auto;
    }
    .empty {
      min-height: 120px;
      display: flex;
      align-items: center;
      justify-content: center;
      color: var(--muted);
      border: 1px dashed var(--line);
      border-radius: 8px;
      text-align: center;
    }
    .empty.small { min-height: 48px; }
    @media (max-width: 860px) {
      main { width: min(100vw - 20px, 720px); padding-top: 12px; }
      .topbar { align-items: flex-start; flex-direction: column; }
      .summary { grid-template-columns: 108px minmax(0, 1fr); gap: 12px; margin-top: -70px; padding: 0 12px 14px; }
      .summary-main { padding-top: 74px; }
      .layout { grid-template-columns: 1fr; }
      .panel.full { grid-column: auto; }
      .field { grid-template-columns: 1fr; gap: 3px; }
      .artwork-columns { grid-template-columns: 1fr; }
      .upload-form { grid-template-columns: 1fr; align-items: stretch; }
    }
  </style>
</head>
<body>
  <main>
    <div class="topbar">
      <a class="button" href="/api/next/app">Back to collection</a>
      <div class="actions">
        <a class="button" href="/api/next/movies/""" + h(movie.get("id")) + """">JSON</a>
        <a class="button" href="/api/next/migration">Migration</a>
      </div>
    </div>
    <section class="hero" """ + hero_style + """></section>
    <section class="summary">
      <div class="poster">""" + poster_html + """</div>
      <div class="summary-main">
        <h1>""" + h(title) + """</h1>
        <div class="tags">""" + detail_tags(movie.get("year"), movie.get("format"), movie.get("runtime_minutes") and f"{movie.get('runtime_minutes')} min", movie.get("rating") and f"Rating {movie.get('rating')}", media_groups and f"{len(media_groups)} groups") + """</div>
        <p>""" + h(movie.get("overview") or "No overview imported yet.") + """</p>
      </div>
    </section>

    <section class="layout">
      <div class="panel">
        <h2>Release</h2>
        <div class="field-list">""" + detail_fields([
            ("Original title", movie.get("original_title")),
            ("Barcode", movie.get("barcode")),
            ("Format", movie.get("format")),
            ("Edition", movie.get("edition")),
            ("Edition type", movie.get("edition_type")),
            ("Release date", movie.get("release_date")),
            ("Country", movie.get("country")),
            ("Language", movie.get("language")),
            ("Location", movie.get("location")),
            ("Director", metadata.get("director")),
            ("Producer", metadata.get("producer")),
            ("Studios", metadata.get("studios")),
            ("Genre", ", ".join(GENRE_KEY_TO_LABEL.get(key, key) for key in (movie.get("genres") or []))),
            ("Distributor", metadata.get("distributor")),
            ("Trailer", metadata.get("trailer_url")),
        ]) + """</div>
      </div>
      <div class="panel">
        <h2>Technical Specs</h2>
        <div class="field-list">""" + detail_fields([
            ("HDR", specs.get("hdr") or metadata.get("hdr")),
            ("Packaging", specs.get("packaging") or metadata.get("packaging")),
            ("Case type", specs.get("carrier_type") or metadata.get("carrier_type")),
            ("Steelbook format", specs.get("steelbook_format") or metadata.get("steelbook_format")),
            ("Outer packaging", specs.get("outer_packaging") or metadata.get("outer_packaging")),
            ("Finish", specs.get("finishes") or metadata.get("finishes")),
            ("Screen ratio", specs.get("screen_ratios") or metadata.get("screen_ratios")),
            ("Audio", specs.get("audio_tracks") or metadata.get("audio_tracks")),
            ("Subtitles", specs.get("subtitles") or metadata.get("subtitles")),
            ("Regions", specs.get("regions") or metadata.get("regions")),
        ]) + """</div>
      </div>
      <div class="panel">
        <h2>Containers</h2>
        <div class="items">""" + movie_detail_container_cards(containers) + """</div>
      </div>
      <div class="panel">
        <h2>Groups</h2>
        <div class="items">""" + movie_detail_group_cards(media_groups) + """</div>
      </div>
      <div class="panel">
        <h2>Digital Sources</h2>
        <div class="items">""" + movie_detail_digital_cards(digital_items) + """</div>
      </div>
      <div class="panel">
        <h2>Identifiers</h2>
        <div class="field-list">""" + identifier_html + """</div>
      </div>
      <div class="panel full">
        <h2>Cast & Crew (""" + h(len(credits)) + """)</h2>
        <div class="items">""" + movie_detail_credit_cards(credits) + """</div>
      </div>
      <div class="panel">
        <h2>Media Assets</h2>
        """ + movie_artwork_manager_html(movie, media_assets) + """
      </div>
      <div class="panel">
        <h2>Metadata</h2>
        <pre>""" + h(metadata_text) + """</pre>
      </div>
    </section>
  </main>
  <script>
    (function () {
      const root = document.querySelector("[data-movie-id]");
      const message = document.getElementById("mediaMessage");
      if (!root || !message) return;
      const movieId = root.dataset.movieId;

      function setMessage(text, type) {
        message.textContent = text || "";
        message.className = "media-message" + (type ? " " + type : "");
      }

      async function parseResponse(response) {
        const body = await response.json().catch(() => ({}));
        if (!response.ok || body.status === "error") {
          throw new Error(body.error || "Request failed");
        }
        return body;
      }

      async function setPrimary(button) {
        const mediaId = button.dataset.mediaId;
        const kind = button.dataset.kind;
        if (!mediaId || !kind) return;
        button.disabled = true;
        setMessage("Updating artwork...", "");
        try {
          const response = await fetch(`/api/next/movies/${movieId}/media/primary`, {
            method: "POST",
            credentials: "same-origin",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({mediaId, kind})
          });
          await parseResponse(response);
          setMessage("Artwork updated.", "ok");
          window.location.reload();
        } catch (error) {
          button.disabled = false;
          setMessage(error.message || String(error), "error");
        }
      }

      async function uploadArtwork(form) {
        const kind = form.dataset.uploadKind;
        const file = form.querySelector("input[type=file]");
        const primary = form.querySelector("input[name=primary]");
        if (!kind || !file || !file.files.length) return;
        const submit = form.querySelector("button[type=submit]");
        submit.disabled = true;
        setMessage("Uploading artwork...", "");
        const data = new FormData();
        data.append("file", file.files[0]);
        data.append("kind", kind);
        data.append("primary", primary && primary.checked ? "true" : "false");
        try {
          const response = await fetch(`/api/next/movies/${movieId}/media/upload`, {
            method: "POST",
            credentials: "same-origin",
            body: data
          });
          await parseResponse(response);
          setMessage("Artwork uploaded.", "ok");
          window.location.reload();
        } catch (error) {
          submit.disabled = false;
          setMessage(error.message || String(error), "error");
        }
      }

      root.addEventListener("click", (event) => {
        const button = event.target.closest("[data-media-primary]");
        if (button) setPrimary(button);
      });
      root.querySelectorAll("[data-upload-kind]").forEach((form) => {
        form.addEventListener("submit", (event) => {
          event.preventDefault();
          uploadArtwork(form);
        });
      });
    })();
  </script>
</body>
</html>
"""


def container_detail_html(detail: dict[str, Any]) -> str:
    container = detail.get("container") or {}
    metadata = container.get("metadata") or {}
    media_assets = detail.get("mediaAssets") or []
    identifiers = detail.get("identifiers") or []
    members = detail.get("memberMovies") or []
    collection_items = detail.get("collectionItems") or []
    container_type = str(container.get("container_type") or "container").replace("_", " ")
    title = container.get("title") or "Untitled"
    poster = container_detail_image(media_assets, metadata, "poster")
    backdrop = container_detail_image(media_assets, metadata, "backdrop")
    poster_html = f'<img src="{h(poster)}" alt="">' if poster else "<span>No poster</span>"
    hero_style = ""
    if backdrop:
        hero_style = (
            ' style="background-image: linear-gradient(180deg, rgba(16,17,22,.18), '
            f'var(--surface)), url(&quot;{h(backdrop)}&quot;)"'
        )
    identifier_html = (
        "".join(
            f'<div class="field"><span>{h(item.get("provider_id"))} {h(item.get("identifier_type"))}</span>'
            f'<strong>{h(item.get("identifier"))}</strong></div>'
            for item in identifiers
        )
        or '<div class="empty small">No identifiers imported yet.</div>'
    )
    metadata_text = json_lib.dumps(json_ready(metadata), indent=2, sort_keys=True, ensure_ascii=True)

    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no, viewport-fit=cover, interactive-widget=resizes-content">
  <title>""" + h(title) + """ - DiscVault Next</title>
""" + pwa_head_tags() + """
  <style>
    :root {
      color-scheme: dark;
      --bg: #090F1A;
      --surface: #111A2A;
      --surface-2: #1A2438;
      --line: #2B3852;
      --text: #EAF0F7;
      --muted: #9AA7B8;
      --accent: #3C7CE1;
      --blue: #6CB4FC;
      --green: #34D399;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    main {
      width: min(1220px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 22px 0 46px;
    }
    a { color: inherit; }
    .topbar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      margin-bottom: 14px;
    }
    .button {
      border: 1px solid var(--line);
      border-radius: 8px;
      min-height: 38px;
      padding: 0 13px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      text-decoration: none;
      background: var(--surface-2);
      white-space: nowrap;
    }
    .actions { display: flex; gap: 9px; flex-wrap: wrap; }
    .hero {
      min-height: 260px;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: linear-gradient(135deg, #242938, #11141d);
      background-size: cover;
      background-position: center;
      overflow: hidden;
    }
    .summary {
      display: grid;
      grid-template-columns: 190px minmax(0, 1fr);
      gap: 18px;
      margin-top: -112px;
      padding: 0 18px 18px;
      position: relative;
    }
    .poster {
      aspect-ratio: 2 / 3;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface-2);
      display: flex;
      align-items: center;
      justify-content: center;
      color: var(--muted);
      overflow: hidden;
    }
    .poster img, .thumb img {
      width: 100%;
      height: 100%;
      object-fit: cover;
      display: block;
    }
    .summary-main {
      min-width: 0;
      padding-top: 110px;
    }
    h1, h2, p { margin: 0; }
    h1 {
      font-size: clamp(1.7rem, 4vw, 3rem);
      line-height: 1.05;
      overflow-wrap: anywhere;
    }
    h2 { font-size: 1rem; }
    p {
      color: var(--muted);
      line-height: 1.58;
      margin-top: 12px;
      max-width: 78ch;
    }
    .tags {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 9px;
    }
    .tag {
      border: 1px solid rgba(255,255,255,.14);
      border-radius: 999px;
      color: var(--muted);
      font-size: .74rem;
      padding: 3px 8px;
    }
    .layout {
      display: grid;
      grid-template-columns: minmax(0, 1.1fr) minmax(320px, .9fr);
      gap: 14px;
      margin-top: 14px;
    }
    .panel {
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      min-width: 0;
    }
    .panel.full { grid-column: 1 / -1; }
    .items {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
      gap: 10px;
      margin-top: 12px;
    }
    .item-card {
      display: grid;
      grid-template-columns: 58px minmax(0, 1fr);
      gap: 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface-2);
      padding: 9px;
      color: inherit;
      text-decoration: none;
      min-width: 0;
    }
    .thumb {
      aspect-ratio: 2 / 3;
      border-radius: 6px;
      background: #151923;
      display: flex;
      align-items: center;
      justify-content: center;
      color: var(--muted);
      font-size: .7rem;
      overflow: hidden;
    }
    .item-body { min-width: 0; }
    .item-body strong {
      display: block;
      line-height: 1.28;
      overflow-wrap: anywhere;
    }
    .field-list {
      display: grid;
      gap: 8px;
      margin-top: 10px;
    }
    .field {
      display: grid;
      grid-template-columns: minmax(110px, .7fr) minmax(0, 1.3fr);
      gap: 10px;
      border-bottom: 1px solid rgba(255,255,255,.08);
      padding-bottom: 8px;
    }
    .field:last-child { border-bottom: 0; padding-bottom: 0; }
    .field span {
      color: var(--muted);
      font-size: .82rem;
    }
    .field strong {
      font-weight: 560;
      overflow-wrap: anywhere;
    }
    .container-artwork-sections {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
      margin-top: 12px;
    }
    .container-artwork-section {
      display: grid;
      gap: 10px;
      min-width: 0;
    }
    .container-artwork-section h3 {
      font-size: .95rem;
      margin: 0;
    }
    .container-art-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(min(138px, 100%), 1fr));
      gap: 10px;
      align-items: start;
      min-width: 0;
    }
    .container-art-grid.backdrops {
      grid-template-columns: repeat(auto-fit, minmax(min(260px, 100%), 1fr));
    }
    .container-art-card {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface-2);
      display: grid;
      min-width: 0;
      overflow: hidden;
    }
    .container-art-preview {
      aspect-ratio: 2 / 3;
      background: #151923;
      color: var(--muted);
      display: grid;
      font-size: .78rem;
      overflow: hidden;
      place-items: center;
    }
    .backdrops .container-art-preview {
      aspect-ratio: 16 / 9;
    }
    .container-art-preview img {
      display: block;
      height: 100%;
      object-fit: cover;
      width: 100%;
    }
    .container-art-body {
      display: grid;
      gap: 8px;
      min-width: 0;
      padding: 10px;
    }
    .container-art-body .tags {
      margin-top: 0;
    }
    .container-art-body strong {
      color: var(--muted);
      font-size: .76rem;
      font-weight: 560;
      line-height: 1.35;
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    pre {
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      color: var(--muted);
      font-size: .78rem;
      margin: 12px 0 0;
      max-height: 420px;
      overflow: auto;
    }
    .empty {
      min-height: 120px;
      display: flex;
      align-items: center;
      justify-content: center;
      color: var(--muted);
      border: 1px dashed var(--line);
      border-radius: 8px;
      text-align: center;
    }
    .empty.small { min-height: 48px; }
    @media (max-width: 860px) {
      main { width: min(100vw - 20px, 720px); padding-top: 12px; }
      .topbar { align-items: flex-start; flex-direction: column; }
      .summary { grid-template-columns: 108px minmax(0, 1fr); gap: 12px; margin-top: -70px; padding: 0 12px 14px; }
      .summary-main { padding-top: 74px; }
      .layout { grid-template-columns: 1fr; }
      .panel.full { grid-column: auto; }
      .field { grid-template-columns: 1fr; gap: 3px; }
      .container-artwork-sections { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <main>
    <div class="topbar">
      <a class="button" href="/api/next/app">Back to collection</a>
      <div class="actions">
        <a class="button" href="/api/next/containers/""" + h(container.get("id")) + """">JSON</a>
        <a class="button" href="/api/next/migration">Migration</a>
      </div>
    </div>
    <section class="hero" """ + hero_style + """></section>
    <section class="summary">
      <div class="poster">""" + poster_html + """</div>
      <div class="summary-main">
        <h1>""" + h(title) + """</h1>
        <div class="tags">""" + detail_tags(container_type, container.get("year"), container.get("barcode"), container.get("badge_label")) + """</div>
        <p>""" + h(container.get("description") or "No description imported yet.") + """</p>
      </div>
    </section>

    <section class="layout">
      <div class="panel">
        <h2>Member Movies (""" + h(len(members)) + """)</h2>
        <div class="items">""" + container_detail_movie_cards(members) + """</div>
      </div>
      <div class="panel">
        <h2>Container Details</h2>
        <div class="field-list">""" + detail_fields([
            ("Type", container_type),
            ("Public ID", container.get("public_id")),
            ("Barcode", container.get("barcode")),
            ("Primary movie", container.get("primary_movie_id")),
            ("Created", container.get("created_at")),
            ("Updated", container.get("updated_at")),
        ]) + """</div>
      </div>
      <div class="panel">
        <h2>Collection Items (""" + h(len(collection_items)) + """)</h2>
        <div class="items">""" + container_detail_collection_item_cards(collection_items) + """</div>
      </div>
      <div class="panel">
        <h2>Identifiers</h2>
        <div class="field-list">""" + identifier_html + """</div>
      </div>
      <div class="panel">
        <h2>Media Assets</h2>
        """ + container_detail_media_gallery(media_assets) + """
      </div>
      <div class="panel">
        <h2>Metadata</h2>
        <pre>""" + h(metadata_text) + """</pre>
      </div>
    </section>
  </main>
</body>
</html>
"""
