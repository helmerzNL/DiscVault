"""A toolbar menu trigger draws its icon wherever it is placed (#719).

Reported as "the sort is in the List Module now, but it is a blank button. I
see the slight outline, so I guessed it was sort". The outline is the
`.icon-button` border; the icon inside it was drawing nothing.

The sizing rule for these icons was written as
`.collection-controls .icon-button svg`, so it reached the four triggers that
happen to sit inside a `.collection-controls` container -- the Library toolbar
and the location detail toolbar -- and not the Lists sort trigger added by
#733, which sits in `.detail-card-actions`. An inline `<svg>` with no width,
no height and no `fill` is not an error: it renders, at whatever size the
element defaults to and in the default fill, and the result is an empty
button.

Nothing in the markup of the Lists trigger differs from the Library's; the two
buttons are character-for-character the same element. The difference is the
container three levels up, which is exactly the thing a reviewer reading the
diff of a new button cannot see.

So the invariant is: the icon of a `.toolbar-menu-trigger` is sized by that
button's own class, never by an ancestor container. A new trigger then works
wherever it is dropped.

These are source-contract tests over the CSS the UI module emits. Needs no
database and no Flask.
"""

import os
import re
import unittest


SOURCE_PATH = os.path.join(os.path.dirname(__file__), "..", "next_views_ui.py")

TRIGGER_CLASS = "toolbar-menu-trigger"


def _rule_bodies_for(source, selector):
    """Return the declaration block of every CSS rule listing `selector`."""
    bodies = []
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", source):
        selectors = [part.strip() for part in match.group(1).split(",")]
        if selector in selectors:
            bodies.append(match.group(2))
    return bodies


class ToolbarMenuTriggerIconTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(SOURCE_PATH, encoding="utf-8") as handle:
            cls.source = handle.read()

    def test_the_trigger_icon_is_sized_by_the_trigger_class(self):
        """The rule must key on `.toolbar-menu-trigger svg`, not on a container.

        Without the fix no rule lists this selector at all, and the Lists sort
        button draws an unsized, unfilled icon -- the reported blank button.
        """
        bodies = _rule_bodies_for(self.source, f".{TRIGGER_CLASS} svg")
        self.assertTrue(
            bodies,
            "no CSS rule sizes `.toolbar-menu-trigger svg`; a trigger placed "
            "outside `.collection-controls` will render a blank button",
        )
        declarations = " ".join(bodies)
        for prop in ("width", "height", "fill"):
            self.assertIn(
                prop,
                declarations,
                f"`.{TRIGGER_CLASS} svg` must declare {prop}: an inline SVG "
                "without it falls back to the browser default",
            )

    def test_every_trigger_carries_an_inline_icon_and_no_text_label(self):
        """Each trigger is icon-only, so a missing icon leaves nothing at all.

        The only non-icon child any trigger has is the filter badge, which is
        hidden until it counts something -- it cannot stand in for the icon.
        A trigger carrying a translated text label would not depend on the
        sizing rule and would not belong to this invariant.
        """
        triggers = re.findall(
            r"<button[^>]*class=\"[^\"]*" + TRIGGER_CLASS + r"[^\"]*\"[^>]*>(.*?)</button>",
            self.source,
            re.DOTALL,
        )
        self.assertGreaterEqual(len(triggers), 5, "expected the known triggers")
        for body in triggers:
            self.assertIn("<svg", body)
            self.assertNotIn(
                "data-next-i18n=", body,
                "a trigger with a visible text label would not depend on the "
                "icon rule; the label of these buttons is the tooltip and the "
                "aria-label, both set with data-next-i18n-title/-aria",
            )

    def test_a_trigger_lives_outside_the_collection_controls_container(self):
        """Guard against folding the rule back into the container-scoped one.

        The Lists sort trigger is the reason the class-scoped rule has to
        exist. If it -- or any other trigger -- is outside
        `.collection-controls`, `.collection-controls .icon-button svg` cannot
        be the only thing sizing these icons.
        """
        lists_trigger = self.source.index('id="listsSortTrigger"')
        enclosing = self.source[:lists_trigger]
        last_controls = enclosing.rfind('class="collection-controls"')
        last_card_actions = enclosing.rfind('class="detail-card-actions"')
        self.assertGreater(
            last_card_actions,
            last_controls,
            "listsSortTrigger is expected in `.detail-card-actions`; if it "
            "moved into `.collection-controls`, this test documents nothing",
        )


if __name__ == "__main__":
    unittest.main()
