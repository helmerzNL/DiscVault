// The title sort key out of next_views_ui.py (App-Guidance library-ordering.md
// §1 and §1a), as source text a replay harness can evaluate.
//
// Every name ordering in the SPA goes through these helpers, so every harness
// that runs one of those orderings needs them too. Cutting them out in one
// place keeps a harness from carrying a hand-written stand-in for the key --
// a stand-in is exactly the second comparator the rule exists to prevent.

const DECLARATIONS = [
  "const LEADING_ARTICLES = ",
  "const LANGUAGES_WITHOUT_LEADING_ARTICLES = ",
  "let leadingArticleCache = ",
];

const FUNCTIONS = [
  "leadingArticlesFor",
  "normalizedLeadingWord",
  "skipLeadingArticle",
  "titleSortKey",
  "compareTitleSortKeys",
];

// The SPA is a non-raw Python string, so a JavaScript `\s` is spelled `\\s` in
// the file. Python collapses the pair when it builds the page; a harness that
// reads the file directly has to do the same, or every regex silently matches
// a literal backslash instead.
export function pythonStringText(text) {
  return text.replace(/\\\\/g, "\\");
}

// From the anchor to the `;` that closes the statement at bracket depth zero.
export function statementSource(source, anchor) {
  const start = source.indexOf(anchor);
  if (start < 0) throw new Error(`could not find ${anchor.trim()}`);
  let depth = 0;
  for (let index = start + anchor.length; index < source.length; index += 1) {
    const char = source[index];
    if (char === "{" || char === "[" || char === "(") depth += 1;
    else if (char === "}" || char === "]" || char === ")") depth -= 1;
    else if (char === ";" && depth === 0) return source.slice(start, index + 1);
  }
  throw new Error(`unterminated statement at ${anchor.trim()}`);
}

// The whole of `function name(...) { ... }`, by brace matching. The body's
// brace is looked for only after the parameter list closes: a default such as
// `options = {sensitivity: "base"}` opens a brace of its own first.
export function functionSource(source, name) {
  const start = source.indexOf(`function ${name}(`);
  if (start < 0) throw new Error(`could not find function ${name}`);
  let parens = 0;
  let paramsEnd = -1;
  for (let index = source.indexOf("(", start); index < source.length; index += 1) {
    if (source[index] === "(") parens += 1;
    else if (source[index] === ")") {
      parens -= 1;
      if (parens === 0) { paramsEnd = index; break; }
    }
  }
  if (paramsEnd < 0) throw new Error(`unbalanced parameter list reading ${name}`);
  let depth = 0;
  for (let index = source.indexOf("{", paramsEnd); index < source.length; index += 1) {
    const char = source[index];
    if (char === "{") depth += 1;
    else if (char === "}") {
      depth -= 1;
      if (depth === 0) return source.slice(start, index + 1);
    }
  }
  throw new Error(`unbalanced braces reading ${name}`);
}

// Expects `localeState` to be in scope where the result is evaluated.
export function titleSortKeySource(source) {
  return pythonStringText(
    [
      ...DECLARATIONS.map((anchor) => statementSource(source, anchor)),
      ...FUNCTIONS.map((name) => functionSource(source, name)),
    ].join("\n"),
  );
}
