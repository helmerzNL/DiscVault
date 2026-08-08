"""Collection dashboard + server card renderers (extracted from next_app).

Move-only extraction: function bodies are unchanged. Shared helpers are
imported back from next_app. next_app re-imports these renderers near the end
of its module body (after every helper is defined), so there is no
circular-import problem at load time.
"""
from __future__ import annotations

try:  # package import (Flask app factory)
    from .next_app import (
        APP_PREFERENCE_DEFAULTS,
        Any,
        app_href,
        h,
        html_lib,
        json_lib,
        json_ready,
        pwa_head_tags,
        server_usable_image,
    )
    from .next_genres import GENRE_KEY_TO_LABEL
except ImportError:  # pragma: no cover - supports gunicorn next_app:app
    from next_app import (
        APP_PREFERENCE_DEFAULTS,
        Any,
        app_href,
        h,
        html_lib,
        json_lib,
        json_ready,
        pwa_head_tags,
        server_usable_image,
    )
    from next_genres import GENRE_KEY_TO_LABEL


def server_movie_cards(movies: list[dict[str, Any]]) -> str:
    if not movies:
        return '<div class="empty">No movies imported yet.</div>'
    cards = []
    for movie in movies:
        poster = server_usable_image(movie.get("poster_url"))
        poster_html = f'<img src="{h(poster)}" alt="">' if poster else "<span>No poster</span>"
        tags = []
        if movie.get("year"):
            tags.append(f'<span class="tag good">{h(movie.get("year"))}</span>')
        if movie.get("format"):
            tags.append(f'<span class="tag blue">{h(movie.get("format"))}</span>')
        if movie.get("media_group_count"):
            label = "Group" if int(movie.get("media_group_count") or 0) == 1 else f"{movie.get('media_group_count')} groups"
            tags.append(f'<span class="tag good">{h(label)}</span>')
        if movie.get("digital_count"):
            label = "Digital" if int(movie.get("digital_count") or 0) == 1 else f"{movie.get('digital_count')} digital"
            tags.append(f'<span class="tag good">{h(label)}</span>')
        if movie.get("barcode"):
            tags.append(f'<span class="tag">{h(movie.get("barcode"))}</span>')
        movie_id = h(movie.get("id"))
        cards.append(
            f"""
          <a class="movie" href="{h(app_href(f'/movies/{movie_id}'))}" data-movie-id="{movie_id}">
            <div class="poster">{poster_html}</div>
            <div class="movie-body">
              <div class="movie-title">{h(movie.get("title") or "Untitled")}</div>
              <div class="tags">{''.join(tags)}</div>
            </div>
          </a>
            """.strip()
        )
    return "\n".join(cards)


def server_container_cards(containers: list[dict[str, Any]]) -> str:
    if not containers:
        return '<div class="empty">No containers imported yet.</div>'
    cards = []
    for container in containers:
        label = str(container.get("container_type") or "container").replace("_", " ")
        tags = [f'<span class="tag blue">{h(label)}</span>']
        if container.get("year"):
            tags.append(f'<span class="tag good">{h(container.get("year"))}</span>')
        if container.get("barcode"):
            tags.append(f'<span class="tag">{h(container.get("barcode"))}</span>')
        container_id = h(container.get("id"))
        cards.append(
            f"""
        <a class="container-card" href="{h(app_href(f'/containers/{container_id}'))}">
          <strong>{h(container.get("title") or "Untitled")}</strong>
          <div class="tags">{''.join(tags)}</div>
        </a>
            """.strip()
        )
    return "\n".join(cards)


def collection_dashboard_html(snapshot: dict[str, Any] | None = None) -> str:
    snapshot = snapshot or {"counts": {}, "movies": [], "containers": [], "plugins": [], "build": {}}
    counts = snapshot.get("counts") or {}
    movies = snapshot.get("movies") or []
    containers = snapshot.get("containers") or []
    plugins = snapshot.get("plugins") or []
    preferences = snapshot.get("preferences") or dict(APP_PREFERENCE_DEFAULTS)
    if not preferences.get("collectors_mode"):
        containers = []
    enabled_plugins = [plugin for plugin in plugins if plugin.get("enabled")]
    container_panel_class = "panel" if preferences.get("collectors_mode") else "panel hidden"
    initial_state_json = html_lib.escape(json_lib.dumps(json_ready(snapshot), separators=(",", ":")), quote=False)
    movie_cards = server_movie_cards(movies)
    container_cards = server_container_cards(containers)
    client_status = (
        f"Server rendered {len(movies)} movies. "
        f"Build {snapshot.get('build', {}).get('sha') or 'unknown'}."
    )
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no, viewport-fit=cover, interactive-widget=resizes-content">
  <title>DiscVault Next Collection</title>
""" + pwa_head_tags() + """
  <script>
    (function () {
      try {
        const preference = localStorage.getItem("dv_next_theme") || "system";
        const dark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
        document.documentElement.dataset.themePreference = preference;
        document.documentElement.dataset.theme = preference === "system" ? (dark ? "dark" : "light") : preference;
      } catch (error) {
        document.documentElement.dataset.theme = "dark";
        document.documentElement.dataset.themePreference = "system";
      }
    })();
  </script>
  <style>
    :root {
      color-scheme: dark;
      --font-sans: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      --font-mono: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace;
      --radius-xs: 5px;
      --radius-sm: 6px;
      --radius-md: 8px;
      --space-1: 4px;
      --space-2: 8px;
      --space-3: 12px;
      --space-4: 16px;
      --space-5: 20px;
      --space-6: 24px;
      --bg: #090F1A;
      --bg-elevated: #111A2A;
      --surface: rgba(17,26,42,.88);
      --surface-2: rgba(26,36,56,.9);
      --surface-3: rgba(36,48,71,.82);
      --chrome: rgba(9,15,26,.72);
      --line: rgba(234,240,247,.16);
      --soft-line: rgba(234,240,247,.09);
      --soft-bg: rgba(255,255,255,.045);
      --empty-bg: rgba(255,255,255,.028);
      --text: #EAF0F7;
      --text-strong: #ffffff;
      --muted: #9AA7B8;
      --muted-2: #647082;
      --accent: #3C7CE1;
      --accent-soft: rgba(60,124,225,.16);
      --blue: #3B9EFF;
      --blue-soft: rgba(59,158,255,.16);
      --green: #34D399;
      --green-soft: rgba(52,211,153,.14);
      --red: #FF5A52;
      --red-soft: rgba(255,90,82,.14);
      --button-bg: rgba(255,255,255,.065);
      --button-hover: rgba(255,255,255,.105);
      --poster-gradient: linear-gradient(145deg, #1A2438, #0E1524);
      --hero-fade: rgba(9,15,26,.16);
      --overlay: rgba(0,0,0,.68);
      --shadow: 0 24px 80px rgba(0,0,0,.42);
      --shadow-soft: 0 12px 34px rgba(0,0,0,.22);
      --control-shadow: inset 0 1px 0 rgba(255,255,255,.055);
      --focus-ring: 0 0 0 3px rgba(42,111,214,.35);
      --material-blur: saturate(160%) blur(20px);
    }
    :root[data-theme="light"] {
      color-scheme: light;
      --bg: #F4F7FB;
      --bg-elevated: #ffffff;
      --surface: rgba(255,255,255,.82);
      --surface-2: rgba(237,241,247,.92);
      --surface-3: rgba(225,232,241,.88);
      --chrome: rgba(255,255,255,.76);
      --line: rgba(60,72,99,.16);
      --soft-line: rgba(60,72,99,.1);
      --soft-bg: rgba(60,72,99,.045);
      --empty-bg: rgba(60,72,99,.035);
      --text: #0E141F;
      --text-strong: #000000;
      --muted: #4A5666;
      --muted-2: #7C8798;
      --accent: #1F5FBF;
      --accent-soft: rgba(31,95,191,.12);
      --blue: #1E6FD8;
      --blue-soft: rgba(30,111,216,.11);
      --green: #0E9F6E;
      --green-soft: rgba(14,159,110,.1);
      --red: #DC2626;
      --red-soft: rgba(220,38,38,.1);
      --button-bg: rgba(255,255,255,.68);
      --button-hover: rgba(255,255,255,.96);
      --poster-gradient: linear-gradient(145deg, #e2e5eb, #f8f9fb);
      --hero-fade: rgba(245,245,247,.16);
      --overlay: rgba(20,22,28,.46);
      --shadow: 0 22px 70px rgba(0,0,0,.16);
      --shadow-soft: 0 10px 30px rgba(0,0,0,.1);
      --control-shadow: inset 0 1px 0 rgba(255,255,255,.72);
      --focus-ring: 0 0 0 3px rgba(0,102,204,.22);
    }
    * { box-sizing: border-box; }
    *::before, *::after { box-sizing: border-box; }
    html {
      background: var(--bg);
      text-rendering: optimizeLegibility;
      -webkit-font-smoothing: antialiased;
      -moz-osx-font-smoothing: grayscale;
    }
    body {
      margin: 0;
      min-height: 100vh;
      background:
        linear-gradient(180deg, var(--bg-elevated), var(--bg) 310px),
        var(--bg);
      color: var(--text);
      font-family: var(--font-sans);
    }
    .hidden { display: none !important; }
    ::selection {
      background: var(--blue-soft);
      color: var(--text-strong);
    }
    main {
      width: min(1400px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 28px 0 56px;
    }
    :focus-visible {
      outline: none;
      box-shadow: var(--focus-ring);
    }
    @media (prefers-reduced-motion: reduce) {
      *, *::before, *::after {
        animation-duration: .01ms !important;
        animation-iteration-count: 1 !important;
        scroll-behavior: auto !important;
        transition-duration: .01ms !important;
      }
    }
    .app-header {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: var(--space-5);
      margin-bottom: var(--space-5);
      position: sticky;
      top: 0;
      z-index: 8;
      padding: 14px 0;
      background: linear-gradient(180deg, var(--bg-elevated) 0%, color-mix(in srgb, var(--bg-elevated) 74%, transparent) 62%, transparent 100%);
      backdrop-filter: var(--material-blur);
    }
    .app-brand { min-width: 0; }
    .eyebrow {
      display: block;
      color: var(--muted);
      font-size: .78rem;
      font-weight: 700;
      letter-spacing: 0;
      margin-bottom: 3px;
    }
    .header-controls {
      display: grid;
      gap: 9px;
      justify-items: end;
      min-width: 0;
    }
    h1, h2, h3, p { margin: 0; }
    h1 {
      color: var(--text-strong);
      font-size: clamp(1.7rem, 2.2vw, 2.35rem);
      font-weight: 760;
      letter-spacing: 0;
      line-height: 1.08;
    }
    h2 { color: var(--text-strong); font-size: 1rem; font-weight: 720; }
    h3 { color: var(--text-strong); font-size: .92rem; font-weight: 700; }
    p { color: var(--muted); line-height: 1.5; }
    a, button {
      color: inherit;
      font: inherit;
    }
    button, a.button {
      border: 1px solid var(--line);
      border-radius: var(--radius-md);
      background: var(--button-bg);
      color: var(--text);
      cursor: pointer;
      min-height: 38px;
      padding: 0 13px;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      white-space: nowrap;
      box-shadow: var(--control-shadow);
      transition: background .16s ease, border-color .16s ease, color .16s ease, transform .16s ease;
    }
    button:hover, a.button:hover {
      background: var(--button-hover);
    }
    button:active, a.button:active {
      transform: translateY(1px);
    }
    button.active {
      background: var(--accent-soft);
      border-color: var(--accent);
      color: var(--accent);
    }
    .theme-toggle {
      display: inline-grid;
      grid-template-columns: repeat(3, minmax(0, auto));
      gap: 3px;
      padding: 3px;
      border: 1px solid var(--line);
      border-radius: var(--radius-md);
      background: var(--chrome);
      box-shadow: var(--control-shadow);
      backdrop-filter: var(--material-blur);
    }
    .theme-toggle button {
      min-height: 30px;
      border: 0;
      border-radius: var(--radius-sm);
      padding: 0 10px;
      background: transparent;
      color: var(--muted);
      box-shadow: none;
      font-size: .8rem;
    }
    .theme-toggle button.active {
      background: var(--surface);
      color: var(--text);
      box-shadow: var(--control-shadow);
    }
    .actions, .filters {
      display: flex;
      gap: 9px;
      flex-wrap: wrap;
      align-items: center;
    }
    .toolbar {
      display: grid;
      grid-template-columns: minmax(240px, 1fr) auto;
      gap: 12px;
      margin-bottom: 16px;
      align-items: center;
    }
    input, select {
      width: 100%;
      min-height: 42px;
      border: 1px solid var(--line);
      border-radius: var(--radius-md);
      background: var(--surface);
      color: var(--text);
      padding: 0 13px;
      font: inherit;
      box-shadow: var(--control-shadow);
      transition: background .16s ease, border-color .16s ease, box-shadow .16s ease;
    }
    input:focus, select:focus {
      border-color: var(--blue);
      outline: none;
      box-shadow: var(--focus-ring);
    }
    .language-control {
      display: inline-flex;
      align-items: center;
      gap: 7px;
      color: var(--muted);
      font-size: .8rem;
      white-space: nowrap;
    }
    .language-control select {
      width: auto;
      min-width: 142px;
      min-height: 34px;
      padding: 0 9px;
    }
    .stats {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 16px;
    }
    .stat, .panel {
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: var(--radius-md);
      padding: 15px;
      min-width: 0;
      box-shadow: var(--control-shadow), var(--shadow-soft);
      backdrop-filter: var(--material-blur);
    }
    .stat strong {
      display: block;
      font-size: 1.8rem;
      line-height: 1.1;
    }
    .stat span, .muted {
      color: var(--muted);
      font-size: .86rem;
    }
    .layout {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 310px;
      gap: 14px;
      align-items: start;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(155px, 1fr));
      gap: 12px;
    }
    .movie {
      border: 1px solid var(--line);
      border-radius: var(--radius-md);
      overflow: hidden;
      background: var(--surface);
      color: inherit;
      display: block;
      min-width: 0;
      text-decoration: none;
      box-shadow: var(--control-shadow);
      transition: border-color .18s ease, box-shadow .18s ease, transform .18s ease;
    }
    .movie:hover {
      border-color: color-mix(in srgb, var(--blue) 46%, var(--line));
      box-shadow: var(--control-shadow), var(--shadow-soft);
      transform: translateY(-1px);
    }
    .poster {
      aspect-ratio: 2 / 3;
      background: var(--poster-gradient);
      display: flex;
      align-items: center;
      justify-content: center;
      color: var(--muted);
      font-size: .82rem;
      overflow: hidden;
    }
    .poster img {
      width: 100%;
      height: 100%;
      object-fit: cover;
      display: block;
    }
    .movie-body {
      padding: 10px;
    }
    .movie-title {
      font-weight: 700;
      font-size: .94rem;
      line-height: 1.25;
      min-height: 2.35em;
      overflow-wrap: anywhere;
    }
    .tags {
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
      margin-top: 8px;
    }
    .tag {
      border: 1px solid var(--soft-line);
      border-radius: 999px;
      padding: 3px 7px;
      color: var(--muted);
      font-size: .72rem;
      line-height: 1.2;
    }
    .tag.good { color: var(--green); border-color: var(--green-soft); }
    .tag.blue { color: var(--blue); border-color: var(--blue-soft); }
    .tag.bad { color: var(--red); border-color: var(--red-soft); }
    .section-head {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: baseline;
      margin-bottom: 12px;
    }
    .containers {
      display: grid;
      gap: 9px;
      margin-top: 12px;
    }
    .container-card {
      border: 1px solid var(--line);
      border-radius: var(--radius-md);
      padding: 11px;
      background: var(--surface-2);
      color: inherit;
      display: block;
      text-decoration: none;
      box-shadow: var(--control-shadow);
      transition: border-color .18s ease, background .18s ease, transform .18s ease;
    }
    .container-card:hover {
      border-color: color-mix(in srgb, var(--blue) 42%, var(--line));
      background: var(--surface-3);
      transform: translateY(-1px);
    }
    .container-card strong {
      display: block;
      line-height: 1.3;
      overflow-wrap: anywhere;
    }
    .empty {
      min-height: 220px;
      display: flex;
      align-items: center;
      justify-content: center;
      text-align: center;
      color: var(--muted);
      border: 1px dashed var(--line);
      border-radius: var(--radius-md);
      background: var(--empty-bg);
    }
    .detail-overlay {
      position: fixed;
      inset: 0;
      z-index: 20;
      display: none;
      background: var(--overlay);
      padding: 18px;
      overflow-y: auto;
    }
    .detail-overlay.open {
      display: block;
    }
    .detail-panel {
      width: min(1120px, 100%);
      margin: 0 auto;
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: var(--radius-md);
      overflow: hidden;
      box-shadow: var(--shadow);
      backdrop-filter: var(--material-blur);
    }
    .detail-hero {
      min-height: 210px;
      background: var(--poster-gradient);
      background-size: cover;
      background-position: center;
      position: relative;
    }
    .detail-hero::after {
      content: "";
      position: absolute;
      inset: 0;
      background: linear-gradient(180deg, var(--hero-fade), var(--surface));
    }
    .detail-close {
      position: absolute;
      top: 14px;
      right: 14px;
      z-index: 2;
      min-width: 40px;
      padding: 0;
      font-size: 1.2rem;
    }
    .detail-content {
      display: grid;
      grid-template-columns: 190px minmax(0, 1fr);
      gap: 18px;
      padding: 0 18px 18px;
      margin-top: -96px;
      position: relative;
      z-index: 2;
    }
    .detail-poster {
      aspect-ratio: 2 / 3;
      border: 1px solid var(--line);
      border-radius: var(--radius-md);
      overflow: hidden;
      background: var(--surface-2);
      display: flex;
      align-items: center;
      justify-content: center;
      color: var(--muted);
    }
    .detail-poster img {
      width: 100%;
      height: 100%;
      object-fit: cover;
      display: block;
    }
    .detail-main {
      min-width: 0;
      padding-top: 100px;
    }
    .detail-title {
      font-size: clamp(1.6rem, 3vw, 2.6rem);
      line-height: 1.05;
      margin-bottom: 8px;
      overflow-wrap: anywhere;
    }
    .detail-overview {
      color: var(--muted);
      line-height: 1.6;
      max-width: 76ch;
      margin-top: 12px;
    }
    .detail-sections {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
      padding: 0 18px 18px;
    }
    .detail-section {
      border: 1px solid var(--line);
      border-radius: var(--radius-md);
      background: var(--soft-bg);
      padding: 14px;
      min-width: 0;
    }
    .detail-section.full {
      grid-column: 1 / -1;
    }
    .collection-video-groups {
      display: grid;
      gap: 14px;
      margin-top: 10px;
      min-width: 0;
    }
    .collection-video-type-group {
      display: grid;
      gap: 9px;
      min-width: 0;
    }
    .collection-video-group-head {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 10px;
      min-width: 0;
    }
    .collection-video-group-head strong {
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      font-size: .92rem;
    }
    .collection-video-group-head span {
      color: var(--muted);
      font-size: .8rem;
      font-weight: 700;
      white-space: nowrap;
    }
    .collection-video-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(min(260px, 100%), 1fr));
      gap: 12px;
      min-width: 0;
    }
    .collection-video-card {
      border: 1px solid var(--soft-line);
      border-radius: var(--radius-md);
      background: var(--surface-2);
      color: inherit;
      display: grid;
      min-width: 0;
      overflow: hidden;
      text-decoration: none;
      box-shadow: var(--control-shadow);
    }
    .collection-video-card > strong,
    .collection-video-card > span {
      margin: 0 10px;
    }
    .collection-video-card > strong {
      margin-top: 10px;
      overflow-wrap: anywhere;
    }
    .collection-video-card > span {
      color: var(--muted);
      font-size: .8rem;
      margin-bottom: 10px;
      overflow-wrap: anywhere;
    }
    .collection-video-copy {
      display: grid;
      gap: 4px;
      padding: 9px 10px 10px;
      min-width: 0;
    }
    .collection-video-copy strong {
      overflow-wrap: anywhere;
    }
    .collection-video-copy span {
      color: var(--muted);
      font-size: .8rem;
      overflow-wrap: anywhere;
    }
    .collection-video-link {
      color: var(--blue);
      font-size: .8rem;
      font-weight: 700;
      margin-top: 2px;
      text-decoration: none;
      width: max-content;
    }
    .collection-video-link:hover {
      text-decoration: underline;
    }
    .collection-video-embed {
      aspect-ratio: 16 / 9;
      background: #050507;
      overflow: hidden;
      width: 100%;
    }
    .collection-video-embed iframe {
      border: 0;
      display: block;
      height: 100%;
      width: 100%;
    }
    .field-list {
      display: grid;
      gap: 8px;
      margin-top: 10px;
    }
    .field {
      display: grid;
      grid-template-columns: minmax(110px, .6fr) minmax(0, 1.4fr);
      gap: 10px;
      border-bottom: 1px solid var(--soft-line);
      padding-bottom: 8px;
    }
    .field:last-child {
      border-bottom: 0;
      padding-bottom: 0;
    }
    .field span {
      color: var(--muted);
      font-size: .82rem;
    }
    .field strong {
      font-weight: 560;
      overflow-wrap: anywhere;
    }
    .credit-list {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(210px, 1fr));
      gap: 8px;
      margin-top: 10px;
    }
    .credit {
      border: 1px solid var(--soft-line);
      border-radius: var(--radius-md);
      padding: 9px;
      background: var(--surface-2);
      min-width: 0;
    }
    .credit strong {
      display: block;
      overflow-wrap: anywhere;
    }
    .credit span {
      color: var(--muted);
      font-size: .8rem;
      display: block;
      margin-top: 3px;
      overflow-wrap: anywhere;
    }
    button:disabled {
      cursor: not-allowed;
      opacity: .62;
    }
    .auth-panel {
      display: grid;
      grid-template-columns: minmax(0, 1.2fr) minmax(280px, .8fr);
      gap: 14px;
      align-items: start;
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: var(--radius-md);
      padding: 15px;
      margin-bottom: 16px;
      box-shadow: var(--control-shadow), var(--shadow-soft);
      backdrop-filter: var(--material-blur);
    }
    .auth-copy {
      min-width: 0;
    }
    .auth-copy strong {
      display: block;
      font-size: 1rem;
      margin-bottom: 5px;
    }
    .auth-copy p {
      max-width: 72ch;
    }
    .auth-form {
      display: grid;
      gap: 9px;
      min-width: 0;
    }
    .auth-form label {
      color: var(--muted);
      font-size: .78rem;
      display: grid;
      gap: 5px;
    }
    .auth-form .actions {
      justify-content: flex-end;
    }
    .auth-status {
      color: var(--muted);
      font-size: .86rem;
      min-height: 1.35em;
      overflow-wrap: anywhere;
    }
    .auth-status.good { color: var(--green); }
    .auth-status.bad { color: var(--red); }
    .auth-status.info { color: var(--blue); }
    .startup-panel {
      display: grid;
      grid-template-columns: 1fr;
      gap: 13px;
      align-items: start;
      background: var(--blue-soft);
      border: 1px solid var(--line);
      border-radius: var(--radius-md);
      padding: 15px;
      margin-bottom: 16px;
      box-shadow: var(--control-shadow);
    }
    .startup-head {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 14px;
      align-items: start;
    }
    .startup-copy {
      min-width: 0;
    }
    .startup-copy strong {
      display: block;
      font-size: 1rem;
      margin-bottom: 5px;
    }
    .startup-copy p {
      max-width: 78ch;
    }
    .startup-meta {
      display: flex;
      flex-wrap: wrap;
      gap: 7px;
      margin-top: 9px;
    }
    .startup-meta span {
      border: 1px solid var(--soft-line);
      border-radius: 999px;
      color: var(--muted);
      background: rgba(255,255,255,.03);
      padding: 4px 9px;
      font-size: .78rem;
      overflow-wrap: anywhere;
    }
    .startup-steps {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 0;
      border-top: 1px solid var(--soft-line);
      border-bottom: 1px solid var(--soft-line);
    }
    .startup-step {
      display: grid;
      grid-template-columns: 28px minmax(0, 1fr);
      gap: 9px;
      padding: 11px 10px;
      border-right: 1px solid var(--soft-line);
      min-width: 0;
    }
    .startup-step:last-child {
      border-right: 0;
    }
    .startup-step-marker {
      width: 24px;
      height: 24px;
      border-radius: 999px;
      display: grid;
      place-items: center;
      border: 1px solid var(--line);
      color: var(--muted);
      font-size: .74rem;
      font-weight: 700;
    }
    .startup-step strong {
      display: block;
      font-size: .84rem;
      overflow-wrap: anywhere;
    }
    .startup-step span {
      display: block;
      color: var(--muted);
      font-size: .74rem;
      line-height: 1.3;
      margin-top: 2px;
      overflow-wrap: anywhere;
    }
    .startup-step.complete .startup-step-marker {
      border-color: var(--green);
      color: var(--green);
    }
    .startup-step.complete {
      background: rgba(58, 201, 146, .06);
    }
    .startup-step.active .startup-step-marker {
      border-color: var(--blue);
      background: var(--blue-soft);
      color: var(--blue);
    }
    .startup-step.active {
      background: rgba(96, 165, 250, .07);
    }
    .startup-step.blocked .startup-step-marker {
      border-color: var(--red);
      color: var(--red);
    }
    .startup-step.blocked {
      background: rgba(251, 113, 133, .07);
    }
    .startup-facts {
      display: flex;
      flex-wrap: wrap;
      gap: 8px 14px;
      color: var(--muted);
      font-size: .8rem;
    }
    .startup-facts strong {
      color: var(--text);
      font-size: .9rem;
      margin-right: 4px;
    }
    .startup-actions {
      display: flex;
      justify-content: flex-end;
      gap: 8px;
      flex-wrap: wrap;
      min-width: 0;
    }
    .admin-panel {
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: var(--radius-md);
      padding: 15px;
      margin-bottom: 16px;
      box-shadow: var(--control-shadow), var(--shadow-soft);
      backdrop-filter: var(--material-blur);
    }
    .admin-panel h2 {
      margin: 0;
      font-size: 1rem;
    }
    .admin-grid {
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
      gap: 12px;
      margin-top: 12px;
    }
    .admin-summary {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
      margin: 12px 0;
    }
    .admin-metric {
      border: 1px solid var(--soft-line);
      border-radius: var(--radius-md);
      background: var(--soft-bg);
      padding: 10px;
      min-width: 0;
    }
    .admin-metric strong {
      display: block;
      font-size: 1.2rem;
      line-height: 1.15;
      overflow-wrap: anywhere;
    }
    .admin-metric span {
      display: block;
      color: var(--muted);
      font-size: .75rem;
      margin-top: 4px;
    }
    .admin-tabs {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      margin: 4px 0 12px;
    }
    .admin-tabs button {
      min-height: 34px;
      padding: 0 11px;
    }
    .admin-view {
      display: none;
    }
    .admin-view.active {
      display: block;
    }
    .admin-card {
      border: 1px solid var(--soft-line);
      border-radius: var(--radius-md);
      background: var(--surface-2);
      padding: 12px;
      min-width: 0;
    }
    .admin-card.wide {
      grid-column: 1 / -1;
    }
    .admin-card h3 {
      margin: 0 0 9px;
      font-size: .9rem;
    }
    .admin-card p + .admin-controls,
    .admin-card .admin-list + .admin-list {
      margin-top: 10px;
    }
    .admin-list {
      display: grid;
      gap: 8px;
      margin-top: 10px;
    }
    .admin-stack {
      display: grid;
      gap: 8px;
      min-width: 0;
    }
    .admin-row {
      border: 1px solid var(--soft-line);
      border-radius: var(--radius-md);
      padding: 9px;
      display: grid;
      gap: 8px;
      background: var(--soft-bg);
      min-width: 0;
    }
    .admin-row-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      min-width: 0;
    }
    .admin-row-head strong {
      overflow-wrap: anywhere;
    }
    .admin-controls {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
    }
    .admin-controls select,
    .admin-controls input {
      min-width: 150px;
    }
    .admin-controls select {
      min-height: 38px;
      border: 1px solid var(--line);
      border-radius: var(--radius-md);
      background: var(--surface);
      color: var(--text);
      padding: 0 11px;
      font: inherit;
    }
    .admin-code {
      font-family: var(--font-mono);
      background: var(--soft-bg);
      border: 1px solid var(--soft-line);
      border-radius: var(--radius-sm);
      padding: 7px 8px;
      overflow-wrap: anywhere;
    }
    .admin-mode {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      align-items: center;
      margin: 8px 0 12px;
    }
    .admin-permission-cloud {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 8px;
    }
    .admin-plugin-head {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 10px;
      min-width: 0;
    }
    .admin-plugin-head strong,
    .admin-plugin-head span {
      overflow-wrap: anywhere;
    }
    .admin-plugin-meta {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 8px;
    }
    .admin-plugin-section {
      display: grid;
      gap: 8px;
      margin-top: 10px;
    }
    .admin-plugin-section + .admin-plugin-section {
      margin-top: 14px;
      padding-top: 12px;
      border-top: 1px solid var(--soft-line);
    }
    .admin-plugin-section-head {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 10px;
      min-width: 0;
    }
    .admin-plugin-section-head h4 {
      margin: 0;
      font-size: .88rem;
    }
    .admin-plugin-config-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      margin-top: 10px;
      min-width: 0;
    }
    .admin-plugin-config-head strong {
      font-size: .82rem;
    }
    .admin-plugin-config {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
      margin-top: 8px;
    }
    .admin-plugin-config label {
      color: var(--muted);
      display: grid;
      font-size: .78rem;
      gap: 5px;
      min-width: 0;
    }
    .admin-plugin-config label span {
      display: flex;
      align-items: center;
      gap: 6px;
      min-width: 0;
      overflow-wrap: anywhere;
    }
    .admin-plugin-config small {
      color: var(--muted);
      font-size: .72rem;
      line-height: 1.3;
      overflow-wrap: anywhere;
    }
    .admin-plugin-config input {
      min-height: 38px;
    }
    .surface-card,
    .glass-card,
    .empty-state {
      border: 1px solid var(--line);
      border-radius: var(--radius-md);
      min-width: 0;
    }
    .surface-card {
      background: var(--surface);
      box-shadow: var(--control-shadow), var(--shadow-soft);
    }
    .glass-card {
      background: var(--chrome);
      box-shadow: var(--control-shadow), var(--shadow-soft);
      backdrop-filter: var(--material-blur);
    }
    .control-row,
    .split-row {
      display: flex;
      align-items: center;
      gap: var(--space-2);
      min-width: 0;
    }
    .split-row {
      justify-content: space-between;
    }
    .status-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--muted-2);
      flex: 0 0 auto;
    }
    .status-dot.good { background: var(--green); }
    .status-dot.info { background: var(--blue); }
    .status-dot.bad { background: var(--red); }
    .visually-hidden {
      position: absolute !important;
      width: 1px;
      height: 1px;
      padding: 0;
      margin: -1px;
      overflow: hidden;
      clip: rect(0, 0, 0, 0);
      white-space: nowrap;
      border: 0;
    }
    .hidden {
      display: none !important;
    }
    @media (max-width: 980px) {
      main { width: min(100vw - 20px, 760px); padding-top: 18px; }
      .app-header, .toolbar { grid-template-columns: 1fr; flex-direction: column; align-items: stretch; }
      .header-controls { justify-items: stretch; }
      .theme-toggle { width: max-content; }
      .auth-panel { grid-template-columns: 1fr; }
      .startup-head { grid-template-columns: 1fr; }
      .startup-steps { grid-template-columns: 1fr; }
      .startup-step { border-right: 0; border-bottom: 1px solid var(--soft-line); }
      .startup-step:last-child { border-bottom: 0; }
      .startup-actions { justify-content: flex-start; }
      .admin-grid { grid-template-columns: 1fr; }
      .admin-summary { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .admin-plugin-config { grid-template-columns: 1fr; }
      .stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .layout { grid-template-columns: 1fr; }
      .grid { grid-template-columns: repeat(auto-fill, minmax(132px, 1fr)); }
      .actions, .filters { justify-content: flex-start; }
      .detail-content {
        grid-template-columns: 120px minmax(0, 1fr);
        margin-top: -62px;
      }
      .detail-main {
        padding-top: 66px;
      }
      .detail-sections {
        grid-template-columns: 1fr;
      }
      .field {
        grid-template-columns: 1fr;
        gap: 3px;
      }
    }
    @media (max-width: 520px) {
      .stats { grid-template-columns: 1fr; }
      .admin-summary { grid-template-columns: 1fr; }
      .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .detail-overlay {
        padding: 0;
      }
      .detail-panel {
        min-height: 100vh;
        border-radius: 0;
      }
      .detail-content {
        grid-template-columns: 92px minmax(0, 1fr);
        gap: 12px;
        padding: 0 12px 14px;
      }
      .detail-sections {
        padding: 0 12px 14px;
      }
    }
  </style>
</head>
<body>
  <main>
    <header class="app-header">
      <div class="app-brand">
        <span class="eyebrow" data-next-i18n="shell.product">DiscVault Next</span>
        <h1 data-next-i18n="collection.title">Collection</h1>
        <p data-next-i18n="collection.description">PostgreSQL collection view for validating the migrated library.</p>
        <p class="muted" id="clientStatus">""" + h(client_status) + """</p>
      </div>
      <div class="header-controls">
        <div class="theme-toggle" role="group" aria-label="Appearance" data-next-i18n-aria="appearance.label">
          <button type="button" data-theme-choice="system" data-next-i18n="appearance.system">System</button>
          <button type="button" data-theme-choice="light" data-next-i18n="appearance.light">Light</button>
          <button type="button" data-theme-choice="dark" data-next-i18n="appearance.dark">Dark</button>
        </div>
        <label class="language-control">
          <span data-next-i18n="language.label">Language</span>
          <select id="nextLanguageSelect" aria-label="Language" data-next-i18n-aria="language.label"></select>
        </label>
        <div class="actions">
          <button type="button" onclick="loadCollection()" data-next-i18n="common.refresh">Refresh</button>
          <a class="button" href="/api/next/migration" data-next-i18n="nav.migration">Migration</a>
          <a class="button" href="/api/next/movies?limit=1000" data-next-i18n="nav.moviesJson">Movies JSON</a>
        </div>
      </div>
    </header>

    <section class="stats">
      <div class="stat"><strong id="movieCount">""" + h(counts.get("movies", 0)) + """</strong><span data-next-i18n="collection.movies">Movies</span></div>
      <div class="stat"><strong id="peopleCount">""" + h(counts.get("people", 0)) + """</strong><span data-next-i18n="collection.people">People</span></div>
      <div class="stat"><strong id="assetCount">""" + h(counts.get("mediaAssets", 0)) + """</strong><span data-next-i18n="collection.mediaAssets">Media assets</span></div>
      <div class="stat"><strong id="pluginCount">""" + h(len(enabled_plugins)) + """</strong><span data-next-i18n="collection.enabledPlugins">Enabled plugins</span></div>
    </section>

    <section class="auth-panel" id="authPanel">
      <div class="auth-copy">
        <strong id="authTitle" data-next-i18n="auth.passkeys">Passkeys</strong>
        <p id="authDescription" data-next-i18n="auth.checking">Checking authentication status...</p>
        <p class="muted" id="authMeta"></p>
      </div>
      <div class="auth-form">
        <div id="authSetupFields">
          <label><span data-next-i18n="auth.username">Username</span>
            <input id="authUsername" type="text" value="admin" autocomplete="username">
          </label>
          <label><span data-next-i18n="auth.passkeyName">Passkey name</span>
            <input id="authCredentialName" type="text" value="Owner passkey" autocomplete="off">
          </label>
          <label id="authInviteLabel"><span data-next-i18n="auth.inviteCode">Invite code</span>
            <input id="authInviteCode" type="text" autocomplete="one-time-code" placeholder="XXXX-XXXX-XXXX">
          </label>
        </div>
        <div id="authReviewFields" class="hidden">
          <label><span>Username</span>
            <input id="authReviewUsername" type="text" autocomplete="username">
          </label>
          <label><span data-next-i18n="auth.password">Password</span>
            <input id="authReviewPassword" type="password" autocomplete="current-password">
          </label>
        </div>
        <div class="actions">
          <button type="button" id="authSetupButton" data-auth-action="setup" data-next-i18n="auth.createOwnerPasskey">Create owner passkey</button>
          <button type="button" id="authJoinButton" data-auth-action="join" data-next-i18n="auth.createAccount">Create account</button>
          <button type="button" id="authReviewButton" class="hidden" data-next-i18n="auth.signIn">Sign in</button>
          <button type="button" id="authLoginButton" data-auth-action="login" data-next-i18n="auth.signIn">Sign in</button>
          <button type="button" id="authLogoutButton" data-auth-action="logout" data-next-i18n="auth.signOut">Sign out</button>
        </div>
        <div class="auth-status" id="authStatusLine"></div>
      </div>
    </section>

    <section class="startup-panel hidden" id="startupPanel">
      <div class="startup-head">
        <div class="startup-copy">
          <strong id="startupTitle" data-next-i18n="startup.setup">Setup</strong>
          <p id="startupDescription" data-next-i18n="startup.checking">Checking startup state...</p>
          <p class="muted startup-meta" id="startupMeta"></p>
          <div class="auth-status" id="startupStatusLine"></div>
        </div>
        <div class="startup-actions">
          <button type="button" id="startupAuthButton" data-startup-action="auth" data-next-i18n="auth.passkey">Passkey</button>
          <button type="button" id="startupStartMigrationButton" data-startup-action="start-migration" data-next-i18n="startup.startMigration">Start Migration</button>
          <button type="button" id="startupMigrationButton" data-startup-action="migration" data-next-i18n="startup.migrationDetails">Migration Details</button>
          <button type="button" id="startupRefreshButton" data-startup-action="refresh" data-next-i18n="common.refresh">Refresh</button>
        </div>
      </div>
      <div class="startup-steps" id="startupSteps"></div>
      <div class="startup-facts" id="startupFacts"></div>
    </section>

    <section class="admin-panel hidden" id="adminPanel">
      <div class="section-head">
        <div>
          <h2>Admin Console</h2>
          <p class="muted" id="adminSummaryLine">Security, users, roles and plugins.</p>
        </div>
        <button type="button" data-admin-action="refresh">Refresh Admin</button>
      </div>
      <div class="admin-summary">
        <div class="admin-metric"><strong id="adminMetricUsers">-</strong><span>Users</span></div>
        <div class="admin-metric"><strong id="adminMetricPasskeys">-</strong><span>Passkeys</span></div>
        <div class="admin-metric"><strong id="adminMetricRbac">-</strong><span>RBAC mode</span></div>
        <div class="admin-metric"><strong id="adminMetricPlugins">-</strong><span>Enabled plugins</span></div>
      </div>
      <div class="admin-tabs" role="tablist" aria-label="Admin sections">
        <button type="button" class="active" data-admin-tab="security">Security</button>
        <button type="button" data-admin-tab="users">Users</button>
        <button type="button" data-admin-tab="groups">Groups</button>
        <button type="button" data-admin-tab="roles">Roles</button>
        <button type="button" data-admin-tab="plugins">Plugins</button>
        <button type="button" data-admin-tab="metadata">Metadata</button>
        <button type="button" data-admin-tab="backups">Backups</button>
      </div>
      <div class="admin-view active" data-admin-view="security">
        <div class="admin-grid">
          <div class="admin-card">
            <h3>Security</h3>
            <div class="admin-controls">
              <button type="button" id="adminAuthToggle" data-admin-action="toggle-auth">Toggle Authentication</button>
              <button type="button" id="adminMovieVaultReceiverToggle" data-admin-action="toggle-movievault-receiver">Toggle MovieVault Receiver</button>
              <button type="button" id="adminMovieVaultContributionToggle" data-admin-action="toggle-movievault-contribution">Toggle Release Contribution</button>
            </div>
            <p class="muted">Choose how new users can join this DiscVault environment.</p>
            <div class="admin-mode" id="adminRegistrationMode">
              <button type="button" data-admin-registration-mode="invite">Invite-only login</button>
              <button type="button" data-admin-registration-mode="public">Public registration</button>
            </div>
            <p class="muted" id="adminSecurityState">-</p>
            <p class="muted" id="adminMovieVaultContributionState">-</p>
          </div>
          <div class="admin-card">
            <h3>Create Invite</h3>
            <div class="admin-controls">
              <input id="adminInviteUsername" type="text" placeholder="username">
              <button type="button" data-admin-action="create-invite">Create Invite</button>
            </div>
            <div class="admin-code hidden" id="adminInviteCodeOutput"></div>
          </div>
          <div class="admin-card wide">
            <h3>Passkeys & Invites</h3>
            <div class="admin-list" id="adminCredentialsList"><div class="empty">No passkeys loaded.</div></div>
            <div class="admin-list" id="adminInvitesList"><div class="empty">No invites loaded.</div></div>
          </div>
        </div>
      </div>
      <div class="admin-view" data-admin-view="users">
        <div class="admin-card">
          <h3>Users</h3>
          <div class="admin-list" id="adminUsersList"><div class="empty">No users loaded.</div></div>
        </div>
      </div>
      <div class="admin-view" data-admin-view="groups">
        <div class="admin-card">
          <h3>Media Groups</h3>
          <p class="muted">Groups imported from legacy sharing data.</p>
          <div class="admin-list" id="adminMediaGroupsList"><div class="empty">No groups loaded.</div></div>
        </div>
      </div>
      <div class="admin-view" data-admin-view="roles">
        <div class="admin-grid">
          <div class="admin-card">
            <h3>RBAC Mode</h3>
            <div class="admin-mode">
              <button type="button" id="adminRbacBasicButton" data-admin-rbac-mode="basic">Basic</button>
              <button type="button" id="adminRbacAdvancedButton" data-admin-rbac-mode="advanced">Advanced</button>
            </div>
            <p class="muted" id="adminRbacModeState">-</p>
          </div>
          <div class="admin-card">
            <h3>Permission Catalog</h3>
            <p class="muted" id="adminPermissionSummary">-</p>
            <div class="admin-permission-cloud" id="adminPermissionPreview"></div>
          </div>
          <div class="admin-card wide">
            <h3>Roles</h3>
            <div class="admin-list" id="adminRolesList"><div class="empty">No roles loaded.</div></div>
          </div>
        </div>
      </div>
      <div class="admin-view" data-admin-view="plugins">
        <div class="admin-grid">
          <div class="admin-card wide">
            <h3>Plugin Registry</h3>
            <div class="admin-list" id="adminPluginsList"><div class="empty">No plugins loaded.</div></div>
          </div>
          <div class="admin-card wide">
            <h3>Execution Jobs</h3>
            <div class="admin-list" id="adminPluginJobsList"><div class="empty">No plugin jobs loaded.</div></div>
          </div>
        </div>
      </div>
      <div class="admin-view" data-admin-view="metadata">
        <div class="admin-grid">
          <div class="admin-card wide">
            <h3>Metadata Refresh</h3>
            <div class="admin-controls">
              <input id="adminMetadataMovieId" type="text" placeholder="movie UUID">
              <button type="button" data-admin-action="metadata-use-first">Use First Movie</button>
              <button type="button" data-admin-action="metadata-preview">Preview</button>
              <button type="button" data-admin-action="metadata-dry-run">Dry Run</button>
              <button type="button" data-admin-action="metadata-apply">Apply</button>
              <button type="button" data-admin-action="metadata-queue-dry-run">Queue Dry Run</button>
              <button type="button" data-admin-action="metadata-queue-apply">Queue Apply</button>
            </div>
            <p class="muted" id="adminMetadataState">No metadata refresh run yet.</p>
          </div>
          <div class="admin-card">
            <h3>Source Summary</h3>
            <div class="admin-list" id="adminMetadataSources"><div class="empty">No source result yet.</div></div>
          </div>
          <div class="admin-card">
            <h3>Proposal</h3>
            <div class="admin-list" id="adminMetadataProposal"><div class="empty">No proposal yet.</div></div>
          </div>
          <div class="admin-card wide">
            <div class="admin-row-head">
              <h3>Metadata Jobs</h3>
              <button type="button" data-admin-action="metadata-jobs-refresh">Refresh Jobs</button>
            </div>
            <div class="admin-list" id="adminMetadataJobs"><div class="empty">No metadata jobs loaded.</div></div>
          </div>
        </div>
      </div>
      <div class="admin-view" data-admin-view="backups">
        <div class="admin-grid">
          <div class="admin-card">
            <h3>Functional Collection Backup</h3>
            <p class="muted" id="adminBackupState">No backup status loaded.</p>
            <div class="admin-controls">
              <button type="button" data-admin-action="backup-refresh">Refresh Backup Status</button>
              <button type="button" data-admin-action="backup-export">Export ZIP</button>
            </div>
          </div>
          <div class="admin-card">
            <h3>Validate or Restore ZIP</h3>
            <div class="admin-controls">
              <input id="adminBackupFile" type="file" accept=".zip,application/zip">
              <button type="button" data-admin-action="backup-validate">Validate ZIP</button>
              <button type="button" data-admin-action="backup-restore">Restore ZIP</button>
            </div>
            <p class="muted">Restore replaces functional collection data only. Auth, passkeys, plugins and secrets are excluded.</p>
          </div>
          <div class="admin-card wide">
            <h3>Backup Report</h3>
            <div class="admin-list" id="adminBackupReport"><div class="empty">No backup report yet.</div></div>
            <div class="admin-list" id="adminBackupJobs"><div class="empty">No restore jobs loaded.</div></div>
          </div>
        </div>
      </div>
      <div class="auth-status" id="adminStatusLine"></div>
    </section>

    <div class="toolbar">
      <input id="searchInput" type="search" placeholder="Search title, barcode, format..." data-next-i18n-placeholder="collection.searchPlaceholder" oninput="renderMovies()">
      <div class="filters" id="formatFilters"></div>
    </div>

    <section class="layout">
      <div class="panel">
        <div class="section-head">
          <h2 data-next-i18n="collection.movies">Movies</h2>
          <span class="muted" id="resultCount">""" + h(len(movies)) + """ server rendered</span>
        </div>
        <div class="grid" id="movieGrid">""" + movie_cards + """</div>
      </div>
      <aside class=\"""" + container_panel_class + """\">
        <div class="section-head">
          <h2 data-next-i18n="collection.containers">Containers</h2>
          <span class="muted" id="containerCount">""" + h(len(containers)) + """</span>
        </div>
        <div class="containers" id="containerList">""" + container_cards + """</div>
      </aside>
    </section>

    <div class="detail-overlay" id="movieDetailOverlay" onclick="if(event.target === this) closeMovieDetail()">
      <article class="detail-panel" aria-modal="true" role="dialog" aria-labelledby="detailTitle">
        <div class="detail-hero" id="detailHero">
          <button class="detail-close" type="button" onclick="closeMovieDetail()" aria-label="Close">x</button>
        </div>
        <div class="detail-content">
          <div class="detail-poster" id="detailPoster">No poster</div>
          <div class="detail-main">
            <h2 class="detail-title" id="detailTitle">Loading...</h2>
            <div class="tags" id="detailTags"></div>
            <p class="detail-overview" id="detailOverview"></p>
          </div>
        </div>
        <div class="detail-sections">
          <section class="detail-section">
            <h3>Release</h3>
            <div class="field-list" id="detailRelease"></div>
          </section>
          <section class="detail-section">
            <h3>Identifiers</h3>
            <div class="field-list" id="detailIdentifiers"></div>
          </section>
          <section class="detail-section">
            <h3>Technical Specs</h3>
            <div class="field-list" id="detailSpecs"></div>
          </section>
          <section class="detail-section">
            <h3>Containers</h3>
            <div class="field-list" id="detailContainers"></div>
          </section>
          <section class="detail-section">
            <h3>Groups</h3>
            <div class="field-list" id="detailGroups"></div>
          </section>
          <section class="detail-section">
            <h3>Digital Sources</h3>
            <div class="field-list" id="detailDigital"></div>
          </section>
          <section class="detail-section full">
            <h3 data-next-i18n="movieDetail.videos">Videos</h3>
            <div class="collection-video-groups" id="detailVideos"></div>
          </section>
          <section class="detail-section full">
            <h3>Cast & Crew</h3>
            <div class="credit-list" id="detailCredits"></div>
          </section>
        </div>
      </article>
    </div>
  </main>

  <script>
    var initialState = JSON.parse(""" + json_lib.dumps(initial_state_json) + """);
    var genreLabels = """ + json_lib.dumps(GENRE_KEY_TO_LABEL, separators=(",", ":")) + """;
    var state = {
      movies: initialState.movies || [],
      containers: initialState.containers || [],
      stats: {counts: initialState.counts || {}},
      plugins: initialState.plugins || [],
      activeFormat: "all"
    };
    var authToken = localStorage.getItem("dv_next_token") || "";
    var authState = {};
    var startupState = {};
    var startupPollTimer = null;
    var ownerSettings = {};
    var adminState = {
      users: [],
      credentials: [],
      invites: [],
      mediaGroups: [],
      rbac: {},
      plugins: [],
      pluginConfigs: {},
      pluginHealth: {},
      pluginExecutions: {},
      pluginJobs: [],
      digitalSources: [],
      metadata: {
        jobs: [],
        lastApplied: null,
        lastPreview: null,
        movieId: ""
      },
      backup: {},
      backupReport: null
    };

    function escapeHtml(value) {
      return String(value == null ? "" : value).replace(/[&<>"']/g, function (char) {
        const escapes = {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"};
        return escapes[char] || char;
      });
    }
    function setClientStatus(message) {
      const node = document.getElementById("clientStatus");
      if (node) node.textContent = message;
    }
    function number(value) {
      return Number(value || 0).toLocaleString();
    }
    const NEXT_I18N_FALLBACK_LOCALES = [
      {locale: "nl-NL", legacy: "nl", nativeName: "Nederlands"},
      {locale: "en-US", legacy: "en", nativeName: "English"},
      {locale: "fr-FR", legacy: "fr", nativeName: "Francais"},
      {locale: "de-DE", legacy: "de", nativeName: "Deutsch"},
      {locale: "es-ES", legacy: "es", nativeName: "Espanol"},
      {locale: "pt-PT", legacy: "pt", nativeName: "Portugues"},
      {locale: "it-IT", legacy: "it", nativeName: "Italiano"},
      {locale: "sv-SE", legacy: "sv", nativeName: "Svenska"},
      {locale: "da-DK", legacy: "da", nativeName: "Dansk"},
      {locale: "nb-NO", legacy: "no", nativeName: "Norsk"},
      {locale: "fi-FI", legacy: "fi", nativeName: "Suomi"}
    ];
    const nextI18n = {
      locale: "nl-NL",
      messages: {},
      locales: NEXT_I18N_FALLBACK_LOCALES
    };
    function normalizeNextLocale(value) {
      const raw = String(value || "").trim().replace("_", "-").toLowerCase();
      const aliases = {};
      nextI18n.locales.forEach((item) => {
        aliases[String(item.locale).toLowerCase()] = item.locale;
        aliases[String(item.legacy).toLowerCase()] = item.locale;
      });
      aliases.nb = "nb-NO";
      aliases["no-no"] = "nb-NO";
      aliases["en-gb"] = "en-US";
      if (raw && aliases[raw]) return aliases[raw];
      const language = raw.split("-", 1)[0];
      return aliases[language] || "nl-NL";
    }
    function legacyLocale(locale) {
      const found = nextI18n.locales.find((item) => item.locale === locale);
      return found ? found.legacy : String(locale || "nl-NL").split("-", 1)[0];
    }
    function preferredNextLocale() {
      const stored = localStorage.getItem("dv_next_locale") || localStorage.getItem("dv_lang");
      if (stored) return normalizeNextLocale(stored);
      const browserLocales = navigator.languages && navigator.languages.length ? navigator.languages : [navigator.language];
      for (const locale of browserLocales) {
        const normalized = normalizeNextLocale(locale);
        if (normalized) return normalized;
      }
      return "nl-NL";
    }
    function tNext(key, fallback) {
      return nextI18n.messages[key] || fallback || key;
    }
    function translatedState(state) {
      const key = `migration.state.${state || "unknown"}`;
      return tNext(key, String(state || "unknown").replaceAll("_", " "));
    }
    function applyNextI18n() {
      document.documentElement.lang = nextI18n.locale;
      document.querySelectorAll("[data-next-i18n]").forEach((node) => {
        node.textContent = tNext(node.dataset.nextI18n, node.textContent);
      });
      document.querySelectorAll("[data-next-i18n-placeholder]").forEach((node) => {
        node.setAttribute("placeholder", tNext(node.dataset.nextI18nPlaceholder, node.getAttribute("placeholder") || ""));
      });
      document.querySelectorAll("[data-next-i18n-aria]").forEach((node) => {
        node.setAttribute("aria-label", tNext(node.dataset.nextI18nAria, node.getAttribute("aria-label") || ""));
      });
      const select = document.getElementById("nextLanguageSelect");
      if (select) select.value = nextI18n.locale;
      renderFormatFilters();
      renderMovies();
      renderContainers();
      if (startupState && Object.keys(startupState).length) renderStartupStatus();
    }
    function renderLanguageOptions() {
      const select = document.getElementById("nextLanguageSelect");
      if (!select) return;
      select.innerHTML = nextI18n.locales.map((item) => {
        return `<option value="${escapeHtml(item.locale)}">${escapeHtml(item.nativeName || item.locale)}</option>`;
      }).join("");
      select.value = nextI18n.locale;
    }
    async function setNextLocale(locale, options) {
      const normalized = normalizeNextLocale(locale);
      nextI18n.locale = normalized;
      if (options && options.persist) {
        localStorage.setItem("dv_next_locale", normalized);
        localStorage.setItem("dv_lang", legacyLocale(normalized));
      }
      try {
        const response = await fetch(`/api/next/i18n/${encodeURIComponent(normalized)}`, {cache: "no-store"});
        if (response.ok) {
          const payload = await response.json();
          nextI18n.locale = payload.locale || normalized;
          nextI18n.messages = payload.messages || {};
          if (Array.isArray(payload.locales) && payload.locales.length) {
            nextI18n.locales = payload.locales;
          }
        }
      } catch (error) {
        console.warn("Next i18n catalog unavailable", error);
      }
      renderLanguageOptions();
      applyNextI18n();
    }
    async function initNextI18n() {
      try {
        const response = await fetch("/api/next/i18n", {cache: "no-store"});
        if (response.ok) {
          const payload = await response.json();
          if (Array.isArray(payload.locales) && payload.locales.length) {
            nextI18n.locales = payload.locales;
          }
        }
      } catch (error) {
        console.warn("Next i18n manifest unavailable", error);
      }
      renderLanguageOptions();
      const select = document.getElementById("nextLanguageSelect");
      if (select && select.dataset.bound !== "true") {
        select.dataset.bound = "true";
        select.addEventListener("change", () => setNextLocale(select.value, {persist: true}));
      }
      await setNextLocale(preferredNextLocale(), {persist: false});
    }
    function systemTheme() {
      return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    }
    function currentThemePreference() {
      return localStorage.getItem("dv_next_theme") || "system";
    }
    function applyTheme(preference) {
      const theme = preference === "system" ? systemTheme() : preference;
      document.documentElement.dataset.themePreference = preference;
      document.documentElement.dataset.theme = theme;
      document.querySelectorAll("[data-theme-choice]").forEach((button) => {
        button.classList.toggle("active", button.dataset.themeChoice === preference);
        button.setAttribute("aria-pressed", button.dataset.themeChoice === preference ? "true" : "false");
      });
    }
    function setThemePreference(preference) {
      localStorage.setItem("dv_next_theme", preference);
      applyTheme(preference);
    }
    function bindThemeControls() {
      applyTheme(currentThemePreference());
      document.querySelectorAll("[data-theme-choice]").forEach((button) => {
        if (button.dataset.bound === "true") return;
        button.dataset.bound = "true";
        button.addEventListener("click", () => setThemePreference(button.dataset.themeChoice || "system"));
      });
      if (window.matchMedia) {
        const media = window.matchMedia("(prefers-color-scheme: dark)");
        const handler = () => {
          if (currentThemePreference() === "system") applyTheme("system");
        };
        if (media.addEventListener) media.addEventListener("change", handler);
        else if (media.addListener) media.addListener(handler);
      }
    }
    function usableImage(url) {
      if (!url) return "";
      if (String(url).startsWith("http://") || String(url).startsWith("https://")) return url;
      if (String(url).startsWith("/api/next/media/")) return url;
      if (String(url).startsWith("/api/next/movievault-v2/posters/")) return url;
      return "";
    }
    function mediaAssetImage(assets, kind) {
      const rows = Array.isArray(assets) ? assets : [];
      const ordered = [
        ...rows.filter((asset) => asset.kind === kind && asset.is_primary),
        ...rows.filter((asset) => asset.kind === kind && !asset.is_primary)
      ];
      for (const asset of ordered) {
        const url = usableImage(asset.url || asset.source_url);
        if (url) return url;
      }
      return "";
    }
    function cssUrl(url) {
      return String(url || "").replace(/["\\\\]/g, "");
    }
    function valueOrDash(value) {
      if (value === null || value === undefined || value === "") return "-";
      if (Array.isArray(value)) return value.length ? value.join(", ") : "-";
      if (typeof value === "object") return JSON.stringify(value);
      return value;
    }
    function authHeaders() {
      return authToken ? {"Authorization": `Bearer ${authToken}`} : {};
    }
    function currentMobileAuthFlow() {
      try {
        return new URLSearchParams(window.location.search || "").get("mobile_flow") || "";
      } catch (error) {
        return "";
      }
    }
    function base64urlToBuffer(value) {
      let normalized = String(value || "").replace(/-/g, "+").replace(/_/g, "/");
      while (normalized.length % 4) normalized += "=";
      const binary = atob(normalized);
      const bytes = new Uint8Array(binary.length);
      for (let index = 0; index < binary.length; index += 1) {
        bytes[index] = binary.charCodeAt(index);
      }
      return bytes.buffer;
    }
    function bufferToBase64url(buffer) {
      const bytes = new Uint8Array(buffer);
      let binary = "";
      bytes.forEach((byte) => { binary += String.fromCharCode(byte); });
      return btoa(binary).replace(/\\+/g, "-").replace(/\\//g, "_").replace(/=+$/, "");
    }
    function setAuthStatus(message, tone) {
      const node = document.getElementById("authStatusLine");
      if (!node) return;
      applyFaqMessage(node, message);
      node.className = `auth-status ${tone || ""}`.trim();
    }
    function reportClientError(error) {
      const message = error && error.message ? error.message : String(error || "Unknown browser error");
      setAuthStatus(message, "bad");
    }
    function isLikelyIpHost(hostname) {
      const host = String(hostname || "").trim().toLowerCase();
      if (!host || host === "localhost") return false;
      if (/^\\d{1,3}(?:\\.\\d{1,3}){3}$/.test(host)) return true;
      return host.includes(":");
    }
    function applyFaqMessage(node, message) {
      node.textContent = "";
      const text = String(message || "");
      const faqUrl = "https://discvault.eu/faq";
      let cursor = 0;
      let found = text.indexOf(faqUrl);
      if (found === -1) {
        node.textContent = text;
        return;
      }
      while (found !== -1) {
        if (found > cursor) node.appendChild(document.createTextNode(text.slice(cursor, found)));
        const anchor = document.createElement("a");
        anchor.href = faqUrl;
        anchor.textContent = faqUrl;
        anchor.target = "_blank";
        anchor.rel = "noreferrer";
        node.appendChild(anchor);
        cursor = found + faqUrl.length;
        found = text.indexOf(faqUrl, cursor);
      }
      if (cursor < text.length) node.appendChild(document.createTextNode(text.slice(cursor)));
    }
    function passkeyUpnSetupMessage() {
      return tNext(
        "auth.passkeyUpnRequired",
        "Passkeys cannot be created on an IP-only URL. Configure a UPN/hostname for DiscVault and open the app with that hostname over HTTPS. Help: https://discvault.eu/faq"
      );
    }
    function passkeyRpMismatchMessage() {
      return tNext(
        "auth.passkeyRpMismatch",
        "Passkeys are blocked because the configured Relying Party ID does not match the address you are using. Open DiscVault on the exact UPN/hostname that matches its passkey configuration (not an IP address), over HTTPS. Help: https://discvault.eu/faq"
      );
    }
    function passkeyClientErrorMessage(error, cancelledFallback, defaultFallback) {
      if (error && error.name === "NotAllowedError") return cancelledFallback;
      const rawMessage = String((error && error.message) || "");
      if (/registrable domain suffix|not equal to the current domain|relying party id/i.test(rawMessage)) {
        return passkeyRpMismatchMessage();
      }
      if ((error && error.name === "NotSupportedError") || /not supported/i.test(rawMessage)) {
        return webauthnUnavailableReason() || passkeyUpnSetupMessage();
      }
      return rawMessage || defaultFallback;
    }
    function webauthnUnavailableReason() {
      if (!window.PublicKeyCredential || !navigator.credentials) {
        return "This browser does not support passkeys.";
      }
      if (isLikelyIpHost(window.location.hostname)) {
        return passkeyUpnSetupMessage();
      }
      if (!window.isSecureContext) {
        return "Open this app over HTTPS to use passkeys.";
      }
      return "";
    }
    async function authJson(url, options) {
      const body = options && options.body;
      const headers = {
        ...authHeaders(),
        ...((options && options.headers) || {})
      };
      if (!(body instanceof FormData) && !headers["Content-Type"]) {
        headers["Content-Type"] = "application/json";
      }
      const response = await fetch(url, {
        cache: "no-store",
        credentials: "same-origin",
        ...(options || {}),
        headers
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || payload.error) {
        throw new Error(payload.error || `${url} failed with HTTP ${response.status}`);
      }
      return payload;
    }
    function passkeyProcessAvailable() {
      return authState.passkey_access_valid === undefined
        ? true
        : Boolean(authState.passkey_access_valid);
    }
    function passkeyConfigurationGuidanceVisible() {
      return !passkeyProcessAvailable()
        && (Boolean(authState.passkey_configuration_valid)
          || !Boolean(authState.request_host_is_local_ip));
    }
    function renderPasskeyConfigurationGuidance(description) {
      const configurationValid = Boolean(authState.passkey_configuration_valid);
      description.textContent = configurationValid
        ? tNext("auth.passkeyConfiguredAddressBody", "Passkeys are configured, but this address does not match. Open DiscVault through the configured HTTPS address to continue.")
        : tNext("auth.passkeyFqdnRequiredBody", "Passkey onboarding requires a valid RP_ID and HTTPS RP_ORIGIN for a fully qualified domain name. Configure these values and open DiscVault through that address.");
      const configuredOrigin = configurationValid && Array.isArray(authState.rp_origins)
        ? String(authState.rp_origins[0] || "")
        : "";
      try {
        const url = new URL(configuredOrigin);
        if (url.protocol === "https:") {
          const originLink = document.createElement("a");
          originLink.href = url.origin;
          originLink.textContent = tNext("auth.openConfiguredOrigin", "Open {origin}").replace("{origin}", url.origin);
          description.append(" ", originLink);
        }
      } catch (_) { /* invalid origins are never linked */ }
      const documentationLink = document.createElement("a");
      documentationLink.href = "https://docs.discvault.eu";
      documentationLink.target = "_blank";
      documentationLink.rel = "noopener noreferrer";
      documentationLink.textContent = tNext("auth.passkeyDocumentation", "Passkey documentation");
      description.append(" ", documentationLink);
    }
    function renderAuthStatus() {
      const title = document.getElementById("authTitle");
      const description = document.getElementById("authDescription");
      const meta = document.getElementById("authMeta");
      const setupFields = document.getElementById("authSetupFields");
      const setupButton = document.getElementById("authSetupButton");
      const joinButton = document.getElementById("authJoinButton");
      const reviewButton = document.getElementById("authReviewButton");
      const reviewFields = document.getElementById("authReviewFields");
      const loginButton = document.getElementById("authLoginButton");
      const logoutButton = document.getElementById("authLogoutButton");
      const inviteLabel = document.getElementById("authInviteLabel");
      const passkeyAvailable = passkeyProcessAvailable();
      const unavailable = passkeyAvailable ? webauthnUnavailableReason() : "";
      const setupRequired = !!authState.setup_required;
      const authenticated = !!authState.authenticated;
      const reviewLoginAvailable = !!authState.legacy_auth_enabled;
      const joinAllowed = !setupRequired && !authenticated && !!authState.auth_enabled;
      title.textContent = authenticated
        ? "Signed in"
        : setupRequired
          ? "First passkey"
          : !passkeyAvailable && reviewLoginAvailable
            ? tNext("legacyAuth.password", "Password")
            : "Passkeys";
      description.textContent = authenticated
        ? `Signed in${authState.role ? ` as ${authState.role}` : ""}.`
        : setupRequired
          ? "Create the first owner passkey for this DiscVault Next instance."
          : !passkeyAvailable && reviewLoginAvailable
            ? tNext("auth.signIn", "Sign in")
          : authState.registration_enabled
            ? "Sign in with your passkey or create a new account."
            : "Sign in with your passkey or create an account with an invite code.";
      meta.textContent = `RP ID: ${authState.rp_id || "-"}; origins: ${(authState.rp_origins || []).join(", ") || "-"}`;
      setupFields.classList.toggle("hidden", !passkeyAvailable || !(setupRequired || joinAllowed));
      inviteLabel.classList.toggle("hidden", !joinAllowed || !!authState.registration_enabled);
      setupButton.classList.toggle("hidden", !passkeyAvailable || !setupRequired);
      joinButton.classList.toggle("hidden", !passkeyAvailable || !joinAllowed);
      reviewButton.classList.toggle("hidden", setupRequired || authenticated || !reviewLoginAvailable);
      reviewFields.classList.toggle("hidden", !reviewLoginAvailable || setupRequired || authenticated);
      loginButton.classList.toggle("hidden", !passkeyAvailable || setupRequired || authenticated);
      logoutButton.classList.toggle("hidden", !authenticated);
      setupButton.disabled = !!unavailable;
      joinButton.disabled = !!unavailable;
      loginButton.disabled = !!unavailable;
      if (!passkeyAvailable && !authenticated && (setupRequired || passkeyConfigurationGuidanceVisible())) {
        renderPasskeyConfigurationGuidance(description);
        setAuthStatus("", "info");
      } else if (unavailable) {
        setAuthStatus(unavailable, "bad");
      } else if (!document.getElementById("authStatusLine").textContent) {
        setAuthStatus(setupRequired ? "Ready to create a passkey." : "Ready.", "info");
      }
    }
    async function refreshAuthStatus() {
      try {
        authState = await authJson("/api/next/auth/status", {headers: authHeaders()});
        renderAuthStatus();
        renderAdminVisibility();
      } catch (error) {
        setAuthStatus(error.message, "bad");
      }
    }
    function setStartupStatus(message, tone) {
      const node = document.getElementById("startupStatusLine");
      if (!node) return;
      node.textContent = message || "";
      node.className = `auth-status ${tone || ""}`.trim();
    }
    function startupTitle(phase) {
      const titles = {
        owner_setup: tNext("startup.phase.owner_setup", "First owner"),
        migration_required: tNext("startup.phase.migration_required", "Migration required"),
        migration_running: tNext("startup.phase.migration_running", "Migration running"),
        migration_pending_non_admin: tNext("startup.phase.migration_pending_non_admin", "Migration pending"),
        schema_blocked: tNext("startup.phase.schema_blocked", "Schema update required"),
        sign_in_required: tNext("startup.phase.sign_in_required", "Sign in required"),
        ready: tNext("startup.phase.ready", "Ready")
      };
      return titles[phase] || tNext("startup.setup", "Setup");
    }
    function startupDescription(phase, fallback) {
      const descriptions = {
        owner_setup: tNext("startup.description.owner_setup", "Follow the steps to create an owner account and complete setup."),
        migration_required: tNext("startup.description.migration_required", "Legacy DiscVault data is ready to migrate."),
        migration_running: tNext("startup.description.migration_running", "Legacy migration is running."),
        migration_pending_non_admin: tNext("startup.description.migration_pending_non_admin", "DiscVault Next is waiting for an owner or administrator to complete migration."),
        schema_blocked: tNext("startup.description.schema_blocked", "PostgreSQL migrations must finish before DiscVault Next can start."),
        sign_in_required: tNext("startup.description.sign_in_required", "Sign in to continue setup."),
        ready: tNext("startup.description.ready", "DiscVault Next is ready.")
      };
      return descriptions[phase] || fallback || tNext("startup.checking", "Checking startup state...");
    }
    function startupTone(phase) {
      if (phase === "schema_blocked") return "bad";
      if (phase === "migration_pending_non_admin") return "bad";
      if (phase === "migration_running") return "info";
      return "info";
    }
    function renderStartupSteps(steps) {
      const node = document.getElementById("startupSteps");
      if (!node) return;
      const rows = Array.isArray(steps) ? steps : [];
      const labels = {
        auth: tNext("startup.step.auth", "Owner account"),
        source: tNext("startup.step.source", "Legacy data"),
        migration: tNext("startup.step.migration", "Migration"),
        collection: tNext("startup.step.collection", "Collection")
      };
      const details = {
        auth: tNext("startup.step.authDetail", "Authentication protects this DiscVault Next environment."),
        source: tNext("startup.step.sourceDetail", "DiscVault checks the configured import source."),
        migration: tNext("startup.step.migrationDetail", "Import the legacy database into PostgreSQL."),
        collection: tNext("startup.step.collectionDetail", "Browse the collection after setup is complete.")
      };
      node.innerHTML = rows.map((step, index) => {
        const state = step.state || "pending";
        const marker = state === "complete" ? "OK" : state === "skipped" ? "-" : String(index + 1);
        const detail = step.detail || details[step.key] || state;
        return `
          <div class="startup-step ${escapeHtml(state)}">
            <div class="startup-step-marker">${escapeHtml(marker)}</div>
            <div>
              <strong>${escapeHtml(labels[step.key] || step.label || step.key || tNext("startup.step", "Step"))}</strong>
              <span>${escapeHtml(detail)}</span>
            </div>
          </div>
        `;
      }).join("") || `<div class="startup-step"><div class="startup-step-marker">1</div><div><strong>${escapeHtml(tNext("startup.setup", "Setup"))}</strong><span>${escapeHtml(tNext("startup.checking", "Checking startup state..."))}</span></div></div>`;
    }
    function renderStartupFacts(startup) {
      const node = document.getElementById("startupFacts");
      if (!node) return;
      if ((startup.phase || "") === "owner_setup") {
        node.innerHTML = "";
        node.classList.add("hidden");
        return;
      }
      const migration = startup.migration || {};
      const legacyData = migration.legacyData || {};
      const sourceCounts = legacyData.sourceCounts || {};
      const mediaExtensions = legacyData.mediaExtensions || {};
      const sourceLabel = legacyData.pluginName || ((migration.importSources || [])[0] || {}).pluginName;
      const facts = [
        [tNext("collection.movies", "Movies"), sourceCounts.movies],
        [tNext("migration.people", "People"), sourceCounts.people],
        [tNext("migration.users", "Users"), sourceCounts.users],
        [tNext("migration.groups", "Groups"), sourceCounts.groups],
        [tNext("migration.passkeys", "Passkeys"), sourceCounts.credentials],
        [tNext("migration.images", "Images"), Object.values(mediaExtensions).reduce((total, value) => total + Number(value || 0), 0)]
      ].filter((item) => Number(item[1] || 0) > 0);
      const sourceFact = sourceLabel
        ? `<span><strong>${escapeHtml(sourceLabel)}</strong>${escapeHtml(tNext("migration.importSource", "Import Source"))}</span>`
        : "";
      node.innerHTML = [sourceFact, ...facts.map(([label, value]) => `<span><strong>${number(value)}</strong>${escapeHtml(label)}</span>`)].filter(Boolean).join("");
      node.classList.toggle("hidden", !sourceFact && facts.length === 0);
    }
    function scheduleStartupPoll(phase) {
      if (startupPollTimer) {
        clearTimeout(startupPollTimer);
        startupPollTimer = null;
      }
      if (phase !== "migration_running") return;
      startupPollTimer = setTimeout(() => {
        resumeStartupOrCollection().catch(reportClientError);
      }, 3000);
    }
    function renderStartupStatus() {
      const panel = document.getElementById("startupPanel");
      if (!panel) return;
      const startup = startupState.startup || startupState || {};
      const phase = startup.phase || "ready";
      panel.classList.toggle("hidden", phase === "ready");
      scheduleStartupPoll(phase);
      if (phase === "ready") return;
      document.getElementById("startupTitle").textContent = startupTitle(phase);
      document.getElementById("startupDescription").textContent = startupDescription(phase, startup.message);
      const migration = startup.migration || {};
      const auth = startup.auth || {};
      const legacyData = migration.legacyData || {};
      const sourceCounts = legacyData.sourceCounts || {};
      const showMigrationContext = phase !== "owner_setup" && !!migration.state;
      document.getElementById("startupMeta").innerHTML = [
        showMigrationContext ? `${tNext("startup.metaMigration", "Migration")}: ${translatedState(migration.state)}` : "",
        showMigrationContext && legacyData.pluginName ? `${tNext("startup.metaSource", "Source")}: ${legacyData.pluginName}` : "",
        showMigrationContext && Number(sourceCounts.movies || 0) ? `${number(sourceCounts.movies)} ${tNext("startup.metaLegacyMovies", "legacy movies")}` : "",
        showMigrationContext && Number(sourceCounts.groups || 0) ? `${number(sourceCounts.groups)} ${tNext("startup.metaLegacyGroups", "legacy groups")}` : "",
        auth.role ? `${tNext("startup.metaSignedIn", "Signed in")}: ${auth.role}` : ""
      ].filter(Boolean).map((item) => `<span>${escapeHtml(item)}</span>`).join("");
      renderStartupSteps(startup.steps);
      renderStartupFacts(startup);
      const authButton = document.getElementById("startupAuthButton");
      const canOpenAuth = startup.canSignIn
        || startup.canSwitchAccount
        || (startup.canCreateOwner && passkeyProcessAvailable());
      authButton.classList.toggle("hidden", !canOpenAuth);
      authButton.textContent = startup.canCreateOwner
        ? tNext("auth.createOwnerPasskey", "Create owner passkey")
        : startup.canSwitchAccount
          ? tNext("startup.switchAccount", "Switch Account")
          : tNext("auth.signIn", "Sign in");
      const startupMigrationButton = document.getElementById("startupStartMigrationButton");
      startupMigrationButton.classList.toggle("hidden", !showMigrationContext || !startup.canStartMigration);
      startupMigrationButton.textContent = tNext("startup.openMigrationGuide", "Open migration guide");
      document.getElementById("startupMigrationButton").classList.toggle("hidden", !showMigrationContext);
      document.getElementById("startupRefreshButton").classList.toggle("hidden", phase === "ready");
      const statusMessage = phase === "migration_pending_non_admin"
        ? tNext("startup.status.pendingNonAdmin", "Ask the owner or administrator to finish migration, or switch to an account with migration rights.")
        : phase === "migration_running"
          ? tNext("startup.status.running", "Migration is running. This panel refreshes automatically.")
          : "";
      setStartupStatus(statusMessage, startupTone(phase));
    }
    async function refreshStartupStatus() {
      startupState = await authJson("/api/next/startup/status", {headers: authHeaders()});
      renderStartupStatus();
      return startupState.startup || startupState || {};
    }
    async function startStartupMigration() {
      const button = document.getElementById("startupStartMigrationButton");
      button.disabled = true;
      setStartupStatus(tNext("migration.starting", "Starting migration..."), "info");
      try {
        await authJson("/api/next/migration/start", {method: "POST", body: "{}"});
        setStartupStatus(tNext("startup.status.migrationQueued", "Migration queued. Refreshing status..."), "good");
        await resumeStartupOrCollection();
      } catch (error) {
        setStartupStatus(error.message, "bad");
      } finally {
        button.disabled = false;
      }
    }
    function setStartupMessage(message, tone) {
      const node = document.getElementById("startupMessage");
      if (!node) return;
      applyFaqMessage(node, message);
      node.className = `login-message ${tone || ""}`.trim();
    }
    async function registerOwnerPasskey(triggerButton = null) {
      setAuthStatus("Create owner passkey clicked.", "info");
      if (!passkeyProcessAvailable()) {
        renderAuthStatus();
        return;
      }
      const unavailable = webauthnUnavailableReason();
      if (unavailable) {
        setAuthStatus(unavailable, "bad");
        setStartupMessage(unavailable, "bad");
        return;
      }
      const username = document.getElementById("authUsername")?.value.trim() || "admin";
      const credentialName = document.getElementById("authCredentialName")?.value.trim() || "Owner passkey";
      const button = triggerButton || document.getElementById("authSetupButton");
      if (button) button.disabled = true;
      setAuthStatus("Waiting for your passkey prompt...", "info");
      setStartupMessage(tNext("auth.waitingForPasskey", "Waiting for your passkey prompt..."), "info");
      try {
        const optionsPayload = await authJson("/api/next/auth/register/options", {
          method: "POST",
          body: JSON.stringify({username, display_name: username})
        });
        const options = optionsPayload.options;
        options.challenge = base64urlToBuffer(options.challenge);
        options.user.id = base64urlToBuffer(options.user.id);
        options.excludeCredentials = (options.excludeCredentials || []).map((credential) => ({
          ...credential,
          id: base64urlToBuffer(credential.id)
        }));
        const attestation = await navigator.credentials.create({publicKey: options});
        const credential = {
          id: attestation.id,
          rawId: bufferToBase64url(attestation.rawId),
          response: {
            attestationObject: bufferToBase64url(attestation.response.attestationObject),
            clientDataJSON: bufferToBase64url(attestation.response.clientDataJSON)
          },
          type: attestation.type,
          authenticatorAttachment: attestation.authenticatorAttachment
        };
        const verified = await authJson("/api/next/auth/register/verify", {
          method: "POST",
          body: JSON.stringify({
            user_id: optionsPayload.user_id,
            username,
            display_name: username,
            credential_name: credentialName,
            credential
          })
        });
        authToken = verified.token || "";
        if (authToken) localStorage.setItem("dv_next_token", authToken);
        setAuthStatus("Passkey created. You are signed in.", "good");
        setStartupMessage(tNext("auth.passkeyCreated", "Passkey created. You are signed in."), "good");
        await refreshAuthStatus();
        await resumeStartupOrCollection();
      } catch (error) {
        setAuthStatus(passkeyClientErrorMessage(error, "Passkey prompt was cancelled.", "Owner passkey could not be created."), "bad");
        setStartupMessage(passkeyClientErrorMessage(error, tNext("auth.passkeyCancelled", "Passkey prompt was cancelled."), tNext("auth.ownerCreateFailed", "Owner passkey could not be created.")), "bad");
      } finally {
        if (button) button.disabled = false;
      }
    }
    async function registerInvitedPasskey() {
      setAuthStatus("Create account clicked.", "info");
      const unavailable = webauthnUnavailableReason();
      if (unavailable) {
        setAuthStatus(unavailable, "bad");
        return;
      }
      const username = document.getElementById("authUsername").value.trim();
      const credentialName = document.getElementById("authCredentialName").value.trim() || "Passkey";
      const inviteCode = document.getElementById("authInviteCode").value.trim();
      if (!username) {
        setAuthStatus("Username is required.", "bad");
        return;
      }
      if (!authState.registration_enabled && !inviteCode) {
        setAuthStatus("Invite code is required.", "bad");
        return;
      }
      const button = document.getElementById("authJoinButton");
      button.disabled = true;
      setAuthStatus("Waiting for your passkey prompt...", "info");
      try {
        const optionsPayload = await authJson("/api/next/auth/register/options", {
          method: "POST",
          body: JSON.stringify({username, display_name: username, invite_code: inviteCode})
        });
        const options = optionsPayload.options;
        options.challenge = base64urlToBuffer(options.challenge);
        options.user.id = base64urlToBuffer(options.user.id);
        options.excludeCredentials = (options.excludeCredentials || []).map((credential) => ({
          ...credential,
          id: base64urlToBuffer(credential.id)
        }));
        const attestation = await navigator.credentials.create({publicKey: options});
        const credential = {
          id: attestation.id,
          rawId: bufferToBase64url(attestation.rawId),
          response: {
            attestationObject: bufferToBase64url(attestation.response.attestationObject),
            clientDataJSON: bufferToBase64url(attestation.response.clientDataJSON)
          },
          type: attestation.type,
          authenticatorAttachment: attestation.authenticatorAttachment
        };
        const verified = await authJson("/api/next/auth/register/verify", {
          method: "POST",
          body: JSON.stringify({
            user_id: optionsPayload.user_id,
            username,
            display_name: username,
            credential_name: credentialName,
            invite_code: inviteCode,
            credential
          })
        });
        authToken = verified.token || "";
        if (authToken) localStorage.setItem("dv_next_token", authToken);
        setAuthStatus("Account created. You are signed in.", "good");
        await refreshAuthStatus();
        await resumeStartupOrCollection();
      } catch (error) {
        setAuthStatus(passkeyClientErrorMessage(error, "Passkey prompt was cancelled.", "Invite sign-up failed."), "bad");
      } finally {
        button.disabled = false;
      }
    }
    async function loginPasskey() {
      setAuthStatus("Sign in clicked.", "info");
      const unavailable = webauthnUnavailableReason();
      if (unavailable) {
        setAuthStatus(unavailable, "bad");
        return;
      }
      const button = document.getElementById("authLoginButton");
      button.disabled = true;
      setAuthStatus("Waiting for your passkey prompt...", "info");
      try {
        const optionsPayload = await authJson("/api/next/auth/login/options", {
          method: "POST",
          body: "{}"
        });
        const options = optionsPayload.options;
        options.challenge = base64urlToBuffer(options.challenge);
        options.allowCredentials = (options.allowCredentials || []).map((credential) => ({
          ...credential,
          id: base64urlToBuffer(credential.id)
        }));
        const assertion = await navigator.credentials.get({publicKey: options});
        const credential = {
          id: assertion.id,
          rawId: bufferToBase64url(assertion.rawId),
          response: {
            authenticatorData: bufferToBase64url(assertion.response.authenticatorData),
            clientDataJSON: bufferToBase64url(assertion.response.clientDataJSON),
            signature: bufferToBase64url(assertion.response.signature),
            userHandle: assertion.response.userHandle ? bufferToBase64url(assertion.response.userHandle) : null
          },
          type: assertion.type,
          authenticatorAttachment: assertion.authenticatorAttachment
        };
        const verifyBody = {credential};
        const mobileFlow = currentMobileAuthFlow();
        if (mobileFlow) verifyBody.mobile_flow = mobileFlow;
        const verified = await authJson("/api/next/auth/login/verify", {
          method: "POST",
          body: JSON.stringify(verifyBody)
        });
        if (verified.callback_url || verified.callbackUrl) {
          window.location.href = verified.callback_url || verified.callbackUrl;
          return;
        }
        authToken = verified.token || "";
        if (authToken) localStorage.setItem("dv_next_token", authToken);
        setAuthStatus("Signed in.", "good");
        await refreshAuthStatus();
        await resumeStartupOrCollection();
      } catch (error) {
        setAuthStatus(passkeyClientErrorMessage(error, "Passkey prompt was cancelled.", "Sign-in with passkey failed."), "bad");
      } finally {
        button.disabled = false;
      }
    }
    async function loginReviewPassword() {
      if (!authState.legacy_auth_enabled) {
        setAuthStatus(tNext("legacyAuth.unavailable", "Password login is not available."), "bad");
        return;
      }
      const username = String(document.getElementById("authReviewUsername")?.value || "").trim();
      const password = String(document.getElementById("authReviewPassword")?.value || "");
      if (!username || !password) {
        setAuthStatus("Enter username and password.", "bad");
        return;
      }
      const button = document.getElementById("authReviewButton");
      button.disabled = true;
      setAuthStatus("Signing in with username/password...", "info");
      try {
        const reviewBody = {username, password};
        const mobileFlow = currentMobileAuthFlow();
        if (mobileFlow) reviewBody.mobile_flow = mobileFlow;
        let payload = await authJson("/api/next/auth/legacy/login", {
          method: "POST",
          body: JSON.stringify(reviewBody)
        });
        while (payload.stage && payload.stage !== "complete") {
          if (payload.stage === "password_change") {
            const newPassword = window.prompt(tNext("legacyAuth.newPassword", "New password"));
            if (!newPassword) throw new Error(tNext("legacyAuth.newPassword", "New password"));
            payload = await authJson("/api/next/auth/legacy/password/change", {
              method: "POST",
              body: JSON.stringify({flow_token: payload.flow_token, new_password: newPassword})
            });
          } else if (payload.stage === "mfa_enrollment") {
            setAuthStatus(`${tNext("legacyAuth.manualKey", "Manual key")}: ${payload.manual_key}`, "info");
            const code = window.prompt(tNext("legacyAuth.authenticatorCode", "Authenticator code"));
            payload = await authJson("/api/next/auth/legacy/mfa/setup/verify", {
              method: "POST",
              body: JSON.stringify({flow_token: payload.flow_token, code})
            });
          } else if (payload.stage === "mfa_challenge") {
            const code = window.prompt(tNext("legacyAuth.authenticatorCode", "Authenticator code"));
            payload = await authJson("/api/next/auth/legacy/mfa/verify", {
              method: "POST",
              body: JSON.stringify({flow_token: payload.flow_token, code})
            });
          } else if (payload.stage === "recovery_codes") {
            setAuthStatus((payload.recovery_codes || []).join("  "), "info");
            if (!window.confirm(tNext("legacyAuth.recoveryAck", "I saved these recovery codes."))) return;
            payload = await authJson("/api/next/auth/legacy/recovery-codes/ack", {
              method: "POST",
              body: JSON.stringify({flow_token: payload.flow_token, acknowledged: true})
            });
          } else {
            throw new Error(tNext("legacyAuth.failed", "Password sign-in failed."));
          }
        }
        if (payload.callback_url || payload.callbackUrl) {
          window.location.href = payload.callback_url || payload.callbackUrl;
          return;
        }
        authToken = payload.token || "";
        if (authToken) localStorage.setItem("dv_next_token", authToken);
        const passwordInput = document.getElementById("authReviewPassword");
        if (passwordInput) passwordInput.value = "";
        setAuthStatus("Signed in.", "good");
        await refreshAuthStatus();
        await resumeStartupOrCollection();
      } catch (error) {
        setAuthStatus(error.message || "Username/password sign-in failed.", "bad");
      } finally {
        button.disabled = false;
      }
    }
    async function logoutPasskey() {
      try {
        await authJson("/api/next/auth/logout", {method: "POST", body: "{}"});
      } catch (error) {
        console.warn("Server logout failed", error);
      }
      authToken = "";
      localStorage.removeItem("dv_next_token");
      setAuthStatus("Signed out.", "info");
      const adminPanel = document.getElementById("adminPanel");
      if (adminPanel) {
        adminPanel.dataset.loaded = "false";
        adminPanel.classList.add("hidden");
      }
      refreshAuthStatus();
      clearProtectedCollection("Sign in with your passkey to load the collection.");
    }
    function bindAuthButtons() {
      document.querySelectorAll("[data-auth-action]").forEach((button) => {
        if (button.dataset.bound === "true") return;
        button.dataset.bound = "true";
        button.addEventListener("click", (event) => {
          event.preventDefault();
          const action = button.dataset.authAction;
          if (action === "setup") registerOwnerPasskey().catch(reportClientError);
          if (action === "join") registerInvitedPasskey().catch(reportClientError);
          if (action === "login") loginPasskey().catch(reportClientError);
          if (action === "logout") logoutPasskey().catch(reportClientError);
        });
      });
      document.getElementById("authReviewButton")?.addEventListener("click", (event) => {
        event.preventDefault();
        loginReviewPassword().catch(reportClientError);
      });
    }
    function bindStartupActions() {
      document.querySelectorAll("[data-startup-action]").forEach((button) => {
        if (button.dataset.bound === "true") return;
        button.dataset.bound = "true";
        button.addEventListener("click", (event) => {
          event.preventDefault();
          const action = button.dataset.startupAction;
          if (action === "migration") {
            window.location.href = "/api/next/migration";
          }
          if (action === "auth") {
            const startup = startupState.startup || startupState || {};
            if (startup.canCreateOwner) {
              registerOwnerPasskey().catch(reportClientError);
              return;
            }
            if (startup.canSwitchAccount) {
              logoutPasskey().catch(reportClientError);
              return;
            }
            if (startup.canSignIn) {
              loginPasskey().catch(reportClientError);
              return;
            }
            document.getElementById("authPanel").scrollIntoView({behavior: "smooth", block: "start"});
          }
          if (action === "start-migration") {
            window.location.href = "/api/next/migration";
          }
          if (action === "refresh") {
            resumeStartupOrCollection().catch(reportClientError);
          }
        });
      });
    }
    function bindCollectionLinks() {
      if (document.body.dataset.collectionLinksBound === "true") return;
      document.body.dataset.collectionLinksBound = "true";
      document.addEventListener("click", (event) => {
        const movieLink = event.target.closest("[data-movie-id]");
        if (!movieLink) return;
        event.preventDefault();
        openMovieDetail(movieLink.dataset.movieId);
      });
    }
    function setAdminStatus(message, tone) {
      const node = document.getElementById("adminStatusLine");
      if (!node) return;
      node.textContent = message || "";
      node.className = `auth-status ${tone || ""}`.trim();
    }
    function isAdminUser() {
      return !!authState.authenticated && ["owner", "admin"].includes(authState.role || "");
    }
    function renderAdminVisibility() {
      const panel = document.getElementById("adminPanel");
      if (!panel) return;
      panel.classList.toggle("hidden", !isAdminUser());
      if (isAdminUser() && panel.dataset.loaded !== "true") {
        loadAdmin().catch((error) => setAdminStatus(error.message, "bad"));
      }
    }
    function setAdminText(id, value) {
      const node = document.getElementById(id);
      if (node) node.textContent = value;
    }
    function setAdminTab(tab) {
      const selected = tab || "security";
      document.querySelectorAll("[data-admin-tab]").forEach((button) => {
        button.classList.toggle("active", button.dataset.adminTab === selected);
      });
      document.querySelectorAll("[data-admin-view]").forEach((view) => {
        view.classList.toggle("active", view.dataset.adminView === selected);
      });
    }
    function renderAdminSummary() {
      const enabledPlugins = adminState.plugins.filter((plugin) => plugin.enabled).length;
      const mode = adminState.rbac.mode || "-";
      setAdminText("adminMetricUsers", number(adminState.users.length));
      setAdminText("adminMetricPasskeys", number(adminState.credentials.length));
      setAdminText("adminMetricRbac", mode);
      setAdminText("adminMetricPlugins", `${number(enabledPlugins)}/${number(adminState.plugins.length)}`);
      setAdminText(
        "adminSummaryLine",
        `${number(adminState.users.length)} users, ${number(adminState.mediaGroups.length)} groups, ${number(adminState.credentials.length)} passkeys, ${number(enabledPlugins)} enabled plugins.`
      );
    }
    function adminRoleOptions(selectedRole, roles) {
      const options = [...(roles || [])];
      if (selectedRole && !options.some((role) => role.key === selectedRole)) {
        const allRoles = adminState.rbac.roles || [];
        const selected = allRoles.find((role) => role.key === selectedRole) || {key: selectedRole, name: selectedRole, assignable: false};
        options.unshift({...selected, assignable: false});
      }
      return options.map((role) => `
        <option value="${escapeHtml(role.key)}" ${role.key === selectedRole ? "selected" : ""} ${role.assignable === false && role.key !== selectedRole ? "disabled" : ""}>${escapeHtml(role.name || role.key)}</option>
      `).join("");
    }
    function renderAdminUsers(users, roles) {
      const list = document.getElementById("adminUsersList");
      if (!list) return;
      list.innerHTML = users.length ? users.map((user) => {
        const disabled = user.status !== "active";
        const roleLocked = user.role === "owner";
        const canReceiveOwnership = authState.role === "owner"
          && user.id !== authState.user_id
          && user.status === "active"
          && ["admin", "owner"].includes(user.role || "");
        return `
          <div class="admin-row">
            <div class="admin-row-head">
              <strong>${escapeHtml(user.display_name || user.username)}</strong>
              <span class="tag ${disabled ? "" : "good"}">${escapeHtml(user.status || "active")}</span>
            </div>
            <div class="muted">${escapeHtml(user.username)} &middot; ${number(user.credential_count)} passkeys &middot; role ${escapeHtml(user.role || "-")}</div>
            <div class="admin-controls">
              <select data-admin-user-role="${escapeHtml(user.id)}" ${roleLocked ? "disabled" : ""}>${adminRoleOptions(user.role, roles)}</select>
              <button type="button" data-admin-user-status="${escapeHtml(user.id)}" data-status="${disabled ? "active" : "disabled"}">${disabled ? "Enable" : "Disable"}</button>
              ${canReceiveOwnership ? `<button type="button" data-admin-owner-transfer="${escapeHtml(user.id)}" data-username="${escapeHtml(user.display_name || user.username)}">Transfer Owner</button>` : ""}
              <button type="button" data-admin-user-delete="${escapeHtml(user.id)}">Delete</button>
            </div>
          </div>
        `;
      }).join("") : `<div class="empty">No users found.</div>`;
    }
    function renderAdminCredentials(credentials) {
      const list = document.getElementById("adminCredentialsList");
      if (!list) return;
      list.innerHTML = credentials.length ? credentials.map((credential) => `
        <div class="admin-row">
          <div class="admin-row-head">
            <strong>${escapeHtml(credential.credential_name || "Passkey")}</strong>
            <button type="button" data-admin-credential-delete="${escapeHtml(credential.id)}">Delete</button>
          </div>
          <div class="muted">${escapeHtml(credential.username)} &middot; created ${escapeHtml((credential.created_at || "").slice(0, 10))} &middot; last used ${escapeHtml((credential.last_used_at || "-").slice(0, 19))}</div>
        </div>
      `).join("") : `<div class="empty">No passkeys found.</div>`;
    }
    function renderAdminInvites(invites) {
      const list = document.getElementById("adminInvitesList");
      if (!list) return;
      list.innerHTML = invites.length ? invites.map((invite) => {
        const used = !!invite.used_at;
        return `
          <div class="admin-row">
            <div class="admin-row-head">
              <strong>${escapeHtml(invite.username)}</strong>
              <span class="tag ${used ? "good" : "blue"}">${used ? "used" : "open"}</span>
            </div>
            <div class="muted">expires ${escapeHtml((invite.expires_at || "").slice(0, 19))}</div>
            ${used ? "" : `<button type="button" data-admin-invite-delete="${escapeHtml(invite.id)}">Delete invite</button>`}
          </div>
        `;
      }).join("") : `<div class="empty">No invites created.</div>`;
    }
    function renderAdminMediaGroups(groups) {
      const list = document.getElementById("adminMediaGroupsList");
      if (!list) return;
      list.innerHTML = groups.length ? groups.map((group) => {
        const members = group.members || [];
        const owner = group.created_by_display_name || group.created_by_username || "-";
        const memberTags = members.slice(0, 8).map((member) => `
          <span class="tag ${member.role === "owner" || member.role === "manager" ? "good" : ""}">
            ${escapeHtml(member.display_name || member.username || member.user_id)} ${escapeHtml(member.role || "member")}
          </span>
        `).join("");
        const overflow = members.length > 8 ? `<span class="tag blue">+${number(members.length - 8)}</span>` : "";
        return `
          <div class="admin-row">
            <div class="admin-row-head">
              <strong>${escapeHtml(group.name || "Untitled group")}</strong>
              <span class="tag ${group.hide_digital ? "blue" : "good"}">${group.hide_digital ? "digital hidden" : "digital visible"}</span>
            </div>
            <div class="muted">${escapeHtml(group.public_id || group.id)} &middot; owner ${escapeHtml(owner)} &middot; ${number(group.member_count)} members &middot; ${number(group.movie_count)} movies &middot; ${number(group.pending_invite_count)} pending invites</div>
            <div class="admin-permission-cloud">${memberTags || `<span class="tag">No members</span>`}${overflow}</div>
          </div>
        `;
      }).join("") : `<div class="empty">No media groups found.</div>`;
    }
    function permissionTags(permissions, limit) {
      const values = permissions || [];
      const shown = values.slice(0, limit || 8).map((permission) => `<span class="tag">${escapeHtml(permission)}</span>`);
      if (values.length > shown.length) {
        shown.push(`<span class="tag blue">+${number(values.length - shown.length)}</span>`);
      }
      return shown.join("") || `<span class="tag">No permissions</span>`;
    }
    function renderAdminRbac(rbac) {
      const mode = rbac.mode || "basic";
      const roles = rbac.roles || [];
      const permissions = rbac.permissions || [];
      const customRoles = roles.filter((role) => role.custom).length;
      const basicButton = document.getElementById("adminRbacBasicButton");
      const advancedButton = document.getElementById("adminRbacAdvancedButton");
      if (basicButton) {
        basicButton.classList.toggle("active", mode === "basic");
        basicButton.disabled = !rbac.canSwitchMode || mode === "basic";
      }
      if (advancedButton) {
        advancedButton.classList.toggle("active", mode === "advanced");
        advancedButton.disabled = !rbac.canSwitchMode || mode === "advanced" || !rbac.advancedEnabled;
      }
      setAdminText(
        "adminRbacModeState",
        `${mode} mode. ${number((rbac.assignableRoles || []).length)} assignable roles, ${number(customRoles)} custom roles.`
      );
      setAdminText("adminPermissionSummary", `${number(permissions.length)} permissions across ${number(new Set(permissions.map((item) => item.domain || "core")).size)} domains.`);
      const preview = document.getElementById("adminPermissionPreview");
      if (preview) {
        preview.innerHTML = permissions.slice(0, 14).map((permission) => `<span class="tag">${escapeHtml(permission.key)}</span>`).join("")
          || `<span class="tag">No permissions</span>`;
      }
      const list = document.getElementById("adminRolesList");
      if (!list) return;
      list.innerHTML = roles.length ? roles.map((role) => `
        <div class="admin-row">
          <div class="admin-row-head">
            <strong>${escapeHtml(role.name || role.key)}</strong>
            <span class="tag ${role.assignable ? "good" : ""}">${role.assignable ? "assignable" : "protected"}</span>
          </div>
          <div class="muted">${escapeHtml(role.key)} &middot; ${role.system ? "system" : "custom"} &middot; ${number((role.permissions || []).length)} permissions</div>
          <div class="admin-permission-cloud">${permissionTags(role.permissions || [], 10)}</div>
        </div>
      `).join("") : `<div class="empty">No roles found.</div>`;
    }
    function pluginCategoryLabel(plugin) {
      return (plugin.categories || []).map((category) => category.replaceAll("_", " ")).join(", ") || "plugin";
    }
    function pluginSchemaItems(plugin, kind) {
      const schema = plugin.settingsSchema || {};
      const values = schema[kind];
      if (Array.isArray(values)) return values;
      if (values && typeof values === "object") {
        return Object.entries(values).map(([name, value]) => ({name, ...(value || {})}));
      }
      return [];
    }
    function pluginInputType(field) {
      if (field.type === "password") return "password";
      if (field.type === "url") return "url";
      if (field.type === "number") return "number";
      return "text";
    }
    function pluginHasCategory(plugin, category) {
      return (plugin.categories || []).includes(category);
    }
    function pluginConfigStatus(plugin, config) {
      const secretNames = (config && config.secretNames) || [];
      const settingKeys = Object.keys((config && config.settings) || {});
      return [
        plugin.requiresSecrets ? `${number(secretNames.length)} secret${secretNames.length === 1 ? "" : "s"}` : "no secrets",
        `${number(settingKeys.length)} setting${settingKeys.length === 1 ? "" : "s"}`
      ].join(" / ");
    }
    function pluginHealthDetails(pluginId) {
      const health = adminState.pluginHealth[pluginId] || {};
      if (!Object.keys(health).length) return "";
      const runtime = health.runtime || {};
      const nested = runtime.health || {};
      const message = health.error || runtime.error || nested.message || nested.error || "";
      return `
        <div class="auth-status ${metadataStateClass(health.state)}">
          Health: ${escapeHtml((health.state || "unknown").replaceAll("_", " "))}${message ? ` - ${escapeHtml(message)}` : ""}
        </div>
      `;
    }
    function renderPluginConfig(plugin, config) {
      const settings = pluginSchemaItems(plugin, "settings");
      const secrets = pluginSchemaItems(plugin, "secrets");
      if (!settings.length && !secrets.length) {
        return `<div class="auth-status info">No configuration required.</div>`;
      }
      const currentSettings = (config && config.settings) || {};
      const secretNames = new Set((config && config.secretNames) || []);
      const settingFields = settings.map((field) => {
        const name = field.name || field.key;
        const value = currentSettings[name];
        const display = Array.isArray(value) ? value.join(", ") : (value || "");
        return `
          <label>
            <span>${escapeHtml(field.label || name)} ${field.required ? `<span class="tag blue">required</span>` : ""}</span>
            <input data-plugin-setting="${escapeHtml(name)}" data-value-type="${escapeHtml(field.type || "text")}" type="${escapeHtml(pluginInputType(field))}" value="${escapeHtml(display)}" placeholder="${escapeHtml(field.placeholder || "")}" ${field.required ? "required" : ""}>
            ${field.description ? `<small>${escapeHtml(field.description)}</small>` : ""}
          </label>
        `;
      }).join("");
      const secretFields = secrets.map((field) => {
        const name = field.name || field.key;
        const configured = secretNames.has(name);
        return `
          <label>
            <span>${escapeHtml(field.label || name)} ${field.required ? `<span class="tag blue">required</span>` : ""} ${configured ? `<span class="tag good">configured</span>` : `<span class="tag">missing</span>`}</span>
            <input data-plugin-secret="${escapeHtml(name)}" type="password" value="" placeholder="${configured ? "Configured - leave blank to keep" : ""}" ${field.required && !configured ? "required" : ""}>
            ${field.description ? `<small>${escapeHtml(field.description)}</small>` : ""}
          </label>
        `;
      }).join("");
      return `
        <div class="admin-plugin-config-head">
          <strong>Configuration</strong>
          <span class="tag ${plugin.requiresSecrets && !secretNames.size ? "blue" : "good"}">${escapeHtml(pluginConfigStatus(plugin, config))}</span>
        </div>
        <div class="admin-plugin-config">
          ${settingFields}
          ${secretFields}
        </div>
        <div class="admin-controls">
          <button type="button" data-admin-plugin-save="${escapeHtml(plugin.id)}">Save Config</button>
        </div>
      `;
    }
    function renderPluginCard(plugin) {
        const health = adminState.pluginHealth[plugin.id] || {};
        const config = adminState.pluginConfigs[plugin.id] || {};
        const execution = adminState.pluginExecutions[plugin.id] || null;
        const latestJob = (adminState.pluginJobs || []).find((job) => (job.payload || {}).pluginId === plugin.id);
        const digitalSource = (adminState.digitalSources || []).find((source) => source.plugin_id === plugin.id);
        const runtime = plugin.runtime || {};
        const capabilities = plugin.capabilities || [];
        const runtimeState = health.state || (runtime.loaded ? "loaded" : "not loaded");
        const needsConfig = plugin.requiresSecrets && !plugin.secretsConfigured;
        return `
          <div class="admin-row">
            <div class="admin-plugin-head">
              <div>
                <strong>${escapeHtml(pluginDisplayName(plugin))}</strong>
                <div class="muted">${escapeHtml(tNext("appAdmin.pluginKey", "Plugin key"))}: ${escapeHtml(plugin.id)} &middot; ${escapeHtml(pluginCategoryLabel(plugin))} &middot; order ${escapeHtml(plugin.orderIndex || "-")}</div>
              </div>
              <span class="tag ${plugin.enabled ? "good" : ""}">${plugin.enabled ? "enabled" : "disabled"}</span>
            </div>
            <div class="admin-plugin-meta">
              <span class="tag ${runtime.loaded ? "good" : ""}">runtime ${escapeHtml(runtimeState)}</span>
              ${plugin.requiresSecrets ? `<span class="tag ${plugin.secretsConfigured ? "good" : ""}">secrets ${plugin.secretsConfigured ? "set" : "missing"}</span>` : `<span class="tag good">no secrets</span>`}
              ${plugin.settingsConfigured ? `<span class="tag good">settings set</span>` : `<span class="tag">default settings</span>`}
              ${pluginHasCategory(plugin, "metadata_source") ? `<span class="tag blue">metadata source</span>` : ""}
              ${pluginHasCategory(plugin, "metadata_receiver") ? `<span class="tag blue">metadata receiver</span>` : ""}
              ${pluginHasCategory(plugin, "digital_media_source") ? `<span class="tag blue">digital source</span>` : ""}
              ${pluginHasCategory(plugin, "import_source") ? `<span class="tag blue">import source</span>` : ""}
              ${digitalSource ? `<span class="tag good">${number(digitalSource.itemCount || digitalSource.item_count)} digital items</span>` : ""}
              ${digitalSource && digitalSource.last_status ? `<span class="tag">${escapeHtml(digitalSource.last_status)}</span>` : ""}
              ${latestJob ? `<span class="tag ${jobStatusClass(latestJob.status)}">job ${escapeHtml(latestJob.status || "-")}</span>` : ""}
              ${execution ? `<span class="tag blue">${escapeHtml(execution.entrypoint || "execution")} ${escapeHtml(execution.state || execution.status || "-")}</span>` : ""}
              ${needsConfig ? `<span class="tag blue">configuration needed</span>` : ""}
              ${plugin.premiumFeatureKey ? `<span class="tag blue">${escapeHtml(plugin.premiumFeatureKey)}</span>` : ""}
            </div>
            <div class="admin-controls">
              <button type="button" data-admin-plugin-enable="${escapeHtml(plugin.id)}" data-enabled="${plugin.enabled ? "false" : "true"}">${plugin.enabled ? "Disable" : "Enable"}</button>
              <button type="button" class="danger" data-admin-plugin-delete="${escapeHtml(plugin.id)}">Delete</button>
              <button type="button" data-admin-plugin-health="${escapeHtml(plugin.id)}">Check Health</button>
              ${capabilities.includes("discover_library") ? `<button type="button" data-admin-plugin-execute="${escapeHtml(plugin.id)}" data-entrypoint="discover_library">Discover</button>` : ""}
              ${capabilities.includes("inspect_source") ? `<button type="button" data-admin-plugin-execute="${escapeHtml(plugin.id)}" data-entrypoint="inspect_source">Inspect Source</button>` : ""}
              ${capabilities.includes("sync_library") ? `<button type="button" data-admin-plugin-job="${escapeHtml(plugin.id)}" data-entrypoint="sync_library">Queue Sync</button>` : ""}
            </div>
            ${renderPluginConfig(plugin, config)}
            ${pluginHealthDetails(plugin.id)}
          </div>
        `;
    }
    function renderPluginSection(title, subtitle, plugins) {
      if (!plugins.length) return "";
      return `
        <div class="admin-plugin-section">
          <div class="admin-plugin-section-head">
            <h4>${escapeHtml(title)}</h4>
            <span class="muted">${number(plugins.length)} plugin${plugins.length === 1 ? "" : "s"}</span>
          </div>
          ${subtitle ? `<p class="muted">${escapeHtml(subtitle)}</p>` : ""}
          ${plugins.map(renderPluginCard).join("")}
        </div>
      `;
    }
    function renderAdminPlugins(plugins) {
      const list = document.getElementById("adminPluginsList");
      if (!list) return;
      if (!plugins.length) {
        list.innerHTML = `<div class="empty">${escapeHtml(tNext("appAdmin.noPluginsFound", "No plugins found."))}</div>`;
        return;
      }
      const metadataPlugins = plugins.filter((plugin) => pluginHasCategory(plugin, "metadata_source") || pluginHasCategory(plugin, "metadata_receiver"));
      const digitalPlugins = plugins.filter((plugin) => pluginHasCategory(plugin, "digital_media_source"));
      const importPlugins = plugins.filter((plugin) => pluginHasCategory(plugin, "import_source"));
      const shown = new Set([...metadataPlugins, ...digitalPlugins, ...importPlugins].map((plugin) => plugin.id));
      const otherPlugins = plugins.filter((plugin) => !shown.has(plugin.id));
      list.innerHTML = [
        renderPluginSection("Metadata Plugins", "", metadataPlugins),
        renderPluginSection("Digital Media Sources", "", digitalPlugins),
        renderPluginSection("Import Sources", "Sources that can inspect or import collection data into DiscVault Next.", importPlugins),
        renderPluginSection("Other Plugins", "", otherPlugins)
      ].filter(Boolean).join("");
    }
    function jobStatusClass(status) {
      if (status === "completed") return "good";
      if (status === "pending" || status === "running") return "blue";
      if (status === "failed") return "bad";
      return "";
    }
    function pluginJobSummary(job) {
      const result = job.result || {};
      const execution = result.execution || {};
      const persistence = result.persistence || {};
      const pluginId = (job.payload || {}).pluginId || result.pluginId || "-";
      const entrypoint = (job.payload || {}).entrypoint || result.entrypoint || job.jobType || "-";
      const bits = [
        `${escapeHtml(pluginDisplayName(pluginId, pluginId))} / ${escapeHtml(entrypoint)}`,
        persistence.items != null ? `${number(persistence.items)} items` : "",
        persistence.matched != null ? `${number(persistence.matched)} matched` : "",
        execution.elapsedMs != null ? `${number(execution.elapsedMs)} ms` : ""
      ].filter(Boolean);
      return bits.join(" &middot; ");
    }
    function renderAdminPluginJobs() {
      const node = document.getElementById("adminPluginJobsList");
      if (!node) return;
      const jobs = adminState.pluginJobs || [];
      node.innerHTML = jobs.length ? jobs.map((job) => `
        <div class="admin-row">
          <div class="admin-row-head">
            <strong>${escapeHtml(job.jobType || "plugin job")}</strong>
            <span class="tag ${jobStatusClass(job.status)}">${escapeHtml(job.status || "-")}</span>
          </div>
          <div class="muted">${pluginJobSummary(job)}</div>
          <div class="muted">created ${escapeHtml((job.createdAt || "").slice(0, 19))} &middot; finished ${escapeHtml((job.finishedAt || "-").slice(0, 19))}</div>
          ${job.error ? `<div class="auth-status bad">${escapeHtml(job.error)}</div>` : ""}
        </div>
      `).join("") : `<div class="empty">No plugin execution jobs yet.</div>`;
    }
    function metadataStateClass(state) {
      if (["applied", "available", "hit", "completed", "ok"].includes(state)) return "good";
      if (["pending", "running", "needs_configuration", "blocked_by_format_policy", "retained_existing"].includes(state)) return "blue";
      if (["error", "failed"].includes(state)) return "bad";
      return "";
    }
    function metadataValuePreview(value) {
      let text = "";
      if (value === null || value === undefined || value === "") return "-";
      if (Array.isArray(value)) {
        text = value.join(", ");
      } else if (typeof value === "object") {
        text = JSON.stringify(value);
      } else {
        text = String(value);
      }
      return text.length > 96 ? `${text.slice(0, 93)}...` : text;
    }
    function metadataBucketTags(bucket) {
      const entries = Object.entries(bucket || {});
      return entries.length ? entries.map(([key, value]) =>
        `<span class="tag" title="${escapeHtml(metadataValuePreview(value))}">${escapeHtml(key)}: ${escapeHtml(metadataValuePreview(value))}</span>`
      ).join("") : `<span class="tag">No fields</span>`;
    }
    function metadataFieldDecisionRows(decisions) {
      const rows = Array.isArray(decisions) ? decisions : [];
      return rows.length ? rows.map((decision) => {
        const winner = decision.winner || {};
        const winnerLabel = winner.pluginId
          ? pluginDisplayName(winner.pluginId, winner.sourceLabel || winner.pluginId)
          : tNext("appAdmin.retainedExisting", "Retained existing");
        const rejected = (decision.candidates || [])
          .filter((candidate) => !candidate.winner)
          .map((candidate) => `${pluginDisplayName(candidate.pluginId, candidate.sourceLabel || candidate.pluginId)}: ${candidate.reason || "-"}`)
          .join(" | ");
        return `
          <div class="admin-row">
            <div class="admin-row-head">
              <strong>${escapeHtml(decision.target || "-")}.${escapeHtml(decision.field || "-")}</strong>
              <span class="tag ${winner.pluginId ? "good" : "blue"}">${escapeHtml(winnerLabel)}</span>
            </div>
            <div class="muted">${escapeHtml(winner.reason || decision.outcome || "-")}</div>
            <div class="admin-plugin-meta">
              <span class="tag" title="${escapeHtml(metadataValuePreview(decision.finalValue))}">${escapeHtml(tNext("appAdmin.finalValue", "Final value"))}: ${escapeHtml(metadataValuePreview(decision.finalValue))}</span>
              <span class="tag ${decision.conflict ? "blue" : ""}">${escapeHtml(formatNumber(decision.candidateCount || 0))} ${escapeHtml(tNext("appAdmin.candidates", "candidates"))}</span>
              ${decision.written ? `<span class="tag good">${escapeHtml(tNext("appAdmin.written", "Written"))}</span>` : ""}
            </div>
            ${rejected ? `<div class="muted">${escapeHtml(tNext("appAdmin.rejectedProviders", "Rejected providers"))}: ${escapeHtml(rejected)}</div>` : ""}
          </div>
        `;
      }).join("") : `<div class="empty">${escapeHtml(tNext("appAdmin.noFieldWinners", "No provider field winners yet."))}</div>`;
    }
    function metadataCurrentMovieId() {
      const input = document.getElementById("adminMetadataMovieId");
      const movieId = (input && input.value ? input.value : adminState.metadata.movieId || "").trim();
      if (!movieId) throw new Error("Movie ID is required.");
      adminState.metadata.movieId = movieId;
      return movieId;
    }
    function normalizeMetadataPayload(payload) {
      const metadata = (payload && payload.metadata) || payload || {};
      if (metadata.preview) {
        return {preview: metadata.preview, applied: metadata.applied || null, dryRun: !!metadata.dryRun};
      }
      if (metadata.proposal) {
        return {preview: metadata, applied: null, dryRun: true};
      }
      return {preview: null, applied: null, dryRun: false};
    }
    function rememberMetadataResult(payload, movieId) {
      const normalized = normalizeMetadataPayload(payload);
      adminState.metadata.lastPreview = normalized.preview;
      adminState.metadata.lastApplied = normalized.applied;
      adminState.metadata.movieId = movieId || adminState.metadata.movieId || "";
      renderAdminMetadata();
      return normalized;
    }
    function renderMetadataSources(preview) {
      const node = document.getElementById("adminMetadataSources");
      if (!node) return;
      const sources = (preview && preview.sourceSummary) || [];
      node.innerHTML = sources.length ? sources.map((source) => `
        <div class="admin-row">
          <div class="admin-row-head">
            <strong>${escapeHtml(pluginDisplayName(source.pluginId, source.name || source.pluginId))}</strong>
            <span class="tag ${metadataStateClass(source.state)}">${escapeHtml((source.state || "-").replaceAll("_", " "))}</span>
          </div>
          <div class="muted">${escapeHtml(source.reason || "-")}</div>
          <div class="admin-plugin-meta">
            <span class="tag">${number(source.acceptedFields || 0)} accepted</span>
            <span class="tag">${number(source.skippedFields || 0)} skipped</span>
            <span class="tag ${source.formatBlockedFields ? "blue" : ""}">${number(source.formatBlockedFields || 0)} format blocked</span>
            <span class="tag">${number(source.resultCount || 0)} results</span>
            <span class="tag">${number(source.elapsedMs || 0)} ms</span>
          </div>
        </div>
      `).join("") : `<div class="empty">Run a preview or refresh to see source decisions.</div>`;
    }
    function renderMetadataProposal(preview, applied) {
      const node = document.getElementById("adminMetadataProposal");
      if (!node) return;
      if (!preview) {
        node.innerHTML = `<div class="empty">${escapeHtml(tNext("appAdmin.noProposalYet", "No proposal yet."))}</div>`;
        return;
      }
      const proposal = preview.proposal || {};
      const stats = preview.proposalStats || {};
      const appliedState = applied || {};
      const appliedChanged = appliedState.changed === true;
      const appliedLabel = appliedState.changed === undefined ? "preview only" : appliedChanged ? "applied" : "no changes";
      node.innerHTML = `
        <div class="admin-row">
          <div class="admin-row-head">
            <strong>${escapeHtml(preview.movie && preview.movie.title ? preview.movie.title : "Metadata proposal")}</strong>
            <span class="tag ${appliedChanged ? "good" : "blue"}">${escapeHtml(appliedLabel)}</span>
          </div>
          <div class="admin-plugin-meta">
            <span class="tag good">${number(stats.acceptedFields || 0)} accepted</span>
            <span class="tag">${number(stats.updateFields || 0)} update fields</span>
            <span class="tag ${stats.skippedFields ? "blue" : ""}">${number(stats.skippedFields || 0)} skipped</span>
            <span class="tag ${stats.formatBlockedFields ? "blue" : ""}">${number(stats.formatBlockedFields || 0)} format blocked</span>
            ${appliedState.revision ? `<span class="tag good">revision ${escapeHtml(appliedState.revision)}</span>` : ""}
          </div>
        </div>
        <div class="admin-row">
          <strong>Movie Fields</strong>
          <div class="admin-permission-cloud">${metadataBucketTags(proposal.movieUpdates)}</div>
        </div>
        <div class="admin-row">
          <strong>Metadata Fields</strong>
          <div class="admin-permission-cloud">${metadataBucketTags(proposal.metadataUpdates)}</div>
        </div>
        <div class="admin-row">
          <strong>Technical Fields</strong>
          <div class="admin-permission-cloud">${metadataBucketTags(proposal.technicalUpdates)}</div>
        </div>
        <div class="admin-row">
          <strong>Identifiers</strong>
          <div class="admin-permission-cloud">${metadataBucketTags(proposal.identifiers)}</div>
        </div>
        <div class="admin-row">
          <strong>${escapeHtml(tNext("appAdmin.fieldWinners", "Field winners"))}</strong>
          <div class="admin-stack">${metadataFieldDecisionRows(proposal.fieldDecisions || [])}</div>
        </div>
      `;
    }
    function metadataJobSummary(job) {
      const payload = job.payload || {};
      const result = job.result || {};
      const run = result.result || {};
      const preview = run.preview || {};
      const stats = preview.proposalStats || {};
      const movieId = payload.movieId || result.movieId || "-";
      const dryRun = payload.dryRun !== undefined ? payload.dryRun : run.dryRun;
      const bits = [
        movieId,
        dryRun ? "dry run" : "apply",
        stats.updateFields != null ? `${number(stats.updateFields)} update fields` : "",
        stats.acceptedFields != null ? `${number(stats.acceptedFields)} accepted` : ""
      ].filter(Boolean);
      return bits.join(" &middot; ");
    }
    function renderAdminMetadataJobs() {
      const node = document.getElementById("adminMetadataJobs");
      if (!node) return;
      const jobs = adminState.metadata.jobs || [];
      node.innerHTML = jobs.length ? jobs.map((job) => `
        <div class="admin-row">
          <div class="admin-row-head">
            <strong>${escapeHtml(job.jobType || "metadata job")}</strong>
            <span class="tag ${jobStatusClass(job.status)}">${escapeHtml(job.status || "-")}</span>
          </div>
          <div class="muted">${metadataJobSummary(job)}</div>
          <div class="muted">created ${escapeHtml((job.createdAt || "").slice(0, 19))} &middot; finished ${escapeHtml((job.finishedAt || "-").slice(0, 19))}</div>
          ${job.error ? `<div class="auth-status bad">${escapeHtml(job.error)}</div>` : ""}
        </div>
      `).join("") : `<div class="empty">${escapeHtml(tNext("appAdmin.metadataNoJobsYet", "No metadata jobs yet."))}</div>`;
    }
    function renderAdminMetadata() {
      const input = document.getElementById("adminMetadataMovieId");
      if (input && adminState.metadata.movieId && input.value !== adminState.metadata.movieId) {
        input.value = adminState.metadata.movieId;
      }
      const preview = adminState.metadata.lastPreview;
      const applied = adminState.metadata.lastApplied;
      const state = document.getElementById("adminMetadataState");
      if (state) {
        if (preview) {
          const stats = preview.proposalStats || {};
          const title = preview.movie && preview.movie.title ? preview.movie.title : adminState.metadata.movieId || "-";
          const mode = applied && applied.changed !== undefined ? (applied.changed ? "applied" : "no changes") : "preview ready";
          state.textContent = `${title}: ${mode}; ${number(stats.updateFields || 0)} update fields, ${number(stats.skippedFields || 0)} skipped.`;
        } else {
          state.textContent = tNext("appAdmin.metadataNoRunYet", "No metadata refresh run yet.");
        }
      }
      renderMetadataSources(preview);
      renderMetadataProposal(preview, applied);
      renderAdminMetadataJobs();
    }
    async function useFirstMetadataMovie() {
      const payload = await authJson("/api/next/movies?limit=1", {headers: authHeaders()});
      const movie = (payload.items || [])[0];
      if (!movie || !movie.id) {
        throw new Error("No movie found.");
      }
      adminState.metadata.movieId = movie.id;
      const input = document.getElementById("adminMetadataMovieId");
      if (input) input.value = movie.id;
      setAdminStatus(`Selected ${movie.title || movie.id}.`, "good");
    }
    async function runAdminMetadata(mode) {
      const movieId = metadataCurrentMovieId();
      if (mode === "apply" && !confirm("Apply this metadata refresh to the selected movie?")) return;
      setAdminStatus(`Metadata ${mode} started...`, "info");
      const url = mode === "preview"
        ? `/api/next/movies/${encodeURIComponent(movieId)}/metadata/preview`
        : `/api/next/movies/${encodeURIComponent(movieId)}/metadata/refresh`;
      const body = mode === "preview" ? "{}" : JSON.stringify({dryRun: mode !== "apply"});
      const payload = await authJson(url, {method: "POST", body});
      const normalized = rememberMetadataResult(payload, movieId);
      const stats = normalized.preview ? normalized.preview.proposalStats || {} : {};
      setAdminStatus(`Metadata ${mode} complete: ${number(stats.updateFields || 0)} update fields.`, "good");
    }
    function upsertMetadataJob(job) {
      if (!job || !job.id) return;
      const rest = (adminState.metadata.jobs || []).filter((item) => item.id !== job.id);
      adminState.metadata.jobs = [job, ...rest].slice(0, 25);
      renderAdminMetadataJobs();
    }
    async function refreshAdminMetadataJobs() {
      const movieId = (document.getElementById("adminMetadataMovieId") || {}).value || adminState.metadata.movieId || "";
      const suffix = movieId.trim() ? `?movieId=${encodeURIComponent(movieId.trim())}&limit=25` : "?limit=25";
      const payload = await authJson(`/api/next/metadata/jobs${suffix}`, {headers: authHeaders()});
      adminState.metadata.jobs = payload.jobs || [];
      renderAdminMetadataJobs();
      setAdminStatus("Metadata jobs loaded.", "good");
    }
    async function pollMetadataJob(jobId) {
      for (let attempt = 0; attempt < 30; attempt += 1) {
        await new Promise((resolve) => setTimeout(resolve, attempt < 4 ? 1000 : 2000));
        const payload = await authJson(`/api/next/jobs/${encodeURIComponent(jobId)}`, {headers: authHeaders()});
        const job = payload.job || {};
        upsertMetadataJob(job);
        if (job.status === "completed") {
          const result = job.result && job.result.result ? job.result.result : null;
          if (result) rememberMetadataResult({metadata: result}, (job.payload || {}).movieId || job.result.movieId);
          setAdminStatus("Metadata job completed.", "good");
          return job;
        }
        if (job.status === "failed") {
          setAdminStatus(`Metadata job failed: ${job.error || "unknown error"}.`, "bad");
          return job;
        }
      }
      setAdminStatus("Metadata job is still running. Refresh jobs for the latest status.", "info");
    }
    async function queueAdminMetadata(dryRun) {
      const movieId = metadataCurrentMovieId();
      if (!dryRun && !confirm("Queue an applying metadata refresh for this movie?")) return;
      const payload = await authJson(`/api/next/movies/${encodeURIComponent(movieId)}/metadata/jobs`, {
        method: "POST",
        body: JSON.stringify({dryRun})
      });
      if (payload.job) upsertMetadataJob(payload.job);
      setAdminStatus(`Metadata ${dryRun ? "dry run" : "apply"} queued: ${payload.job ? payload.job.id : "-"}.`, "good");
      if (payload.job && payload.job.id) {
        pollMetadataJob(payload.job.id).catch((error) => setAdminStatus(error.message, "bad"));
      }
    }
    function backupCountsLine(counts) {
      const values = counts || {};
      return [
        `${number(values.movies)} movies`,
        `${number(values.containers)} containers`,
        `${number(values.mediaAssets || values.media_assets)} media assets`,
        `${number(values.collection_items)} collection items`,
        `${number(values.container_movies)} container movies`
      ].join(", ");
    }
    function renderBackupReport(report) {
      const node = document.getElementById("adminBackupReport");
      if (!node) return;
      if (!report) {
        node.innerHTML = `<div class="empty">${escapeHtml(tNext("appAdmin.noBackupReportYet", "No backup report yet."))}</div>`;
        return;
      }
      const tables = report.tables || {};
      const tableTags = Object.entries(tables).slice(0, 12).map(([name, item]) =>
        `<span class="tag">${escapeHtml(name)} ${number(item.count)}</span>`
      ).join("");
      const errors = (report.errors || []).map((item) => `<div class="auth-status bad">${escapeHtml(item)}</div>`).join("");
      const warnings = (report.warnings || []).map((item) => `<div class="auth-status info">${escapeHtml(item)}</div>`).join("");
      const media = report.media || {};
      node.innerHTML = `
        <div class="admin-row">
          <div class="admin-row-head">
            <strong>${report.valid ? "ZIP is valid" : "ZIP has issues"}</strong>
            <span class="tag ${report.valid ? "good" : ""}">${escapeHtml(report.scope || "unknown scope")}</span>
          </div>
          <div class="muted">Format ${escapeHtml(report.format || "-")} v${escapeHtml(report.formatVersion || "-")} &middot; created ${escapeHtml(report.createdAt || "-")}</div>
          <div class="admin-plugin-meta">
            <span class="tag ${media.missing ? "blue" : "good"}">${number(media.embedded)} embedded media</span>
            <span class="tag ${media.missing ? "blue" : "good"}">${number(media.missing)} missing media</span>
          </div>
          <div class="admin-permission-cloud">${tableTags || `<span class="tag">No table counts</span>`}</div>
          ${errors}
          ${warnings}
        </div>
      `;
    }
    function renderAdminBackups(backup) {
      const statusNode = document.getElementById("adminBackupState");
      if (statusNode) {
        statusNode.textContent = backup && backup.status === "ok"
          ? `${backup.scope || "functional_collection"} in ${backup.backupDir || "-"}; ${backupCountsLine(backup.counts || {})}.`
          : "No backup status loaded.";
      }
      renderBackupReport(adminState.backupReport);
      const jobsNode = document.getElementById("adminBackupJobs");
      if (!jobsNode) return;
      const jobs = (backup && backup.latestJobs) || [];
      jobsNode.innerHTML = jobs.length ? jobs.map((job) => `
        <div class="admin-row">
          <div class="admin-row-head">
            <strong>${escapeHtml(job.jobType || "backup job")}</strong>
            <span class="tag ${job.status === "completed" ? "good" : job.status === "failed" ? "" : "blue"}">${escapeHtml(job.status || "-")}</span>
          </div>
          <div class="muted">created ${escapeHtml((job.createdAt || "").slice(0, 19))} &middot; finished ${escapeHtml((job.finishedAt || "-").slice(0, 19))}</div>
          ${job.error ? `<div class="auth-status bad">${escapeHtml(job.error)}</div>` : ""}
        </div>
      `).join("") : `<div class="empty">No restore jobs yet.</div>`;
    }
    function renderAdminSecurity() {
      const stateLine = document.getElementById("adminSecurityState");
      const authButton = document.getElementById("adminAuthToggle");
      const registrationMode = authState.registration_enabled ? "public" : "invite";
      const movieVaultReceiverButton = document.getElementById("adminMovieVaultReceiverToggle");
      if (stateLine) {
        const receiverText = authState.role === "owner"
          ? ` MovieVault receiver ${ownerSettings.movievault_contribution_enabled ? "on" : "off"}.`
          : "";
        stateLine.textContent = `Auth ${authState.configured_auth_enabled ? "configured on" : "configured off"}; active ${authState.auth_enabled ? "yes" : "no"}; registration mode ${authState.registration_enabled ? "public registration" : "invite-only login"}.${receiverText}`;
      }
      if (authButton) authButton.textContent = authState.configured_auth_enabled ? "Disable Auth" : "Enable Auth";
      document.querySelectorAll("[data-admin-registration-mode]").forEach((button) => {
        const active = button.dataset.adminRegistrationMode === registrationMode;
        button.classList.toggle("active", active);
        button.setAttribute("aria-pressed", active ? "true" : "false");
      });
      if (movieVaultReceiverButton) {
        movieVaultReceiverButton.classList.toggle("hidden", authState.role !== "owner");
        movieVaultReceiverButton.textContent = ownerSettings.movievault_contribution_enabled
          ? "Disable MovieVault Receiver"
          : "Enable MovieVault Receiver";
      }
      const movieVaultContributionButton = document.getElementById("adminMovieVaultContributionToggle");
      if (movieVaultContributionButton) {
        movieVaultContributionButton.classList.toggle("hidden", authState.role !== "owner");
        movieVaultContributionButton.textContent = ownerSettings.movievault_v2_contribution_enabled
          ? "Disable Release Contribution"
          : "Enable Release Contribution";
      }
      const contributionState = ownerSettings.movievault_v2_contribution || {};
      const contributionLine = document.getElementById("adminMovieVaultContributionState");
      if (contributionLine) {
        contributionLine.classList.toggle("hidden", authState.role !== "owner");
        // The last error is worth showing even when sharing is on: a blocked
        // instance turns the gate off by itself, and the reason is the only
        // thing that explains why sends stopped.
        const parts = [`Release contribution ${ownerSettings.movievault_v2_contribution_enabled ? "on" : "off"}`];
        parts.push(contributionState.registered ? `registered as ${contributionState.instanceId || "unknown"}` : "not registered yet");
        if (contributionState.lastError) parts.push(`last error: ${contributionState.lastError}`);
        contributionLine.textContent = parts.join("; ") + ".";
      }
    }
    async function loadAdmin() {
      if (!isAdminUser()) return;
      setAdminStatus("Loading admin data...", "info");
      const [usersPayload, credentialsPayload, invitesPayload, mediaGroupsPayload, rbacPayload, pluginsPayload, digitalSourcesPayload, backupPayload, pluginJobsPayload, metadataJobsPayload, ownerSettingsPayload] = await Promise.all([
        authJson("/api/next/auth/users", {headers: authHeaders()}),
        authJson("/api/next/auth/credentials", {headers: authHeaders()}),
        authJson("/api/next/auth/invite", {headers: authHeaders()}),
        authJson("/api/next/media-groups?limit=500", {headers: authHeaders()}).catch(() => ({groups: []})),
        authJson("/api/next/auth/rbac", {headers: authHeaders()}),
        authJson("/api/next/plugins/registry", {headers: authHeaders()}),
        authJson("/api/next/digital-sources", {headers: authHeaders()}).catch(() => ({items: []})),
        authJson("/api/next/backup/status", {headers: authHeaders()}).catch((error) => ({status: "error", error: error.message})),
        authJson("/api/next/jobs?jobType=plugin.execute&limit=10", {headers: authHeaders()}).catch(() => ({jobs: []})),
        authJson("/api/next/metadata/jobs?limit=10", {headers: authHeaders()}).catch(() => ({jobs: []})),
        authState.role === "owner"
          ? authJson("/api/next/auth/owner/settings", {headers: authHeaders()})
          : Promise.resolve({settings: {}})
      ]);
      ownerSettings = ownerSettingsPayload.settings || {};
      adminState.users = usersPayload.users || [];
      adminState.credentials = credentialsPayload.credentials || [];
      adminState.invites = invitesPayload.invites || [];
      adminState.mediaGroups = mediaGroupsPayload.groups || [];
      adminState.rbac = rbacPayload || {};
      adminState.plugins = pluginsPayload.plugins || [];
      adminState.digitalSources = digitalSourcesPayload.items || [];
      adminState.backup = backupPayload || {};
      adminState.pluginJobs = pluginJobsPayload.jobs || [];
      adminState.metadata.jobs = metadataJobsPayload.jobs || [];
      const configPayloads = await Promise.all(adminState.plugins.map((plugin) =>
        authJson(`/api/next/plugins/${encodeURIComponent(plugin.id)}/config`, {headers: authHeaders()})
          .catch((error) => ({plugin, error: error.message, config: {}}))
      ));
      adminState.pluginConfigs = {};
      configPayloads.forEach((payload) => {
        if (payload.plugin && payload.plugin.id) {
          adminState.pluginConfigs[payload.plugin.id] = payload.config || {};
        }
      });
      renderAdminSecurity();
      renderAdminUsers(adminState.users, rbacPayload.assignableRoles || usersPayload.roles || []);
      renderAdminCredentials(adminState.credentials);
      renderAdminInvites(adminState.invites);
      renderAdminMediaGroups(adminState.mediaGroups);
      renderAdminRbac(adminState.rbac);
      renderAdminPlugins(adminState.plugins);
      renderAdminPluginJobs();
      renderAdminMetadata();
      renderAdminBackups(adminState.backup);
      renderAdminSummary();
      document.getElementById("adminPanel").dataset.loaded = "true";
      setAdminStatus("Admin data loaded.", "good");
    }
    async function setAuthConfigured(enabled) {
      await authJson("/api/next/auth/toggle", {
        method: "POST",
        body: JSON.stringify({enabled})
      });
      await refreshAuthStatus();
      await loadAdmin();
    }
    async function setInviteOnly(inviteOnly) {
      authState = await authJson("/api/next/auth/registration", {
        method: "POST",
        body: JSON.stringify({enabled: !inviteOnly})
      });
      renderAuthStatus();
      renderAdminSecurity();
      await loadAdmin();
      setAdminStatus(`Registration mode set to ${inviteOnly ? "invite-only login" : "public registration"}.`, "good");
    }
    async function setMovieVaultContribution(enabled) {
      if (authState.role !== "owner") {
        setAdminStatus("Only the owner can change release contribution.", "bad");
        return;
      }
      const payload = await authJson("/api/next/auth/owner/settings", {
        method: "POST",
        body: JSON.stringify({movievault_v2_contribution_enabled: enabled})
      });
      ownerSettings = payload.settings || {};
      renderAdminSecurity();
      setAdminStatus(`Release contribution ${enabled ? "enabled" : "disabled"}.`, "good");
    }
    async function setMovieVaultReceiver(enabled) {
      if (authState.role !== "owner") {
        setAdminStatus("Only the owner can change MovieVault receiver mode.", "bad");
        return;
      }
      const payload = await authJson("/api/next/auth/owner/settings", {
        method: "POST",
        body: JSON.stringify({movievault_contribution_enabled: enabled})
      });
      ownerSettings = payload.settings || {};
      renderAdminSecurity();
      setAdminStatus(`MovieVault receiver ${enabled ? "enabled" : "disabled"}.`, "good");
    }
    async function setRbacMode(mode) {
      await authJson("/api/next/auth/rbac", {
        method: "PATCH",
        body: JSON.stringify({mode})
      });
      await loadAdmin();
      setAdminStatus(`RBAC switched to ${mode}.`, "good");
    }
    async function setPluginEnabled(pluginId, enabled) {
      const payload = await authJson(`/api/next/plugins/${encodeURIComponent(pluginId)}`, {
        method: "PATCH",
        body: JSON.stringify({enabled})
      });
      adminState.plugins = (payload.registry && payload.registry.plugins) || adminState.plugins;
      renderAdminPlugins(adminState.plugins);
      renderAdminSummary();
      setAdminStatus(`${pluginDisplayName(pluginId, pluginId)} ${enabled ? "enabled" : "disabled"}.`, "good");
    }
    async function deleteAdminPlugin(pluginId) {
      if (!pluginId) return;
      if (!window.confirm(`Delete plugin ${pluginDisplayName(pluginId, pluginId)} from DiscVault?`)) return;
      const typed = window.prompt(`Type the plugin id to confirm deletion: ${pluginId}`, "");
      if (typed !== pluginId) {
        setAdminStatus("Plugin deletion cancelled.", "bad");
        return;
      }
      const payload = await authJson(`/api/next/plugins/${encodeURIComponent(pluginId)}`, {
        method: "DELETE",
        body: JSON.stringify({confirmPluginId: pluginId, confirmation: `DELETE ${pluginId}`})
      });
      adminState.plugins = (payload.registry && payload.registry.plugins) || adminState.plugins.filter((plugin) => plugin.id !== pluginId);
      delete adminState.pluginConfigs[pluginId];
      delete adminState.pluginHealth[pluginId];
      delete adminState.pluginExecutions[pluginId];
      renderAdminPlugins(adminState.plugins);
      renderAdminSummary();
      setAdminStatus(`${pluginDisplayName(pluginId, pluginId)} deleted.`, "good");
    }
    async function checkPluginHealth(pluginId) {
      const payload = await authJson(`/api/next/plugins/${encodeURIComponent(pluginId)}/health`, {
        headers: authHeaders()
      });
      adminState.pluginHealth[pluginId] = payload.health || {};
      renderAdminPlugins(adminState.plugins);
      setAdminStatus(`${pluginDisplayName(pluginId, pluginId)} health: ${(payload.health && payload.health.state) || "unknown"}.`, "info");
    }
    function coercePluginValue(value, type) {
      const trimmed = String(value || "").trim();
      if (type === "array") {
        return trimmed ? trimmed.split(",").map((item) => item.trim()).filter(Boolean) : [];
      }
      if (type === "number") {
        return trimmed ? Number(trimmed) : null;
      }
      if (type === "boolean") {
        return ["1", "true", "yes", "on"].includes(trimmed.toLowerCase());
      }
      return trimmed;
    }
    async function savePluginConfig(pluginId, row) {
      const missingRequired = Array.from(row.querySelectorAll("[required]")).filter((input) => !String(input.value || "").trim());
      if (missingRequired.length) {
        throw new Error("Fill the required plugin configuration fields first.");
      }
      const settings = {};
      const secrets = {};
      row.querySelectorAll("[data-plugin-setting]").forEach((input) => {
        settings[input.dataset.pluginSetting] = coercePluginValue(input.value, input.dataset.valueType || "text");
      });
      row.querySelectorAll("[data-plugin-secret]").forEach((input) => {
        if (input.value) secrets[input.dataset.pluginSecret] = input.value;
      });
      const payload = await authJson(`/api/next/plugins/${encodeURIComponent(pluginId)}/config`, {
        method: "PATCH",
        body: JSON.stringify({settings, secrets})
      });
      adminState.pluginConfigs[pluginId] = payload.config || {};
      if (payload.plugin) {
        adminState.plugins = adminState.plugins.map((plugin) => plugin.id === pluginId ? payload.plugin : plugin);
      }
      renderAdminPlugins(adminState.plugins);
      setAdminStatus(`${pluginDisplayName(pluginId, pluginId)} configuration saved.`, "good");
    }
    function summarizePluginExecution(pluginId, entrypoint, execution) {
      const result = (execution && execution.result) || {};
      const parts = [
        `${pluginDisplayName(pluginId, pluginId)} ${entrypoint}`,
        execution && execution.state ? execution.state : "",
        Array.isArray(result.libraries) ? `${number(result.libraries.length)} libraries` : "",
        Array.isArray(result.items) ? `${number(result.items.length)} items` : "",
        execution && execution.elapsedMs != null ? `${number(execution.elapsedMs)} ms` : ""
      ].filter(Boolean);
      return parts.join(" - ");
    }
    async function executePlugin(pluginId, entrypoint) {
      const payload = await authJson(`/api/next/plugins/${encodeURIComponent(pluginId)}/execute`, {
        method: "POST",
        body: JSON.stringify({entrypoint, payload: {}})
      });
      const execution = payload.execution || {};
      adminState.pluginExecutions[pluginId] = {entrypoint, ...(execution || {})};
      renderAdminPlugins(adminState.plugins);
      setAdminStatus(summarizePluginExecution(pluginId, entrypoint, execution), execution.status === "ok" ? "good" : "bad");
    }
    function upsertPluginJob(job) {
      if (!job || !job.id) return;
      const rest = (adminState.pluginJobs || []).filter((item) => item.id !== job.id);
      adminState.pluginJobs = [job, ...rest].slice(0, 10);
      renderAdminPluginJobs();
      renderAdminPlugins(adminState.plugins);
    }
    async function refreshDigitalSourcesForPlugins() {
      const payload = await authJson("/api/next/digital-sources", {headers: authHeaders()}).catch(() => ({items: []}));
      adminState.digitalSources = payload.items || [];
      renderAdminPlugins(adminState.plugins);
    }
    async function pollPluginJob(jobId, pluginId) {
      for (let attempt = 0; attempt < 30; attempt += 1) {
        await new Promise((resolve) => setTimeout(resolve, attempt < 4 ? 1000 : 2000));
        const payload = await authJson(`/api/next/jobs/${encodeURIComponent(jobId)}`, {headers: authHeaders()});
        const job = payload.job || {};
        upsertPluginJob(job);
        if (job.status === "completed") {
          await refreshDigitalSourcesForPlugins();
          const persistence = (job.result && job.result.persistence) || {};
          setAdminStatus(`${pluginDisplayName(pluginId, pluginId)} sync completed: ${number(persistence.items || 0)} items, ${number(persistence.matched || 0)} matched.`, "good");
          return job;
        }
        if (job.status === "failed") {
          setAdminStatus(`${pluginDisplayName(pluginId, pluginId)} sync failed: ${job.error || "unknown error"}.`, "bad");
          return job;
        }
      }
      setAdminStatus(`${pluginDisplayName(pluginId, pluginId)} sync is still running. Refresh Admin for the latest status.`, "info");
    }
    async function queuePluginExecution(pluginId, entrypoint) {
      const payload = await authJson(`/api/next/plugins/${encodeURIComponent(pluginId)}/jobs`, {
        method: "POST",
        body: JSON.stringify({entrypoint, payload: {}})
      });
      if (payload.job) upsertPluginJob(payload.job);
      setAdminStatus(`${pluginDisplayName(pluginId, pluginId)} ${entrypoint} queued: ${payload.job ? payload.job.id : "-"}.`, "good");
      if (payload.job && payload.job.id) {
        pollPluginJob(payload.job.id, pluginId).catch((error) => setAdminStatus(error.message, "bad"));
      }
    }
    async function loadBackupStatus() {
      adminState.backup = await authJson("/api/next/backup/status", {headers: authHeaders()});
      renderAdminBackups(adminState.backup);
      setAdminStatus("Backup status loaded.", "good");
    }
    function selectedBackupFile() {
      const input = document.getElementById("adminBackupFile");
      const file = input && input.files && input.files[0];
      if (!file) {
        throw new Error("Select a DiscVault backup ZIP first.");
      }
      return file;
    }
    async function uploadBackupZip(url) {
      const formData = new FormData();
      formData.append("file", selectedBackupFile());
      const response = await fetch(url, {
        method: "POST",
        cache: "no-store",
        credentials: "same-origin",
        headers: authHeaders(),
        body: formData
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok && !payload.report) {
        throw new Error(payload.error || `${url} failed with HTTP ${response.status}`);
      }
      return payload;
    }
    function backupDownloadName(response) {
      const disposition = response.headers.get("Content-Disposition") || "";
      const match = disposition.match(/filename\\*?=(?:UTF-8''|")?([^";]+)/i);
      if (match && match[1]) {
        return decodeURIComponent(match[1].replace(/"/g, ""));
      }
      return `discvault-next-functional-${new Date().toISOString().slice(0, 10)}.zip`;
    }
    async function exportBackupZip() {
      setAdminStatus("Creating functional collection backup...", "info");
      const response = await fetch("/api/next/backup/export", {
        method: "GET",
        cache: "no-store",
        credentials: "same-origin",
        headers: authHeaders()
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.error || `Backup export failed with HTTP ${response.status}`);
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = backupDownloadName(response);
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
      await loadBackupStatus();
      setAdminStatus("Functional collection backup downloaded.", "good");
    }
    async function validateBackupZip() {
      setAdminStatus("Validating backup ZIP...", "info");
      const payload = await uploadBackupZip("/api/next/backup/validate");
      adminState.backupReport = payload.report || null;
      renderAdminBackups(adminState.backup);
      const valid = !!(payload.report && payload.report.valid);
      setAdminStatus(valid ? "Backup ZIP is valid." : "Backup ZIP has validation issues.", valid ? "good" : "bad");
    }
    async function restoreBackupZip() {
      if (!confirm("Restore this ZIP and replace the functional collection data? Auth, passkeys, plugins and secrets are not restored.")) return;
      setAdminStatus("Validating and queueing restore job...", "info");
      const payload = await uploadBackupZip("/api/next/backup/restore?confirm=restore-functional-collection");
      adminState.backupReport = payload.report || null;
      await loadBackupStatus();
      setAdminStatus(`Restore queued: ${payload.job ? payload.job.id : "-"}.`, "good");
    }
    async function createAdminInvite() {
      const input = document.getElementById("adminInviteUsername");
      const username = input.value.trim();
      if (!username) {
        setAdminStatus("Username is required for an invite.", "bad");
        return;
      }
      const payload = await authJson("/api/next/auth/invite", {
        method: "POST",
        body: JSON.stringify({username})
      });
      const output = document.getElementById("adminInviteCodeOutput");
      output.classList.remove("hidden");
      output.textContent = `Invite for ${payload.username}: ${payload.code}`;
      input.value = "";
      await loadAdmin();
    }
    async function updateAdminUserRole(userId, role) {
      await authJson(`/api/next/auth/users/${encodeURIComponent(userId)}/role`, {
        method: "PUT",
        body: JSON.stringify({role})
      });
      await loadAdmin();
    }
    async function updateAdminUserStatus(userId, status) {
      await authJson(`/api/next/auth/users/${encodeURIComponent(userId)}`, {
        method: "PATCH",
        body: JSON.stringify({status})
      });
      await loadAdmin();
    }
    async function deleteAdminUser(userId) {
      if (!confirm("Delete this user and all passkeys?")) return;
      await authJson(`/api/next/auth/users/${encodeURIComponent(userId)}`, {method: "DELETE"});
      await loadAdmin();
    }
    async function transferOwnership(userId, username) {
      if (!confirm(`Transfer ownership to ${username || "this user"}? You will be changed to admin.`)) return;
      setAdminStatus("Waiting for owner passkey confirmation...", "info");
      const optionsPayload = await authJson("/api/next/auth/owner/transfer/options", {
        method: "POST",
        body: JSON.stringify({target_user_id: userId})
      });
      const options = optionsPayload.options;
      options.challenge = base64urlToBuffer(options.challenge);
      options.allowCredentials = (options.allowCredentials || []).map((credential) => ({
        ...credential,
        id: base64urlToBuffer(credential.id)
      }));
      const assertion = await navigator.credentials.get({publicKey: options});
      const credential = {
        id: assertion.id,
        rawId: bufferToBase64url(assertion.rawId),
        response: {
          authenticatorData: bufferToBase64url(assertion.response.authenticatorData),
          clientDataJSON: bufferToBase64url(assertion.response.clientDataJSON),
          signature: bufferToBase64url(assertion.response.signature),
          userHandle: assertion.response.userHandle ? bufferToBase64url(assertion.response.userHandle) : null
        },
        type: assertion.type,
        authenticatorAttachment: assertion.authenticatorAttachment
      };
      const payload = await authJson("/api/next/auth/owner/transfer/verify", {
        method: "POST",
        body: JSON.stringify({target_user_id: userId, credential})
      });
      authState = payload.auth || authState;
      renderAuthStatus();
      await loadAdmin();
      setAdminStatus("Ownership transferred.", "good");
    }
    async function deleteAdminCredential(credentialId) {
      if (!confirm("Delete this passkey?")) return;
      await authJson(`/api/next/auth/credentials/${encodeURIComponent(credentialId)}`, {method: "DELETE"});
      await loadAdmin();
    }
    async function deleteAdminInvite(inviteId) {
      await authJson(`/api/next/auth/invite/${encodeURIComponent(inviteId)}`, {method: "DELETE"});
      await loadAdmin();
    }
    function bindAdminActions() {
      const panel = document.getElementById("adminPanel");
      if (!panel || panel.dataset.bound === "true") return;
      panel.dataset.bound = "true";
      panel.addEventListener("click", (event) => {
        const target = event.target.closest("[data-admin-action], [data-admin-tab], [data-admin-rbac-mode], [data-admin-registration-mode], [data-admin-plugin-enable], [data-admin-plugin-delete], [data-admin-plugin-health], [data-admin-plugin-execute], [data-admin-plugin-job], [data-admin-plugin-save], [data-admin-user-status], [data-admin-user-delete], [data-admin-owner-transfer], [data-admin-credential-delete], [data-admin-invite-delete]");
        if (!target) return;
        event.preventDefault();
        const action = target.dataset.adminAction;
        let task = Promise.resolve();
        if (target.dataset.adminTab) {
          setAdminTab(target.dataset.adminTab);
        } else if (target.dataset.adminRbacMode) {
          task = setRbacMode(target.dataset.adminRbacMode);
        } else if (target.dataset.adminRegistrationMode) {
          task = setInviteOnly(target.dataset.adminRegistrationMode === "invite");
        } else if (target.dataset.adminPluginEnable) {
          task = setPluginEnabled(target.dataset.adminPluginEnable, target.dataset.enabled === "true");
        } else if (target.dataset.adminPluginDelete) {
          task = deleteAdminPlugin(target.dataset.adminPluginDelete);
        } else if (target.dataset.adminPluginHealth) {
          task = checkPluginHealth(target.dataset.adminPluginHealth);
        } else if (target.dataset.adminPluginExecute) {
          task = executePlugin(target.dataset.adminPluginExecute, target.dataset.entrypoint);
        } else if (target.dataset.adminPluginJob) {
          task = queuePluginExecution(target.dataset.adminPluginJob, target.dataset.entrypoint);
        } else if (target.dataset.adminPluginSave) {
          task = savePluginConfig(target.dataset.adminPluginSave, target.closest(".admin-row"));
        } else if (action === "refresh") {
          task = loadAdmin();
        } else if (action === "toggle-auth") {
          task = setAuthConfigured(!authState.configured_auth_enabled);
        } else if (action === "toggle-invite-only") {
          task = setInviteOnly(!!authState.registration_enabled);
        } else if (action === "toggle-movievault-receiver") {
          task = setMovieVaultReceiver(!ownerSettings.movievault_contribution_enabled);
        } else if (action === "toggle-movievault-contribution") {
          task = setMovieVaultContribution(!ownerSettings.movievault_v2_contribution_enabled);
        } else if (action === "create-invite") {
          task = createAdminInvite();
        } else if (action === "metadata-use-first") {
          task = useFirstMetadataMovie();
        } else if (action === "metadata-preview") {
          task = runAdminMetadata("preview");
        } else if (action === "metadata-dry-run") {
          task = runAdminMetadata("dry run");
        } else if (action === "metadata-apply") {
          task = runAdminMetadata("apply");
        } else if (action === "metadata-queue-dry-run") {
          task = queueAdminMetadata(true);
        } else if (action === "metadata-queue-apply") {
          task = queueAdminMetadata(false);
        } else if (action === "metadata-jobs-refresh") {
          task = refreshAdminMetadataJobs();
        } else if (action === "backup-refresh") {
          task = loadBackupStatus();
        } else if (action === "backup-export") {
          task = exportBackupZip();
        } else if (action === "backup-validate") {
          task = validateBackupZip();
        } else if (action === "backup-restore") {
          task = restoreBackupZip();
        } else if (target.dataset.adminUserStatus) {
          task = updateAdminUserStatus(target.dataset.adminUserStatus, target.dataset.status);
        } else if (target.dataset.adminUserDelete) {
          task = deleteAdminUser(target.dataset.adminUserDelete);
        } else if (target.dataset.adminOwnerTransfer) {
          task = transferOwnership(target.dataset.adminOwnerTransfer, target.dataset.username);
        } else if (target.dataset.adminCredentialDelete) {
          task = deleteAdminCredential(target.dataset.adminCredentialDelete);
        } else if (target.dataset.adminInviteDelete) {
          task = deleteAdminInvite(target.dataset.adminInviteDelete);
        }
        Promise.resolve(task).catch((error) => setAdminStatus(error.message, "bad"));
      });
      panel.addEventListener("change", (event) => {
        const target = event.target.closest("[data-admin-user-role]");
        if (!target) return;
        updateAdminUserRole(target.dataset.adminUserRole, target.value).catch((error) => setAdminStatus(error.message, "bad"));
      });
    }
    window.registerOwnerPasskey = registerOwnerPasskey;
    window.registerInvitedPasskey = registerInvitedPasskey;
    window.loginPasskey = loginPasskey;
    window.logoutPasskey = logoutPasskey;
    window.addEventListener("error", (event) => reportClientError(event.error || event.message));
    window.addEventListener("unhandledrejection", (event) => reportClientError(event.reason));
    function field(label, value) {
      return `<div class="field"><span>${escapeHtml(label)}</span><strong>${escapeHtml(valueOrDash(value))}</strong></div>`;
    }
    function fieldsFromObject(entries) {
      const rows = entries.filter(([, value]) => value !== null && value !== undefined && value !== "");
      return rows.length ? rows.map(([label, value]) => field(label, value)).join("") : field("None", "-");
    }
    function usableVideoUrl(value) {
      const text = String(value || "");
      return text.startsWith("http://") || text.startsWith("https://") ? text : "";
    }
    function youtubeEmbedUrl(value) {
      const text = usableVideoUrl(value);
      if (!text) return "";
      try {
        const url = new URL(text);
        const host = url.hostname.replace(/^www\\./, "").toLowerCase();
        let videoId = "";
        if (host === "youtu.be") {
          videoId = url.pathname.split("/").filter(Boolean)[0] || "";
        } else if (host === "youtube.com" || host === "m.youtube.com" || host === "music.youtube.com" || host === "youtube-nocookie.com") {
          if (url.pathname === "/watch") videoId = url.searchParams.get("v") || "";
          else {
            const parts = url.pathname.split("/").filter(Boolean);
            if (["embed", "shorts", "live", "v"].includes(parts[0])) videoId = parts[1] || "";
          }
        }
        if (!/^[A-Za-z0-9_-]{11}$/.test(videoId)) return "";
        return `https://www.youtube-nocookie.com/embed/${encodeURIComponent(videoId)}?rel=0`;
      } catch (_) {
        return "";
      }
    }
    function collectionMovieVideoItems(movie, metadata) {
      const videos = [];
      const seen = new Set();
      const addVideo = (item) => {
        const url = usableVideoUrl(item?.url || item?.video_url || item?.href || "");
        if (!url || seen.has(url)) return;
        seen.add(url);
        videos.push({
          label: item?.label || item?.name || item?.title || item?.type || tNext("movieDetail.video", "Video"),
          type: item?.type || "",
          source: item?.source || item?.provider || "",
          url
        });
      };
      addVideo({label: tNext("movieDetail.trailer", "Trailer"), type: "Trailer", source: "metadata", url: movie.trailer_url || metadata.trailer_url});
      (Array.isArray(metadata.videos) ? metadata.videos : []).forEach(addVideo);
      return videos;
    }
    function collectionVideoTypeLabel(video) {
      return String(video?.type || "").trim() || tNext("movieDetail.video", "Video");
    }
    function collectionVideoTypeSortValue(label) {
      const key = String(label || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
      const order = ["trailer", "teaser", "clip", "featurette", "behind the scenes", "interview", "bloopers"];
      const index = order.findIndex((item) => key === item || key.endsWith(` ${item}`) || key.includes(item));
      return index >= 0 ? index : order.length;
    }
    function collectionVideoCardHtml(video) {
      const title = video?.label || tNext("movieDetail.video", "Video");
      const meta = [video?.type, video?.source].filter(Boolean).join(" / ") || tNext("movieDetail.openVideo", "Open video");
      const url = usableVideoUrl(video?.url || "");
      const embedUrl = youtubeEmbedUrl(url);
      if (embedUrl) {
        return `
          <article class="collection-video-card embedded">
            <div class="collection-video-embed">
              <iframe src="${escapeHtml(embedUrl)}" title="${escapeHtml(title)}" loading="lazy" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" allowfullscreen referrerpolicy="strict-origin-when-cross-origin"></iframe>
            </div>
            <div class="collection-video-copy">
              <strong>${escapeHtml(title)}</strong>
              <span>${escapeHtml(meta)}</span>
              ${url ? `<a class="collection-video-link" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(tNext("movieDetail.openVideo", "Open video"))}</a>` : ""}
            </div>
          </article>
        `;
      }
      return `
        <a class="collection-video-card" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">
          <strong>${escapeHtml(title)}</strong>
          <span>${escapeHtml(meta)}</span>
        </a>
      `;
    }
    function collectionMovieVideoGroupsHtml(videos) {
      if (!videos.length) {
        return `<div class="empty small">${escapeHtml(tNext("movieDetail.noVideos", "No videos imported yet."))}</div>`;
      }
      const typeGroups = new Map();
      videos.forEach((video) => {
        const label = collectionVideoTypeLabel(video);
        const key = label.toLowerCase();
        if (!typeGroups.has(key)) {
          typeGroups.set(key, {label, sortValue: collectionVideoTypeSortValue(label), videos: []});
        }
        typeGroups.get(key).videos.push(video);
      });
      const orderedTypeGroups = [...typeGroups.values()].sort((a, b) => {
        const orderDelta = a.sortValue - b.sortValue;
        if (orderDelta) return orderDelta;
        return a.label.localeCompare(b.label, undefined, {sensitivity: "base"});
      });
      return orderedTypeGroups.map((group) => `
        <section class="collection-video-type-group">
          <div class="collection-video-group-head">
            <strong>${escapeHtml(group.label)}</strong>
            <span>${escapeHtml(number(group.videos.length))} ${escapeHtml(tNext("movieDetail.videos", "Videos"))}</span>
          </div>
          <div class="collection-video-grid">${group.videos.map(collectionVideoCardHtml).join("")}</div>
        </section>
      `).join("");
    }
    function movieMatches(movie, query) {
      if (!query) return true;
      const haystack = [
        movie.title,
        movie.original_title,
        movie.barcode,
        movie.format,
        movie.year,
        movie.edition
      ].filter(Boolean).join(" ").toLowerCase();
      return haystack.includes(query.toLowerCase());
    }
    function renderFormatFilters() {
      const formats = Array.from(new Set(state.movies.map((movie) => movie.format).filter(Boolean))).sort();
      const buttons = ["all", ...formats].map((format) => {
        const label = format === "all" ? tNext("common.allFormats", "All formats") : format;
        return `<button type="button" class="${state.activeFormat === format ? "active" : ""}" onclick="setFormat('${escapeHtml(format)}')">${escapeHtml(label)}</button>`;
      });
      document.getElementById("formatFilters").innerHTML = buttons.join("");
    }
    function setFormat(format) {
      state.activeFormat = format;
      renderFormatFilters();
      renderMovies();
    }
    function renderStats() {
      const counts = state.stats.counts || {};
      document.getElementById("movieCount").textContent = number(counts.movies);
      document.getElementById("peopleCount").textContent = number(counts.people);
      document.getElementById("assetCount").textContent = number(counts.mediaAssets);
      document.getElementById("pluginCount").textContent = number(state.plugins.filter((plugin) => plugin.enabled).length);
    }
    function renderMovies() {
      const query = document.getElementById("searchInput").value.trim();
      const filtered = state.movies.filter((movie) => {
        const formatOk = state.activeFormat === "all" || movie.format === state.activeFormat;
        return formatOk && movieMatches(movie, query);
      });
      document.getElementById("resultCount").textContent = `${number(filtered.length)} ${tNext("collection.shown", "shown")}`;
      document.getElementById("movieGrid").innerHTML = filtered.length ? filtered.map((movie) => {
        const poster = usableImage(movie.poster_url);
        const posterHtml = poster
          ? `<img src="${escapeHtml(poster)}" alt="">`
          : `<span>${escapeHtml(tNext("collection.noPoster", "No poster"))}</span>`;
        return `
          <a class="movie" href="/api/next/app/movies/${encodeURIComponent(movie.id)}" data-movie-id="${escapeHtml(movie.id)}">
            <div class="poster">${posterHtml}</div>
            <div class="movie-body">
              <div class="movie-title">${escapeHtml(movie.title || tNext("common.untitled", "Untitled"))}</div>
              <div class="tags">
                ${movie.year ? `<span class="tag good">${escapeHtml(movie.year)}</span>` : ""}
                ${movie.format ? `<span class="tag blue">${escapeHtml(movie.format)}</span>` : ""}
                ${movie.media_group_count ? `<span class="tag good">${escapeHtml(movie.media_group_count === 1 ? "Group" : `${movie.media_group_count} groups`)}</span>` : ""}
                ${movie.digital_count ? `<span class="tag good">${escapeHtml(movie.digital_count === 1 ? "Digital" : `${movie.digital_count} digital`)}</span>` : ""}
                ${movie.barcode ? `<span class="tag">${escapeHtml(movie.barcode)}</span>` : ""}
              </div>
            </div>
          </a>
        `;
      }).join("") : `<div class="empty">${escapeHtml(tNext("collection.emptyMovies", "No movies match the current filter."))}</div>`;
    }
    function renderContainers() {
      document.getElementById("containerCount").textContent = number(state.containers.length);
      document.getElementById("containerList").innerHTML = state.containers.length ? state.containers.map((container) => `
        <a class="container-card" href="/api/next/app/containers/${encodeURIComponent(container.id)}">
          <strong>${escapeHtml(container.title || tNext("common.untitled", "Untitled"))}</strong>
          <div class="tags">
            <span class="tag blue">${escapeHtml((container.container_type || "container").replaceAll("_", " "))}</span>
            ${container.year ? `<span class="tag good">${escapeHtml(container.year)}</span>` : ""}
            ${container.barcode ? `<span class="tag">${escapeHtml(container.barcode)}</span>` : ""}
          </div>
        </a>
      `).join("") : `<div class="empty">${escapeHtml(tNext("collection.emptyContainers", "No containers imported yet."))}</div>`;
    }
    function renderMovieDetail(detail) {
      const movie = detail.movie || {};
      const metadata = movie.metadata || {};
      const specs = detail.technicalSpecs || {};
      const poster = mediaAssetImage(detail.mediaAssets, "poster") || usableImage(metadata.poster_url || metadata.posterUrl || metadata.poster);
      const backdrop = mediaAssetImage(detail.mediaAssets, "backdrop") || usableImage(metadata.backdrop_url || metadata.backdropUrl || metadata.backdrop);
      document.getElementById("detailHero").style.backgroundImage = backdrop
        ? `linear-gradient(180deg, rgba(16,17,22,.18), var(--surface)), url("${cssUrl(backdrop)}")`
        : "";
      document.getElementById("detailPoster").innerHTML = poster
        ? `<img src="${escapeHtml(poster)}" alt="">`
        : "No poster";
      document.getElementById("detailTitle").textContent = movie.title || "Untitled";
      document.getElementById("detailOverview").textContent = movie.overview || "No overview imported yet.";
      document.getElementById("detailTags").innerHTML = [
        movie.year,
        movie.format,
        movie.runtime_minutes ? `${movie.runtime_minutes} min` : "",
        movie.rating ? `Rating ${movie.rating}` : "",
        (detail.mediaGroups || []).length ? `${(detail.mediaGroups || []).length} groups` : "",
        (detail.digitalItems || []).length ? `${(detail.digitalItems || []).length} digital` : ""
      ].filter(Boolean).map((item) => `<span class="tag good">${escapeHtml(item)}</span>`).join("");
      document.getElementById("detailRelease").innerHTML = fieldsFromObject([
        ["Original title", movie.original_title],
        ["Barcode", movie.barcode],
        ["Format", movie.format],
        ["Edition", movie.edition],
        ["Edition type", movie.edition_type],
        ["Release date", movie.release_date],
        ["Country", movie.country],
        ["Language", movie.language],
        ["Location", movie.location],
        ["Director", metadata.director],
        ["Producer", metadata.producer],
        ["Studios", metadata.studios],
        ["Genre", (movie.genres || []).map((key) => genreLabels[key] || key).join(", ")],
        ["Distributor", metadata.distributor],
        ["Trailer", metadata.trailer_url]
      ]);
      document.getElementById("detailIdentifiers").innerHTML = (detail.identifiers || []).length
        ? detail.identifiers.map((identifier) => field(`${identifier.provider_id} ${identifier.identifier_type}`, identifier.identifier)).join("")
        : field("None", "-");
      document.getElementById("detailSpecs").innerHTML = fieldsFromObject([
        ["HDR", specs.hdr || metadata.hdr],
        ["Packaging", specs.packaging || metadata.packaging],
        ["Case type", specs.carrier_type || metadata.carrier_type],
        ["Steelbook format", specs.steelbook_format || metadata.steelbook_format],
        ["Outer packaging", specs.outer_packaging || metadata.outer_packaging],
        ["Finish", specs.finishes || metadata.finishes],
        ["Screen ratio", specs.screen_ratios || metadata.screen_ratios],
        ["Audio", specs.audio_tracks || metadata.audio_tracks],
        ["Subtitles", specs.subtitles || metadata.subtitles],
        ["Regions", specs.regions || metadata.regions]
      ]);
      document.getElementById("detailContainers").innerHTML = (detail.containers || []).length
        ? detail.containers.map((container) => field(`${container.container_type} / ${container.relationship}`, container.title)).join("")
        : field("None", "-");
      document.getElementById("detailGroups").innerHTML = (detail.mediaGroups || []).length
        ? detail.mediaGroups.map((group) => field(
            group.name || "Media group",
            [
              group.member_count ? `${group.member_count} members` : "",
              group.movie_count ? `${group.movie_count} movies` : "",
              group.hide_digital ? "digital hidden" : "digital visible"
            ].filter(Boolean).join(" / ")
          )).join("")
        : field("None", "-");
      document.getElementById("detailDigital").innerHTML = (detail.digitalItems || []).length
        ? detail.digitalItems.map((item) => {
            const variantCount = Number(item.variant_count || item.variantCount || 0);
            return field(
              item.source_name || item.plugin_id || "Digital source",
              [item.title, item.year, variantCount > 1 ? `${variantCount} variants` : "", item.playback_url].filter(Boolean).join(" / ")
            );
          }).join("")
        : field("None", "-");
      document.getElementById("detailVideos").innerHTML = collectionMovieVideoGroupsHtml(collectionMovieVideoItems(movie, metadata));
      document.getElementById("detailCredits").innerHTML = (detail.credits || []).length
        ? detail.credits.map((credit) => `
          <div class="credit">
            <strong>${escapeHtml(credit.name)}</strong>
            <span>${escapeHtml(credit.character || credit.job || credit.credit_type || "credit")}</span>
          </div>
        `).join("")
        : `<div class="empty">${escapeHtml(tNext("movieDetail.noCreditsImported", "No credits imported for this movie."))}</div>`;
    }
    function renderMovieDetailError(error) {
      const message = error.message || String(error);
      document.getElementById("detailHero").style.backgroundImage = "";
      document.getElementById("detailPoster").textContent = tNext("movieDetail.errorLabel", "Error");
      document.getElementById("detailTitle").textContent = tNext("movieDetail.loadErrorTitle", "Could not load movie details");
      document.getElementById("detailTags").innerHTML = `<span class="tag">${escapeHtml(message)}</span>`;
      document.getElementById("detailOverview").textContent = tNext("movieDetail.loadErrorDescription", "The movie detail request failed. Check the Next API logs for the backend error.");
      document.getElementById("detailRelease").innerHTML = [
        field("Error", message),
        field("URL", error.url || "-"),
        field("Response", error.body || "-")
      ].join("");
      document.getElementById("detailIdentifiers").innerHTML = field("None", "-");
      document.getElementById("detailSpecs").innerHTML = field("None", "-");
      document.getElementById("detailContainers").innerHTML = field("None", "-");
      document.getElementById("detailGroups").innerHTML = field("None", "-");
      document.getElementById("detailDigital").innerHTML = field("None", "-");
      document.getElementById("detailVideos").innerHTML = `<div class="empty small">${escapeHtml(tNext("movieDetail.noVideos", "No videos imported yet."))}</div>`;
      document.getElementById("detailCredits").innerHTML = `<div class="empty">${escapeHtml(tNext("movieDetail.noCreditsLoaded", "No credits loaded."))}</div>`;
    }
    async function openMovieDetail(movieId) {
      const overlay = document.getElementById("movieDetailOverlay");
      overlay.classList.add("open");
      document.getElementById("detailTitle").textContent = tNext("movieDetail.loadingTitle", "Loading...");
      document.getElementById("detailOverview").textContent = "";
      document.getElementById("detailPoster").textContent = tNext("movieDetail.loadingPoster", "Loading");
      document.getElementById("detailTags").innerHTML = "";
      document.getElementById("detailGroups").innerHTML = "";
      document.getElementById("detailVideos").innerHTML = "";
      try {
        const payload = await json(`/api/next/movies/${encodeURIComponent(movieId)}`, 15000);
        renderMovieDetail(payload.detail || {});
      } catch (error) {
        renderMovieDetailError(error);
      }
    }
    function closeMovieDetail() {
      document.getElementById("movieDetailOverlay").classList.remove("open");
    }
    async function json(url, timeoutMs = 20000) {
      const controller = new AbortController();
      const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
      let response;
      try {
        response = await fetch(url, {cache: "no-store", credentials: "same-origin", headers: authHeaders(), signal: controller.signal});
      } catch (error) {
        if (error.name === "AbortError") {
          throw new Error(`${url} timed out after ${Math.round(timeoutMs / 1000)} seconds`);
        }
        throw error;
      } finally {
        window.clearTimeout(timeout);
      }
      if (!response.ok) {
        const body = await response.text();
        const error = new Error(`${url} failed with HTTP ${response.status}`);
        error.url = url;
        error.status = response.status;
        error.body = body.slice(0, 800);
        throw error;
      }
      return response.json();
    }
    async function loadCollection() {
      setClientStatus(tNext("collection.loadingCollection", "Loading collection data..."));
      document.getElementById("resultCount").textContent = tNext("collection.loading", "Loading...");
      const stats = await json("/api/next/stats");
      setClientStatus(tNext("collection.loadingMovies", "Stats loaded; loading movies..."));
      const movies = await json("/api/next/movies?limit=1000");
      setClientStatus(tNext("collection.loadingPlugins", "Movies loaded; loading containers..."));
      const containers = await json("/api/next/containers");
      setClientStatus(tNext("collection.loadingContainers", "Containers loaded; loading metadata plugins..."));
      const plugins = await json("/api/next/metadata/plugins");
      state.stats = stats || {};
      state.movies = movies.items || [];
      state.containers = containers.items || [];
      state.plugins = plugins.plugins || [];
      renderStats();
      renderFormatFilters();
      renderMovies();
      renderContainers();
      setClientStatus(`Loaded ${number(state.movies.length)} movies and ${number(state.containers.length)} containers.`);
    }
    function clearProtectedCollection(message) {
      state.movies = [];
      state.containers = [];
      state.plugins = [];
      state.stats = {counts: {}};
      renderStats();
      renderFormatFilters();
      renderMovies();
      renderContainers();
      document.getElementById("movieGrid").innerHTML = `<div class="empty">${escapeHtml(message)}</div>`;
      document.getElementById("resultCount").textContent = authState.authenticated
        ? tNext("collection.blocked", "Blocked")
        : tNext("collection.signInRequired", "Sign in required");
      setClientStatus(message);
    }
    async function resumeStartupOrCollection() {
      const startup = await refreshStartupStatus();
      if (!startup.canUseCollection) {
        clearProtectedCollection(startup.message || "DiscVault Next setup is not complete.");
        return;
      }
      await loadCollection();
    }
    async function bootCollection() {
      setClientStatus("Client script started.");
      bindThemeControls();
      await initNextI18n();
      bindAuthButtons();
      bindStartupActions();
      bindCollectionLinks();
      bindAdminActions();
      await refreshAuthStatus();
      if (authState.auth_enabled && !authState.authenticated) {
        clearProtectedCollection(tNext("collection.signInRequiredMessage", "Sign in with your passkey to load the collection."));
        return;
      }
      resumeStartupOrCollection().catch((error) => {
        if (error.status === 401) {
          clearProtectedCollection(tNext("collection.signInRequiredMessage", "Sign in with your passkey to load the collection."));
          return;
        }
        document.getElementById("movieGrid").innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
        document.getElementById("resultCount").textContent = tNext("common.error", "Error");
        setClientStatus(`Load failed: ${error.message}`);
      });
    }
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", bootCollection);
    } else {
      bootCollection();
    }
    window.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeMovieDetail();
    });
  </script>
</body>
</html>
"""
