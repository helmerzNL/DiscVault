import os
import unittest


NEXT_VIEWS_UI_PATH = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "next_views_ui.py",
    )
)


class NextMovieDetailUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(NEXT_VIEWS_UI_PATH, encoding="utf-8") as handle:
            cls.source = handle.read()

    def test_section_tabs_are_above_personal_lists(self):
        tabs_index = self.source.index(
            'class="detail-submenu movie-detail-section-tabs"'
        )
        release_panel_index = self.source.index('id="movieDetailReleasePanel"')
        personal_lists_index = self.source.index('id="movieListStateCard"')

        self.assertLess(tabs_index, release_panel_index)
        self.assertLess(release_panel_index, personal_lists_index)

    def test_section_tabs_use_localized_labels_and_separate_panels(self):
        expected_tabs = (
            (
                "movieDetail.release",
                "movieDetailReleasePanel",
                "movieDetailReleaseTab",
                "true",
            ),
            (
                "movieDetail.technical",
                "movieDetailTechnicalPanel",
                "movieDetailTechnicalTab",
                "false",
            ),
            (
                "movieDetail.collectors",
                "movieDetailCollectorsPanel",
                "movieDetailCollectorsTab",
                "false",
            ),
        )
        for key, panel_id, tab_id, selected_state in expected_tabs:
            self.assertIn(f'data-next-i18n="{key}"', self.source)
            self.assertIn(f'data-detail-panel="{panel_id}"', self.source)
            self.assertIn(
                f'id="{tab_id}" role="tab" aria-controls="{panel_id}" '
                f'aria-selected="{selected_state}"',
                self.source,
            )
            self.assertIn(
                f'id="{panel_id}" role="tabpanel" aria-labelledby="{tab_id}" '
                'data-detail-panel-group="movieSections"',
                self.source,
            )

        self.assertIn('id="movieDetailRelease"', self.source)
        self.assertIn('id="movieDetailTechnical"', self.source)
        self.assertIn('id="movieDetailCollectors"', self.source)

    def test_rendering_splits_technical_and_collectors_fields(self):
        self.assertIn(
            'document.getElementById("movieDetailTechnical").innerHTML = '
            "detailFieldRows(audioVideoFields);",
            self.source,
        )
        self.assertIn(
            'document.getElementById("movieDetailCollectors").innerHTML = '
            "detailFieldRows(collectorsFields);",
            self.source,
        )
        self.assertIn(
            'activateDetailTab("movieSections", "movieDetailReleasePanel");',
            self.source,
        )

    def test_cast_and_crew_block_precedes_media(self):
        self.assertIn('data-next-i18n="movieDetail.castCrew"', self.source)
        people_index = self.source.index('data-detail-tab="moviePeople"')
        media_index = self.source.index('data-detail-tab="movieMedia"')

        self.assertLess(people_index, media_index)


if __name__ == "__main__":
    unittest.main()
