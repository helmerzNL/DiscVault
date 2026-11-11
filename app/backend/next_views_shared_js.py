"""JavaScript shared by more than one server-rendered page.

Each renderer -- the PWA shell (`next_views_ui`), the migration wizard
(`next_views_migration`) and the collection dashboard
(`next_views_collection`) -- returns a complete HTML page with its own inline
`<script>`. Nothing is shared between those scripts at runtime: a function
exists on a page only if that page's renderer emitted it.

They were one file once, and when the renderers were split apart their scripts
kept calling helpers by name. `pluginDisplayName` stayed in the shell while
the other two pages went on calling it, so `/api/next/migration` stopped at
"Loading report..." with `pluginDisplayName is not defined`, and the plugin
administration on `/api/next/collection` had the same hole.

A helper that more than one page calls therefore lives here, as text, and each
renderer splices it into its script the way it splices every other Python
value: close the string, add the constant, open the string again -- the same
shape as the `pwa_head_tags()` splice at the top of every page.

One definition rather than a copy per page, because this one carries a
hand-maintained table of plugin names. A stale copy of that does not throw; it
renders a plausible, slightly wrong name.

This module imports nothing, on purpose. The renderers import it, and
`tests/test_next_standalone_page_scripts.py` imports it without Flask to
rebuild each page's script and check that every function it calls is defined.

Indentation and escaping match the scripts these constants are spliced into:
four spaces, and a backslash doubled because this is an ordinary Python string.
A constant has no trailing newline; the line after the splice supplies it.
"""
from __future__ import annotations


#: `pluginDisplayName(pluginOrId, fallback = "")` -- the display name for a
#: plugin object or a bare plugin id.
#:
#: Every pool it searches sits behind a `typeof ... !== "undefined"` guard, so
#: it runs unchanged on a page that declares none of them: `appAdmin`, `state`
#: and `importCenter` belong to the shell, `adminState` to the collection
#: dashboard, and the migration wizard has no registry at all and resolves a
#: name from the object it was handed or from the table at the end.
PLUGIN_DISPLAY_NAME_JS = """    function pluginDisplayName(pluginOrId, fallback = "") {
      if (!pluginOrId && fallback) return fallback;
      const direct = pluginOrId && typeof pluginOrId === "object" ? pluginOrId : null;
      const pluginId = String(direct?.id || direct?.pluginId || direct?.plugin_id || pluginOrId || "").trim();
      const directName = String(direct?.displayName || direct?.pluginName || direct?.name || direct?.manifest?.name || "").trim();
      if (directName) return directName;
      const pools = [
        typeof appAdmin !== "undefined" ? appAdmin.plugins : [],
        typeof state !== "undefined" ? state.metadataPlugins : [],
        typeof state !== "undefined" ? state.plugins : [],
        typeof importCenter !== "undefined" ? importCenter.report?.metadataPlugins?.items : [],
        typeof importCenter !== "undefined" ? importCenter.report?.importSources : [],
        typeof importCenter !== "undefined" ? importCenter.preview?.sources : [],
        typeof adminState !== "undefined" ? adminState.plugins : []
      ].filter(Array.isArray);
      for (const pool of pools) {
        const match = pool.find((plugin) => {
          const id = String(plugin?.id || plugin?.pluginId || plugin?.plugin_id || "").trim();
          return id && id === pluginId;
        });
        const name = String(match?.displayName || match?.pluginName || match?.name || match?.manifest?.name || "").trim();
        if (name) return name;
      }
      // Names for plugin ids the registry cannot supply one for. `movievault`
      // and `movievault_26` are removed plugins and stay here on purpose:
      // identifiers and audit entries they wrote are still on existing movies,
      // and without an entry those rows would render a mangled id instead of the
      // source's name.
      const known = {
        movievault: "MovieVault",
        movievault_26: "MovieVault 26",
        tmdb: "TMDb",
        omdb: "OMDb",
        upcitemdb: "UPCItemDB",
        bluray_com: "Blu-ray.com",
        trakt: "Trakt",
        plex: "Plex",
        jellyfin: "Jellyfin",
        discvault_legacy_import: "DiscVault Legacy Import"
      };
      if (pluginId && known[pluginId]) return known[pluginId];
      if (fallback) return fallback;
      if (!pluginId) return "";
      return pluginId
        .replace(/[_-]+/g, " ")
        .replace(/\\b\\w/g, (char) => char.toUpperCase());
    }"""
