"""A page's script may only call a function that page's script declares.

Three renderers each emit a self-contained HTML page with its own inline
`<script>`: the PWA shell (`next_views_ui.py`), the migration wizard
(`next_views_migration.py`) and the collection dashboard
(`next_views_collection.py`). They were one file once. When the renderers were
split out of `next_app`, their scripts kept calling helpers by name, and a
name is not something a move carries with it.

`pluginDisplayName` stayed behind in the shell. The migration wizard called it
from `renderWizard`, which runs on every report load, so `/api/next/migration`
never got past "Loading report...": the State card showed a red `error` badge
reading `pluginDisplayName is not defined`, and every count stayed at "-". The
collection dashboard called it fifteen times and had the same hole, plus a
`formatNumber` where that page's helper is called `number`.

Nothing was red. `py_compile` sees a string. `node --check` sees valid syntax,
because a call to an undeclared function *is* valid syntax. The duplicate-name
sweep in `test_next_views_ui_scope` counts declarations and has no opinion on
a name with none. `scripts/check_undefined_names.py` does exactly this job --
for Python, through `symtable`, which stops at the quote. And the page's own
`catch` turned the ReferenceError into a line of status text, so not even the
browser console said anything.

So the check has to read the emitted script and resolve its calls. It does
that without Flask, without a database and without node, deliberately: a test
that skips when a tool is missing reports the same as a test that passed.

The scope model is flat on purpose. Every declaration anywhere in the script
counts as a definition for every call anywhere in it, so a name is reported
only when *nothing* in the page could ever bind it. That misses a helper
declared inside one function and called from another; it cannot produce a
finding about a script that works.
"""

import ast
import os
import re
import sys
import unittest


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import next_views_shared_js  # noqa: E402  (dependency-free; needs no Flask)


#: The pages swept for unresolved calls: module file, renderer.
#:
#: The PWA shell is not in this list yet. Its script is 1.9 MB and loads two
#: further files by `src`, so sweeping it needs those resolved as well; it is
#: covered here only by the declared-once check at the bottom.
STANDALONE_PAGES = (
    ("next_views_migration.py", "migration_dashboard_html"),
    ("next_views_collection.py", "collection_dashboard_html"),
)

SHELL_PAGE = ("next_views_ui.py", "ui_preview_html")

#: Names the browser or the language supplies. Kept short and explicit: a name
#: added here is a name this test can no longer find missing.
BROWSER_GLOBALS = frozenset(
    """
    AbortController Array ArrayBuffer BigInt Blob Boolean CustomEvent Date
    DOMParser Error Event File FileReader FormData Headers Image
    IntersectionObserver Intl JSON Map Math MutationObserver Notification Number
    Object Promise Proxy RangeError RegExp Request ResizeObserver Response Set
    String Symbol TextDecoder TextEncoder TypeError URL URLSearchParams
    Uint8Array WeakMap WeakSet
    alert atob btoa cancelAnimationFrame clearInterval clearTimeout confirm
    decodeURI decodeURIComponent encodeURI encodeURIComponent fetch
    getComputedStyle isFinite isNaN matchMedia parseFloat parseInt prompt
    queueMicrotask requestAnimationFrame requestIdleCallback setInterval
    setTimeout structuredClone
    """.split()
)

JS_KEYWORDS = frozenset(
    """
    async await break case catch class const continue debugger default delete
    do else export extends false finally for function if import in instanceof
    let new null of return static super switch this throw true try typeof
    undefined var void while with yield
    """.split()
)

#: A `/` after one of these starts a regular expression, not a division.
REGEX_AFTER_KEYWORD = frozenset(
    "await case delete do else in instanceof new of return throw typeof void yield".split()
)

IDENTIFIER = re.compile(r"[A-Za-z_$][\w$]*")
NUMBER = re.compile(r"\d[\w.]*")
OPENERS = {"(": ")", "[": "]", "{": "}"}


def rendered_page(module_file, renderer):
    """The HTML a renderer returns, rebuilt from its source without running it.

    Every renderer ends in `return "..." + value + "..."`. The literals are
    taken as written -- `ast` has already resolved their escapes, so this is
    the text the browser receives. A spliced name that `next_views_shared_js`
    exports is replaced by its value; any other spliced expression becomes
    `null`, which keeps `JSON.parse(` + ... + `)` a well-formed call.

    Importing the module would be simpler and would also need Flask, psycopg
    and a JWT secret, and a test that needs those gets skipped without them.
    """
    path = os.path.join(BACKEND_DIR, module_file)
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    function = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == renderer
    )
    returned = [node.value for node in function.body if isinstance(node, ast.Return)]
    if len(returned) != 1:
        raise AssertionError(f"{renderer} no longer ends in a single return expression")

    parts = []

    def flatten(node):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            flatten(node.left)
            flatten(node.right)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            parts.append(node.value)
        elif isinstance(node, ast.Name) and isinstance(getattr(next_views_shared_js, node.id, None), str):
            parts.append(getattr(next_views_shared_js, node.id))
        else:
            parts.append("null")

    flatten(returned[0])
    return "".join(parts)


def executable_scripts(html):
    """The inline scripts a browser would run, joined into one global scope."""
    bodies = []
    for attributes, body in re.findall(r"<script([^>]*)>([\s\S]*?)</script>", html, re.IGNORECASE):
        if re.search(r"type\s*=\s*[\"']application/json[\"']", attributes, re.IGNORECASE):
            continue
        if body.strip():
            bodies.append(body)
    return "\n;\n".join(bodies)


def tokenize(script):
    """Split a script into `(kind, text, offset)` tokens.

    Only as much of JavaScript as it takes to never mistake text for code:
    comments are dropped, a quoted string or regular expression is one token,
    and a template literal contributes the tokens of its `${...}` expressions
    between a `${` and a `}$` marker -- spelled differently from the braces
    so that matching a `{` never lands inside a template.
    """
    tokens = []
    length = len(script)

    def template(index):
        while index < length:
            char = script[index]
            if char == "\\":
                index += 2
            elif char == "`":
                return index + 1
            elif script.startswith("${", index):
                tokens.append(("punct", "${", index))
                index = code(index + 2, inside_template=True)
            else:
                index += 1
        return index

    def starts_regex():
        if not tokens:
            return True
        kind, text, _ = tokens[-1]
        if kind == "punct":
            return text not in (")", "]", "}")
        return kind == "id" and text in REGEX_AFTER_KEYWORD

    def code(index, inside_template=False):
        depth = 0
        while index < length:
            char = script[index]
            if char.isspace():
                index += 1
                continue
            if script.startswith("//", index):
                end = script.find("\n", index)
                index = length if end < 0 else end
                continue
            if script.startswith("/*", index):
                end = script.find("*/", index)
                index = length if end < 0 else end + 2
                continue
            if char in "\"'":
                end = index + 1
                while end < length and script[end] != char:
                    end += 2 if script[end] == "\\" else 1
                tokens.append(("str", script[index : end + 1], index))
                index = end + 1
                continue
            if char == "`":
                tokens.append(("str", "`", index))
                index = template(index + 1)
                tokens.append(("str", "`", index))
                continue
            if char == "/" and starts_regex():
                end, in_class = index + 1, False
                while end < length and script[end] != "\n":
                    if script[end] == "\\":
                        end += 2
                        continue
                    if script[end] == "[":
                        in_class = True
                    elif script[end] == "]":
                        in_class = False
                    elif script[end] == "/" and not in_class:
                        break
                    end += 1
                end += 1
                while end < length and script[end].isalpha():
                    end += 1
                tokens.append(("regex", script[index:end], index))
                index = end
                continue
            match = IDENTIFIER.match(script, index)
            if match:
                tokens.append(("id", match.group(0), index))
                index = match.end()
                continue
            match = NUMBER.match(script, index)
            if match:
                tokens.append(("num", match.group(0), index))
                index = match.end()
                continue
            if inside_template:
                if char == "{":
                    depth += 1
                elif char == "}":
                    if depth == 0:
                        tokens.append(("punct", "}$", index))
                        return index + 1
                    depth -= 1
            for operator in ("?.", "=>", "..."):
                if script.startswith(operator, index):
                    # `a?.5:b` is a ternary, not optional chaining; no page
                    # here writes one, and reading it as a member access can
                    # only hide a finding, never invent one.
                    tokens.append(("punct", operator, index))
                    index += len(operator)
                    break
            else:
                tokens.append(("punct", char, index))
                index += 1
        return index

    code(0)
    return tokens


def unresolved_calls(script):
    """Bare `name(...)` calls that nothing in the script can bind.

    Returns `{name: [line, ...]}`, lines counted within the script.
    """
    tokens = tokenize(script)
    count = len(tokens)

    def text(index):
        return tokens[index][1] if 0 <= index < count else ""

    def kind(index):
        return tokens[index][0] if 0 <= index < count else ""

    def is_punct(index, value):
        return kind(index) == "punct" and text(index) == value

    # Pair every bracket once, so each lookup below is a dictionary read.
    partner = {}
    stack = []
    for index, (token_kind, token_text, _) in enumerate(tokens):
        if token_kind != "punct":
            continue
        if token_text in OPENERS or token_text == "${":
            stack.append(index)
        elif token_text in (")", "]", "}", "}$") and stack:
            opened = stack.pop()
            partner[opened] = index
            partner[index] = opened

    declared = set()

    def bind(start, end):
        """Every name a binding pattern in `tokens[start:end]` introduces.

        Default values are bound along with the rest. That over-declares, and
        over-declaring is the safe direction for a check that only reports
        what cannot resolve.
        """
        for index in range(start, end):
            if kind(index) != "id" or text(index) in JS_KEYWORDS:
                continue
            if is_punct(index - 1, ".") or is_punct(index - 1, "?."):
                continue
            if is_punct(index + 1, ":"):
                continue  # `{key: local}` -- the key is not a binding
            declared.add(text(index))

    def bind_declarators(index):
        """The names after a `const` / `let` / `var` at `tokens[index - 1]`."""
        while index < count:
            if is_punct(index, "{") or is_punct(index, "["):
                end = partner.get(index, count)
                bind(index + 1, end)
                index = end + 1
            elif kind(index) == "id" and text(index) not in JS_KEYWORDS:
                declared.add(text(index))
                index += 1
            else:
                return
            if not is_punct(index, "="):
                if is_punct(index, ","):
                    index += 1
                    continue
                return
            # Skip the initializer to the comma that starts the next
            # declarator, stepping over anything bracketed.
            index += 1
            while index < count:
                if kind(index) == "punct":
                    value = text(index)
                    if value in OPENERS or value == "${":
                        index = partner.get(index, count) + 1
                        continue
                    if value in (")", "]", "}", "}$", ";"):
                        return
                    if value == ",":
                        break
                elif kind(index) == "id" and text(index) in ("const", "let", "var"):
                    return
                index += 1
            index += 1

    def is_method_definition(index):
        """`name(...) {` -- an object or class method, not a call."""
        close = partner.get(index + 1)
        return close is not None and is_punct(close + 1, "{")

    for index, (token_kind, token_text, _) in enumerate(tokens):
        if token_kind == "punct":
            if token_text == "=>" and is_punct(index - 1, ")") and (index - 1) in partner:
                bind(partner[index - 1] + 1, index - 1)
            continue
        if token_kind != "id":
            continue
        if token_text == "function":
            cursor = index + 1
            if is_punct(cursor, "*"):
                cursor += 1
            if kind(cursor) == "id":
                declared.add(text(cursor))
                cursor += 1
            if is_punct(cursor, "(") and cursor in partner:
                bind(cursor + 1, partner[cursor])
        elif token_text in ("const", "let", "var"):
            bind_declarators(index + 1)
        elif token_text == "catch" and is_punct(index + 1, "(") and (index + 1) in partner:
            bind(index + 2, partner[index + 1])
        elif token_text == "class" and kind(index + 1) == "id":
            declared.add(text(index + 1))
        elif token_text not in JS_KEYWORDS:
            if is_punct(index + 1, "=>") and not is_punct(index - 1, "."):
                declared.add(token_text)
            elif is_punct(index + 1, "(") and is_method_definition(index):
                bind(index + 2, partner[index + 1])

    missing = {}
    for index, (token_kind, token_text, offset) in enumerate(tokens):
        if token_kind != "id" or token_text in JS_KEYWORDS:
            continue
        if not is_punct(index + 1, "("):
            continue
        if is_punct(index - 1, ".") or is_punct(index - 1, "?."):
            continue  # a method on something else
        if kind(index - 1) == "id" and text(index - 1) == "function":
            continue
        if is_method_definition(index):
            continue
        if token_text in declared or token_text in BROWSER_GLOBALS:
            continue
        missing.setdefault(token_text, []).append(script.count("\n", 0, offset) + 1)
    return missing


class UnresolvedCallScannerTests(unittest.TestCase):
    """The gate's own tests, in both directions.

    A checker with no test is the thing it exists to prevent: it reports
    nothing, and nothing reads the same as clean. The false-positive half
    matters as much -- a gate that refuses working scripts gets switched off.
    """

    def test_a_call_to_an_undeclared_function_is_reported_with_its_line(self):
        script = "function render(report) {\n  return label(report.id);\n}\n"
        self.assertEqual(unresolved_calls(script), {"label": [2]})

    def test_the_shape_that_broke_the_migration_wizard(self):
        script = (
            "function escapeHtml(value) { return String(value); }\n"
            "function renderWizard(report) {\n"
            "  const source = report.source || {};\n"
            "  return `<strong>${escapeHtml(pluginDisplayName(source.pluginId, source.pluginName || \"-\"))}</strong>`;\n"
            "}\n"
        )
        self.assertEqual(unresolved_calls(script), {"pluginDisplayName": [4]})

    def test_declarations_of_every_kind_resolve_a_call(self):
        script = """
            function declared() {}
            async function declaredAsync() {}
            const arrow = () => 1, second = function () {};
            let later;
            var {picked, renamed: local, ...rest} = source();
            function source() { return {}; }
            class Widget {}
            declared(); declaredAsync(); arrow(); second(); later(); picked(); local(); rest();
            new Widget();
        """
        self.assertEqual(unresolved_calls(script), {})

    def test_parameters_resolve_a_call(self):
        script = """
            function run(callback, {onDone}, fallback = () => null) { callback(); onDone(); fallback(); }
            const each = (handler) => handler();
            const one = single => single();
            [1].forEach(function (visit) { visit(); });
            try { run(); } catch (error) { error(); }
            const api = { method(hook) { hook(); }, async other(hook2) { hook2(); } };
        """
        self.assertEqual(unresolved_calls(script), {})

    def test_a_property_key_does_not_declare_the_name(self):
        """`{renamed: local}` binds `local`. Counting `renamed` as bound too
        would let a destructured key vouch for a function that does not exist."""
        script = "const {renamed: local} = {};\nrenamed();\nlocal();\n"
        self.assertEqual(unresolved_calls(script), {"renamed": [2]})

    def test_method_calls_and_keywords_are_not_free_calls(self):
        script = """
            const node = document.getElementById("x");
            node?.focus();
            if (node) { for (const item of [node]) { while (false) {} } }
            switch (typeof (node)) { default: break; }
        """
        self.assertEqual(unresolved_calls(script), {})

    def test_text_is_not_code(self):
        script = (
            "// commentedOut(1)\n"
            "/* blockComment(2) */\n"
            "const a = \"inString(3)\";\n"
            "const b = 'inSingle(4)';\n"
            "const c = `inTemplate(5) ${a} stillText(6)`;\n"
            "const d = /inRegex\\(7\\)[/(]/g.test(a);\n"
            "const e = a.length / 2; const f = lateCall(e) / 2;\n"
        )
        self.assertEqual(unresolved_calls(script), {"lateCall": [7]})

    def test_code_inside_a_template_expression_is_still_code(self):
        script = "const row = `<td>${[1].map((n) => `${cell(n)}`).join(\"\")}</td>`;\n"
        self.assertEqual(unresolved_calls(script), {"cell": [1]})

    def test_browser_globals_resolve(self):
        script = "setTimeout(() => fetch(encodeURIComponent(String(parseInt('1', 10)))), 0);\n"
        self.assertEqual(unresolved_calls(script), {})


class StandalonePageScriptTests(unittest.TestCase):
    def test_the_renderers_are_read_as_whole_scripts(self):
        """Guards the extraction itself: an empty script has no unresolved
        calls, and a sweep over nothing would pass forever."""
        for module_file, renderer in STANDALONE_PAGES:
            with self.subTest(page=renderer):
                script = executable_scripts(rendered_page(module_file, renderer))
                self.assertGreater(len(script), 10_000, "the page's script was not found")
                # assertTrue rather than assertIn: a failure would otherwise
                # print the whole script as the message.
                self.assertTrue("function escapeHtml(" in script, "escapeHtml is not in the page's script")

    def test_every_called_function_is_defined_by_the_page_that_calls_it(self):
        for module_file, renderer in STANDALONE_PAGES:
            with self.subTest(page=renderer):
                script = executable_scripts(rendered_page(module_file, renderer))
                missing = unresolved_calls(script)
                self.assertEqual(
                    missing,
                    {},
                    f"{module_file} calls functions its own script never defines; "
                    "each throws a ReferenceError the first time that line runs: "
                    + ", ".join(
                        f"{name} (script lines {lines[:5]})" for name, lines in sorted(missing.items())
                    ),
                )

    def test_plugin_display_name_is_declared_exactly_once_per_page(self):
        """One shared definition, spliced into each page once.

        Zero is the bug this file exists for. Two is the silent overwrite
        `test_next_views_ui_scope` describes -- and that test reads
        `next_views_ui.py` as text, so it cannot see a declaration that
        arrives through a splice.
        """
        for module_file, renderer in STANDALONE_PAGES + (SHELL_PAGE,):
            with self.subTest(page=renderer):
                script = executable_scripts(rendered_page(module_file, renderer))
                declarations = re.findall(r"\bfunction\s+pluginDisplayName\s*\(", script)
                self.assertEqual(len(declarations), 1, f"{module_file}: {len(declarations)} declarations")

    def test_the_shared_helper_is_not_redefined_inline(self):
        """A local copy would shadow or duplicate the shared one, and its
        table of plugin names would then drift from the other two pages'."""
        for module_file, _ in STANDALONE_PAGES + (SHELL_PAGE,):
            with self.subTest(module=module_file):
                with open(os.path.join(BACKEND_DIR, module_file), encoding="utf-8") as handle:
                    source = handle.read()
                self.assertFalse(
                    "function pluginDisplayName(" in source,
                    f"{module_file} defines pluginDisplayName inline; splice PLUGIN_DISPLAY_NAME_JS instead",
                )
                self.assertTrue(
                    "+ PLUGIN_DISPLAY_NAME_JS +" in source,
                    f"{module_file} does not splice PLUGIN_DISPLAY_NAME_JS into its script",
                )


if __name__ == "__main__":
    unittest.main()
