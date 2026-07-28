/*
 * DiscVault - library export
 *
 * Adds the export flow to the library toolbar: a dialog to pick the file format
 * (CSV / XLSX / PDF) and the columns, and the download itself.
 *
 * The rows are extracted client-side through the `window.DiscVaultLibrary` bridge so the
 * export is a true WYSIWYG copy of the list view (same sorting, same filters, same
 * de-duplicated credits and aggregated tags). The backend only renders those rows into a
 * file; it does not re-derive any values.
 */
(function () {
  "use strict";

  var COLUMNS_ENDPOINT = "/api/next/collection/export/columns";
  var EXPORT_ENDPOINT = "/api/next/collection/export";
  var FORMAT_STORAGE_KEY = "dv_next_library_export_format_v1";
  var COLUMNS_STORAGE_KEY = "dv_next_library_export_columns_v1";
  var BRIDGE_POLL_MS = 100;
  var BRIDGE_POLL_ATTEMPTS = 100;

  var FORMAT_LABELS = {
    csv: "CSV",
    xlsx: "Excel (XLSX)",
    pdf: "PDF",
  };

  var state = {
    catalogue: null,
    overlay: null,
    busy: false,
    lastFocus: null,
  };

  function bridge() {
    return window.DiscVaultLibrary || null;
  }

  function translate(key, fallback) {
    var api = bridge();
    if (!api || typeof api.t !== "function") return fallback;
    try {
      return api.t(key, fallback) || fallback;
    } catch (error) {
      return fallback;
    }
  }

  function readStored(key) {
    try {
      return window.localStorage.getItem(key);
    } catch (error) {
      return null;
    }
  }

  function writeStored(key, value) {
    try {
      window.localStorage.setItem(key, value);
    } catch (error) {
      /* storage is optional */
    }
  }

  function requestHeaders(extra) {
    var headers = { Accept: "application/json" };
    var key;
    if (extra) {
      for (key in extra) {
        if (Object.prototype.hasOwnProperty.call(extra, key)) headers[key] = extra[key];
      }
    }
    var api = bridge();
    if (api && typeof api.authHeaders === "function") {
      try {
        var merged = api.authHeaders(headers);
        if (merged) return merged;
      } catch (error) {
        /* fall back to the default headers */
      }
    }
    return headers;
  }

  function loadCatalogue() {
    if (state.catalogue) return window.Promise.resolve(state.catalogue);
    return window
      .fetch(COLUMNS_ENDPOINT, { credentials: "same-origin", headers: requestHeaders() })
      .then(function (response) {
        if (!response.ok) throw new Error("export column request failed: " + response.status);
        return response.json();
      })
      .then(function (payload) {
        var columns = payload && Array.isArray(payload.columns) ? payload.columns : [];
        if (!columns.length) throw new Error("export column catalogue is empty");
        state.catalogue = columns;
        return columns;
      });
  }

  function selectedFormat() {
    var stored = readStored(FORMAT_STORAGE_KEY);
    if (stored === "csv" || stored === "xlsx" || stored === "pdf") return stored;
    return "csv";
  }

  function selectedColumns(catalogue) {
    var known = {};
    var defaults = [];
    catalogue.forEach(function (column) {
      known[column.key] = true;
      if (column.default !== false) defaults.push(column.key);
    });
    var raw = readStored(COLUMNS_STORAGE_KEY);
    if (!raw) return defaults;
    var parsed;
    try {
      parsed = JSON.parse(raw);
    } catch (error) {
      return defaults;
    }
    if (!Array.isArray(parsed)) return defaults;
    var picked = parsed.filter(function (key) {
      return known[key] === true;
    });
    return picked.length ? picked : defaults;
  }

  function exportRows() {
    var api = bridge();
    if (!api || typeof api.getExportRows !== "function") return [];
    try {
      var rows = api.getExportRows();
      return Array.isArray(rows) ? rows : [];
    } catch (error) {
      return [];
    }
  }

  function exportLabels() {
    var api = bridge();
    if (!api || typeof api.getExportColumnLabels !== "function") return {};
    try {
      return api.getExportColumnLabels() || {};
    } catch (error) {
      return {};
    }
  }

  function formatCount(template, count) {
    return String(template).replace("{count}", String(count));
  }

  function closeDialog() {
    if (!state.overlay) return;
    if (state.overlay.parentNode) state.overlay.parentNode.removeChild(state.overlay);
    state.overlay = null;
    state.busy = false;
    document.removeEventListener("keydown", onKeydown, true);
    if (state.lastFocus && typeof state.lastFocus.focus === "function") {
      try {
        state.lastFocus.focus();
      } catch (error) {
        /* the toolbar may have been re-rendered */
      }
    }
    state.lastFocus = null;
  }

  function onKeydown(event) {
    if (event.key !== "Escape" || state.busy) return;
    event.stopPropagation();
    closeDialog();
  }

  function triggerDownload(blob, filename) {
    var url = window.URL.createObjectURL(blob);
    var link = document.createElement("a");
    link.href = url;
    link.download = filename;
    link.rel = "noopener";
    link.style.display = "none";
    document.body.appendChild(link);
    link.click();
    window.setTimeout(function () {
      if (link.parentNode) link.parentNode.removeChild(link);
      window.URL.revokeObjectURL(url);
    }, 0);
  }

  function filenameFromDisposition(disposition, fallback) {
    if (!disposition) return fallback;
    var utf8 = /filename\*=UTF-8''([^;]+)/i.exec(disposition);
    if (utf8 && utf8[1]) {
      try {
        return window.decodeURIComponent(utf8[1]);
      } catch (error) {
        /* fall through to the plain filename */
      }
    }
    var plain = /filename="?([^";]+)"?/i.exec(disposition);
    if (plain && plain[1]) return plain[1];
    return fallback;
  }

  function runExport(options) {
    var rows = exportRows();
    if (!rows.length) {
      options.onError(translate("collection.exportEmpty", "There is nothing to export."));
      return;
    }
    var body = {
      format: options.format,
      columns: options.columns,
      rows: rows,
      labels: exportLabels(),
      title: translate("collection.exportDocumentTitle", "DiscVault library"),
      subtitle: formatCount(
        translate("collection.exportRowCount", "{count} movies"),
        rows.length
      ),
    };
    options.onStart();
    window
      .fetch(EXPORT_ENDPOINT, {
        method: "POST",
        credentials: "same-origin",
        headers: requestHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(body),
      })
      .then(function (response) {
        if (!response.ok) {
          return response
            .json()
            .catch(function () {
              return null;
            })
            .then(function (payload) {
              var message =
                payload && payload.error
                  ? payload.error
                  : translate("collection.exportFailed", "The export could not be created.");
              throw new Error(message);
            });
        }
        var disposition = response.headers.get("Content-Disposition");
        return response.blob().then(function (blob) {
          return { blob: blob, disposition: disposition };
        });
      })
      .then(function (result) {
        var fallback = "discvault-library." + options.format;
        triggerDownload(result.blob, filenameFromDisposition(result.disposition, fallback));
        options.onDone();
      })
      .catch(function (error) {
        options.onError(
          (error && error.message) ||
            translate("collection.exportFailed", "The export could not be created.")
        );
      });
  }

  function buildDialog(catalogue) {
    var overlay = document.createElement("div");
    overlay.className = "library-export-overlay";

    var dialog = document.createElement("div");
    dialog.className = "library-export-dialog";
    dialog.setAttribute("role", "dialog");
    dialog.setAttribute("aria-modal", "true");
    dialog.setAttribute("aria-labelledby", "libraryExportDialogTitle");

    var heading = document.createElement("h3");
    heading.id = "libraryExportDialogTitle";
    heading.textContent = translate("collection.exportTitle", "Export library");
    dialog.appendChild(heading);

    var rowCount = exportRows().length;
    var summary = document.createElement("p");
    summary.className = "library-export-summary";
    summary.textContent = formatCount(
      translate("collection.exportSummary", "{count} movies will be exported."),
      rowCount
    );
    dialog.appendChild(summary);

    var formatSet = document.createElement("fieldset");
    formatSet.className = "library-export-fieldset";
    var formatLegend = document.createElement("legend");
    formatLegend.className = "library-export-legend";
    formatLegend.textContent = translate("collection.exportFormat", "File format");
    formatSet.appendChild(formatLegend);
    var formatRow = document.createElement("div");
    formatRow.className = "library-export-formats";
    var activeFormat = selectedFormat();
    var formatInputs = [];
    ["csv", "xlsx", "pdf"].forEach(function (value) {
      var label = document.createElement("label");
      label.className = "library-export-format";
      label.setAttribute("data-selected", value === activeFormat ? "true" : "false");
      var input = document.createElement("input");
      input.type = "radio";
      input.name = "libraryExportFormat";
      input.value = value;
      input.checked = value === activeFormat;
      input.addEventListener("change", function () {
        formatInputs.forEach(function (entry) {
          entry.label.setAttribute("data-selected", entry.input.checked ? "true" : "false");
        });
      });
      var text = document.createElement("span");
      text.textContent = FORMAT_LABELS[value];
      label.appendChild(input);
      label.appendChild(text);
      formatRow.appendChild(label);
      formatInputs.push({ input: input, label: label });
    });
    formatSet.appendChild(formatRow);
    dialog.appendChild(formatSet);

    var columnSet = document.createElement("fieldset");
    columnSet.className = "library-export-fieldset";
    var columnLegend = document.createElement("legend");
    columnLegend.className = "library-export-legend";
    columnLegend.textContent = translate("collection.exportColumns", "Columns");
    columnSet.appendChild(columnLegend);
    var columnGrid = document.createElement("div");
    columnGrid.className = "library-export-columns";
    var labels = exportLabels();
    var active = selectedColumns(catalogue);
    var columnInputs = [];
    catalogue.forEach(function (column) {
      var label = document.createElement("label");
      label.className = "library-export-column";
      var input = document.createElement("input");
      input.type = "checkbox";
      input.value = column.key;
      input.checked = active.indexOf(column.key) !== -1;
      var text = document.createElement("span");
      text.textContent = labels[column.key] || column.label || column.key;
      label.appendChild(input);
      label.appendChild(text);
      columnGrid.appendChild(label);
      columnInputs.push(input);
    });
    columnSet.appendChild(columnGrid);
    dialog.appendChild(columnSet);

    var error = document.createElement("p");
    error.className = "library-export-error";
    error.hidden = true;
    error.setAttribute("role", "alert");
    dialog.appendChild(error);

    var actions = document.createElement("div");
    actions.className = "library-export-actions";
    var cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "secondary-button";
    cancel.textContent = translate("common.cancel", "Cancel");
    var submit = document.createElement("button");
    submit.type = "button";
    submit.className = "primary-button";
    submit.textContent = translate("collection.exportDownload", "Export");
    actions.appendChild(cancel);
    actions.appendChild(submit);
    dialog.appendChild(actions);

    function showError(message) {
      error.textContent = message;
      error.hidden = false;
    }

    function clearError() {
      error.textContent = "";
      error.hidden = true;
    }

    cancel.addEventListener("click", function () {
      if (state.busy) return;
      closeDialog();
    });

    overlay.addEventListener("mousedown", function (event) {
      if (event.target !== overlay || state.busy) return;
      closeDialog();
    });

    submit.addEventListener("click", function () {
      if (state.busy) return;
      clearError();
      var chosenFormat = "csv";
      formatInputs.forEach(function (entry) {
        if (entry.input.checked) chosenFormat = entry.input.value;
      });
      var chosenColumns = columnInputs
        .filter(function (input) {
          return input.checked;
        })
        .map(function (input) {
          return input.value;
        });
      if (!chosenColumns.length) {
        showError(translate("collection.exportNoColumns", "Select at least one column."));
        return;
      }
      writeStored(FORMAT_STORAGE_KEY, chosenFormat);
      writeStored(COLUMNS_STORAGE_KEY, JSON.stringify(chosenColumns));
      runExport({
        format: chosenFormat,
        columns: chosenColumns,
        onStart: function () {
          state.busy = true;
          submit.disabled = true;
          cancel.disabled = true;
          submit.classList.add("is-loading");
        },
        onDone: function () {
          closeDialog();
        },
        onError: function (message) {
          state.busy = false;
          submit.disabled = false;
          cancel.disabled = false;
          submit.classList.remove("is-loading");
          showError(message);
        },
      });
    });

    overlay.appendChild(dialog);
    return { overlay: overlay, focus: submit };
  }

  function openDialog(button) {
    if (state.overlay) return;
    button.disabled = true;
    loadCatalogue()
      .then(function (catalogue) {
        button.disabled = false;
        if (state.overlay) return;
        var built = buildDialog(catalogue);
        state.overlay = built.overlay;
        state.lastFocus = button;
        document.body.appendChild(built.overlay);
        document.addEventListener("keydown", onKeydown, true);
        if (built.focus && typeof built.focus.focus === "function") built.focus.focus();
      })
      .catch(function (error) {
        button.disabled = false;
        console.warn(
          translate("collection.exportFailed", "The export could not be created."),
          error
        );
      });
  }

  function attach() {
    var api = bridge();
    if (!api || typeof api.getExportRows !== "function") return false;
    var button = document.getElementById("libraryExportButton");
    if (!button) return false;
    if (button.getAttribute("data-export-bound") === "true") return true;
    button.setAttribute("data-export-bound", "true");
    button.classList.remove("hidden");
    button.addEventListener("click", function () {
      openDialog(button);
    });
    return true;
  }

  function waitForBridge(attempt) {
    if (attach()) return;
    if (attempt >= BRIDGE_POLL_ATTEMPTS) return;
    window.setTimeout(function () {
      waitForBridge(attempt + 1);
    }, BRIDGE_POLL_MS);
  }

  window.addEventListener("pagehide", function () {
    closeDialog();
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      waitForBridge(0);
    });
  } else {
    waitForBridge(0);
  }
})();
