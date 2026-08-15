"""The Images tab draws a square gallery, and every action sits behind one menu.

The rules pinned here are normative for *every* DiscVault client and live in
App-Guidance `docs/apps/discvault/film-detail-media.md`, section "Hoe de galerij
getoond wordt". They were established after the iOS build showed the tab on a
device; the PWA did not meet them, because it inherited its frame and its
scaling from the artwork gallery it shares its markup with. For a poster a 2:3
frame with `object-fit: cover` is the right answer, so nothing looked broken --
the PWA had simply never made the choice.

What goes wrong without each rule:

- **Fitted, not filled.** These photographs share no aspect ratio: a case stands
  upright, a spine is long and thin, a disc is round, an insert is usually
  landscape. `cover` scales until the short side covers the frame and discards
  the rest, so a spine becomes a strip of its middle and the printing -- the
  only reason anyone photographs a spine -- falls outside the frame.
- **Square, sized by the grid.** A square frame favours none of the five shapes
  and keeps the rows level. It has to come from the grid track: on iOS the cell
  was sized by the photograph (`.aspectRatio(1, contentMode: .fill)`), every
  cell reported a width outside its column, and the grid drew over the sections
  below it.
- **Backdrop width, not poster width.** Four per row is too small to read a
  title off a spine or the text on an insert, which is what the user opened the
  tab to check.

Source-text assertions, in the idiom the other UI tests here use.
"""

import os
import re
import unittest


NEXT_VIEWS_UI_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "next_views_ui.py")
)

#: Every action the iOS Images tab offers behind a long press, by i18n key.
#: Share is the odd one out: it is the only non-mutating action, so it is the
#: one a reader without the artwork permission still gets.
OWN_IMAGE_MENU_KEYS = (
    "ownImages.makePrimary",
    "ownImages.useAsPoster",
    "ownImages.useAsBackdrop",
    "ownImages.label",
    "common.share",
    "ownImages.hide",
    "ownImages.unhide",
    "common.delete",
)


class OwnImagesGalleryUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(NEXT_VIEWS_UI_PATH, encoding="utf-8") as handle:
            cls.source = handle.read()

    def own_image_card_source(self) -> str:
        start = self.source.index("function ownImageCardHtml(")
        end = self.source.index("function ownImagePendingCardHtml(", start)
        return self.source[start:end]

    def own_image_menu_source(self) -> str:
        start = self.source.index("function openOwnImageActionsMenu(")
        end = self.source.index("function openOwnImageLabelMenu(", start)
        return self.source[start:end]

    def test_the_frame_is_square(self):
        self.assertIn(
            ".own-image-option .art-option-preview {\n      aspect-ratio: 1 / 1;\n    }",
            self.source,
        )

    def test_the_photograph_is_fitted_into_the_square_and_never_cropped(self):
        """`contain`, and it must be stated on the own-image cell itself.

        `.art-option-preview img` is `object-fit: cover` for the poster and
        backdrop galleries and stays that way -- cropping a poster is harmless,
        it has the same ratio everywhere and can be fetched again. The own-image
        rule has to override it rather than replace it.
        """
        self.assertIn(
            ".own-image-option .art-option-preview img {\n      object-fit: contain;\n    }",
            self.source,
        )
        self.assertIn(
            ".art-option-preview img {\n      width: 100%;\n      height: 100%;\n      object-fit: cover;",
            self.source,
        )

    def test_the_square_is_sized_by_the_grid_and_not_by_the_image(self):
        """No sizing hint on the `<img>`, which is what would let the picture
        decide the cell. The image fills a cell the track already sized.
        """
        card = self.own_image_card_source()
        self.assertIn('<img src="${escapeHtml(image.url)}"', card)
        self.assertNotIn("aspect-ratio", card)
        self.assertNotIn("width=", card)
        self.assertNotIn("height=", card)

    def test_the_width_follows_the_backdrops_and_not_the_posters(self):
        """Each own-image grid mirrors the backdrop grid on its own page: 180px
        tracks on the container and series pages, 200px on the movie page, where
        `#movieDetailBackdropArtwork` is 200px too. The base `.art-option-grid`
        is 104px -- poster width -- and inheriting it is the bug this pins.
        """
        for selector, track in (
            (".own-image-grid", "180px"),
            ("#movieOwnImagesGrid", "200px"),
        ):
            with self.subTest(selector=selector):
                self.assertIn(
                    f"{selector} {{\n      grid-template-columns:"
                    f" repeat(auto-fill, minmax(min({track}, 100%), 1fr));\n    }}",
                    self.source,
                )
        self.assertIn(
            "#movieDetailBackdropArtwork {\n"
            "      grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));\n    }",
            self.source,
        )

    def test_the_whole_square_is_the_target(self):
        """The photograph no longer reaches every edge of its frame, so the tile
        carries the role and the keyboard affordance -- not the `<img>`, and not
        a strip of buttons under it.
        """
        card = self.own_image_card_source()
        self.assertIn('tabindex="0"', card)
        self.assertIn('role="button"', card)
        self.assertIn('aria-haspopup="dialog"', card)

    def test_the_label_is_bottom_left_and_the_primary_badge_top_right(self):
        card = self.own_image_card_source()
        self.assertIn('<span class="own-image-label">', card)
        self.assertIn('class="art-option-badge own-image-primary"', card)
        self.assertIn(
            ".own-image-label {\n      position: absolute;\n      left: 6px;\n      bottom: 6px;",
            self.source,
        )
        self.assertIn(
            ".own-image-option .art-option-badge.own-image-primary {\n"
            "      left: auto;\n      right: 6px;\n    }",
            self.source,
        )

    def test_an_upload_in_flight_has_a_square_of_its_own(self):
        """Required by sync-contract 4d.8, not decoration. These are the
        photographs no source can supply again, so "did mine arrive?" has to be
        answerable by looking at the grid.
        """
        self.assertIn("function ownImagePendingCardHtml(label)", self.source)
        self.assertIn("const ownImagesPending = {movie: [], container: [], series: []};", self.source)
        self.assertIn("ownImagesPending[entity] = files.map(() => ({label}));", self.source)
        # Cleared on both exits, or a failed upload leaves a square that never
        # resolves into a photograph.
        upload = self.source[
            self.source.index("async function uploadOwnImages(") :
            self.source.index("async function ownImageRequest(")
        ]
        self.assertEqual(upload.count('ownImagesPending[entity] = [];'), 2)

    def test_every_ios_action_is_in_the_long_press_menu(self):
        menu = self.own_image_menu_source()
        for key in OWN_IMAGE_MENU_KEYS:
            with self.subTest(key=key):
                self.assertIn(f'tNext("{key}"', menu)

    def test_the_menu_opens_on_long_press_on_all_three_pages(self):
        """`bindLongPressActionMenu` is the poster and backdrop binder, and it
        answers to a long press, a right-click, a plain click and the keyboard
        alike. The own-image binder is not scoped to `#movieDetailPage`: the tab
        is on the box-set and series pages too.
        """
        self.assertIn(
            'bindLongPressActionMenu(tile, () => openOwnImageActionsMenu(tile));',
            self.source,
        )
        binder = self.source[
            self.source.index("function bindOwnImageLongPressMenus(") :
            self.source.index("function bindOwnImageLongPressMenus(") + 400
        ]
        self.assertNotIn("#movieDetailPage", binder)

    def test_the_card_carries_no_button_row(self):
        """Every action moved into the sheet. A leftover control would be a
        second way to do the same thing, and the one that goes stale.
        """
        card = self.own_image_card_source()
        self.assertNotIn("<button", card)
        self.assertNotIn("<select", card)
        self.assertNotIn(".own-image-actions", self.source)

    def test_the_deletion_still_asks_first(self):
        """Everywhere else a deletion can be fetched again from its source. Not
        here, and the confirmation says so in as many words.
        """
        menu = self.own_image_menu_source()
        self.assertIn("window.confirm(tNext(\"ownImages.deleteConfirm\"", menu)

    def test_the_mutating_actions_are_gated_on_the_artwork_permission(self):
        menu = self.own_image_menu_source()
        self.assertIn(
            "const canEdit = hasAnyPermission(APP_PERMISSION_GROUPS.artworkManage);",
            menu,
        )

    def test_one_action_sheet_serves_posters_backdrops_and_own_images(self):
        """A second copy of the sheet is how the three would come to offer the
        same action under two different labels.
        """
        self.assertEqual(
            len(re.findall(r"function openArtworkActionSheet\(", self.source)), 1
        )
        for caller in ("openMovieArtworkActionsMenu", "openOwnImageActionsMenu", "openOwnImageLabelMenu"):
            with self.subTest(caller=caller):
                start = self.source.index(f"function {caller}(")
                end = self.source.index("\n    }\n", start)
                self.assertIn("openArtworkActionSheet(", self.source[start:end])


if __name__ == "__main__":
    unittest.main()
