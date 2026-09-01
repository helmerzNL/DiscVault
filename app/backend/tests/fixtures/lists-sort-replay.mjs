// Execute the real comparator out of next_views_ui.py, rather than reading it.
//
// The Lists page is inline JavaScript inside a Python string, so there is no
// module to import. This harness cuts the four functions that make up the sort
// out of that string and evaluates them with the small amount of environment
// they touch stubbed -- the same trade the library-hydration replay makes, and
// for the same reason: a source-text assertion pins the shape of a fix already
// understood and cannot notice a case nobody thought of.
//
// Usage: node lists-sort-replay.mjs <next_views_ui.py> <scenario-json>
// Prints the sorted titles as JSON.

import { readFileSync } from "node:fs";

const [, , sourcePath, payloadJson] = process.argv;
const source = readFileSync(sourcePath, "utf8");

const NEEDED = [
  "const LISTS_SORT_MODES",
  "function normalizeListsSortMode(",
  "function listsEntryDateValue(",
  "function listsEntrySortTitle(",
  "function listsEntryYearValue(",
  "function sortPersonalListEntries(",
];

// Take everything from the first declaration through the end of
// sortPersonalListEntries, so the extraction cannot silently miss a helper a
// later edit adds between them.
const start = source.indexOf(NEEDED[0]);
if (start < 0) throw new Error(`could not find ${NEEDED[0]} in ${sourcePath}`);
const endAnchor = source.indexOf("function renderListsSortMenu(");
if (endAnchor < 0) throw new Error("could not find the end anchor");
const slice = source.slice(start, endAnchor);
for (const name of NEEDED) {
  if (!slice.includes(name)) throw new Error(`extracted slice is missing ${name}`);
}

const payload = JSON.parse(payloadJson);

// The only outside references the extracted code makes.
const localeState = { locale: payload.locale || "en-US" };
let listsSortMode = payload.mode;

const evaluate = new Function(
  "localeState",
  "listsSortMode",
  `${slice}\nreturn sortPersonalListEntries;`,
);
const sortPersonalListEntries = evaluate(localeState, listsSortMode);

const sorted = sortPersonalListEntries(payload.entries, payload.kind);
process.stdout.write(JSON.stringify(sorted.map((entry) => entry.title)));
