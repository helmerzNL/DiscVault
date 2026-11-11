"""A header with no backdrop draws nothing -- not a broken-image icon.

Every detail header, and the library's featured hero, is an ``<img>`` stretched
over the header. Assigning ``""`` to its ``src`` leaves the attribute in place;
the element keeps its full-header box and the browser paints its broken-image
icon in the top-left corner. Nothing errors, so the only thing that reports it
is somebody looking at a location that has no backdrop yet.

These are source assertions, like the other ``*_ui`` modules here: the script is
one inline block inside ``next_views_ui.py`` and there is no DOM to run it in.
"""

import os
import re
import unittest


NEXT_VIEWS_UI_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "next_views_ui.py")
)

#: Every element that is a header backdrop.
BACKDROP_IDS = (
    "heroBackdrop",
    "movieDetailBackdrop",
    "containerDetailBackdrop",
    "seriesDetailBackdrop",
    "discoverDetailBackdrop",
    "locationDetailBackdrop",
)


class BackdropHeaderUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(NEXT_VIEWS_UI_PATH, encoding="utf-8") as handle:
            cls.source = handle.read()

    def test_the_helper_drops_the_attribute_and_hides_the_element(self):
        start = self.source.index("function setBackdropImage(node, url)")
        body = self.source[start : self.source.index("function escapeHtml", start)]
        self.assertIn('node.removeAttribute("src")', body)
        self.assertIn('node.classList.toggle("hidden", !url)', body)
        # The empty branch must not assign: `src = ""` is the defect itself.
        self.assertNotIn('node.src = ""', body)

    def test_every_backdrop_is_written_through_the_helper(self):
        for element_id in BACKDROP_IDS:
            with self.subTest(element=element_id):
                self.assertIn(
                    f'setBackdropImage(document.getElementById("{element_id}"),',
                    self.source,
                )

    def test_no_backdrop_src_is_assigned_directly(self):
        # Both spellings that existed: through a `backdropNode` local, and
        # straight off `getElementById` in the "loading" reset of each page.
        self.assertNotRegex(self.source, r"backdropNode\.src\s*=")
        self.assertNotRegex(
            self.source,
            r'getElementById\("[A-Za-z]*Backdrop"\)\??\.src\s*=',
        )

    def test_backdrops_start_hidden_until_one_is_known(self):
        # Before the first render there is no backdrop to show either. The
        # featured hero is the one exception: the server knows its image, and
        # emits the tag with a `src` and without `hidden` when there is one.
        for element_id in BACKDROP_IDS:
            with self.subTest(element=element_id):
                tags = re.findall(rf'<img[^>]*id="{element_id}"[^>]*>', self.source)
                self.assertTrue(tags, "the backdrop element is gone")
                for tag in tags:
                    if "src=" in tag:
                        self.assertNotIn("hidden", tag)
                    else:
                        self.assertIn("hidden", tag)


if __name__ == "__main__":
    unittest.main()
