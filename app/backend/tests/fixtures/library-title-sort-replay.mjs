// Execute the Library's name orderings out of next_views_ui.py, rather than
// reading them (App-Guidance library-ordering.md §1, §1a and §3).
//
// The title key and the three comparators that use it -- the grid's toolbar
// sort, the list view, and the series disc table -- are cut out of the inline
// SPA and run with the little environment they touch stubbed. Only what the
// orderings do not depend on is stubbed: the key itself is always the real one,
// because a stand-in key would test the stand-in.
//
// Usage: node library-title-sort-replay.mjs <next_views_ui.py> <scenario-json>
//
// Scenario ops:
//   skip    {names}                 -> each name without its leading article
//   keys    {rows, descending}      -> row names, ordered by titleSortKey
//   grid    {items, mode}           -> item titles, ordered by sortLibraryItems
//   list    {items, sort}           -> item titles, ordered by sortLibraryListItems
//   detail  {items, sort}           -> item titles, ordered by sortDetailItems
//   tables  {}                      -> the article table's languages, and the
//                                      languages declared to have none
// Every op takes `locale`, the language the app is displayed in.

import { readFileSync } from "node:fs";
import { functionSource, pythonStringText, titleSortKeySource } from "./title-sort-key.mjs";

const [, , sourcePath, payloadJson] = process.argv;
const source = readFileSync(sourcePath, "utf8");
const payload = JSON.parse(payloadJson);

const LIBRARY_FUNCTIONS = [
  "itemIsGroup",
  "itemEntity",
  "itemTitleValue",
  "itemTitleSortKey",
  "itemSortTitleValue",
  "compareItemTitles",
  "itemDateValue",
  "itemYearValue",
  "sortLibraryItems",
  "normalizeLibraryDetailSort",
  "libraryListSortValue",
  "sortLibraryListItems",
  "detailSortValue",
  "sortDetailItems",
];

const extracted = [
  titleSortKeySource(source),
  pythonStringText(LIBRARY_FUNCTIONS.map((name) => functionSource(source, name)).join("\n")),
].join("\n");

// The environment the extracted functions reach for. None of it decides a
// name ordering; each stub returns what an item with no such data would.
const environment = `
const tNext = (key, fallback) => fallback || key;
const itemMovieRows = () => [];
const libraryListCompactMode = () => false;
const customFieldDefinitions = () => [];
const itemFormatValues = () => [];
const itemFormatLabel = () => "";
const itemDirectorCredits = () => [];
const itemActorCredits = () => [];
const creditText = () => "";
const itemStudioValues = () => [];
const itemRatingValues = () => [];
const itemTagValues = () => [];
const itemYearLabel = (item) => String(item?.movie?.year || "");
`;

const evaluate = new Function(
  "localeState",
  "collectionSortMode",
  "libraryDetailSort",
  `${environment}\n${extracted}\nreturn {skipLeadingArticle, titleSortKey, compareTitleSortKeys, sortLibraryItems, sortLibraryListItems, sortDetailItems, itemTitleValue, LEADING_ARTICLES, LANGUAGES_WITHOUT_LEADING_ARTICLES};`,
);

const localeState = { locale: payload.locale || "en-US" };
const api = evaluate(localeState, payload.mode || "title_asc", payload.sort || { key: "title", direction: "asc" });
const titles = (items) => items.map((item) => api.itemTitleValue(item));

let result;
switch (payload.op) {
  case "skip":
    result = payload.names.map((name) => api.skipLeadingArticle(name));
    break;
  case "keys": {
    const sign = payload.descending ? -1 : 1;
    result = payload.rows
      .map((row) => ({ row, key: api.titleSortKey(row.name, row.sortTitle) }))
      .sort((left, right) => sign * api.compareTitleSortKeys(left.key, right.key))
      .map((entry) => entry.row.name);
    break;
  }
  case "grid":
    result = titles(api.sortLibraryItems(payload.items));
    break;
  case "list":
    result = titles(api.sortLibraryListItems(payload.items, payload.sort));
    break;
  case "detail":
    result = titles(api.sortDetailItems(payload.items, payload.sort));
    break;
  case "tables":
    result = {
      articles: Object.keys(api.LEADING_ARTICLES),
      none: api.LANGUAGES_WITHOUT_LEADING_ARTICLES,
    };
    break;
  default:
    throw new Error(`unknown op ${payload.op}`);
}
process.stdout.write(JSON.stringify(result));
