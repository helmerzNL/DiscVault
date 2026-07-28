import os
import re
import unittest


NEXT_VIEWS_UI_PATH = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "next_views_ui.py",
    )
)


class NextImportCenterUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(NEXT_VIEWS_UI_PATH, encoding="utf-8") as handle:
            cls.source = handle.read()
        start = cls.source.index('<section class="import-view hidden" id="importView"')
        end = cls.source.index('<section class="discover-view hidden" id="discoverView">')
        cls.markup = cls.source[start:end]

    def test_mode_tabs_use_dashboard_tablist(self):
        self.assertIn(
            'class="detail-submenu profile-dashboard-tabs import-section-tabs import-mode-tabs" role="tablist"',
            self.markup,
        )
        expected = (
            ("add", "importTabAdd", "importPanelAdd", "true"),
            ("sources", "importTabSources", "importPanelSources", "false"),
            ("plan", "importTabPlan", "importPanelPlan", "false"),
            ("review", "importTabReview", "importPanelReview", "false"),
            ("jobs", "importTabJobs", "importPanelJobs", "false"),
        )
        for tab, tab_id, panel_id, selected in expected:
            self.assertIn(f'id="{tab_id}"', self.markup)
            self.assertIn(f'aria-controls="{panel_id}"', self.markup)
            self.assertIn(f'data-import-tab="{tab}"', self.markup)
            self.assertIn(f'id="{panel_id}"', self.markup)
            self.assertIn(f'data-import-panel="{tab}"', self.markup)
            button = re.search(
                rf'<button[^>]*id="{tab_id}"[^>]*>',
                self.markup,
            )
            self.assertIsNotNone(button, tab_id)
            self.assertIn('role="tab"', button.group(0))
            self.assertIn(f'aria-selected="{selected}"', button.group(0))

    def test_method_tabs_use_dashboard_tablist(self):
        self.assertIn(
            'class="detail-submenu profile-dashboard-tabs import-section-tabs import-method-tabs" role="tablist"',
            self.markup,
        )
        expected = (
            ("camera", "importMethodTabCamera", "importMethodPanelCamera"),
            ("single", "importMethodTabSingle", "importMethodPanelSingle"),
            ("batch", "importMethodTabBatch", "importMethodPanelBatch"),
            ("boxset", "importMethodTabBoxset", "importMethodPanelBoxset"),
            ("csv", "importMethodTabCsv", "importMethodPanelCsv"),
        )
        for method, tab_id, panel_id in expected:
            self.assertIn(f'id="{tab_id}"', self.markup)
            self.assertIn(f'aria-controls="{panel_id}"', self.markup)
            self.assertIn(f'data-import-method="{method}"', self.markup)
            self.assertIn(f'data-import-method-panel="{method}"', self.markup)

    def test_every_panel_uses_the_dashboard_shell(self):
        panels = re.findall(r'data-import-panel="([a-z]+)"', self.markup)
        self.assertEqual(sorted(panels), ["add", "jobs", "plan", "review", "sources"])
        self.assertEqual(
            self.markup.count('class="profile-dashboard import-dashboard"'),
            len(panels),
        )
        self.assertEqual(
            self.markup.count('class="profile-dashboard-intro"'),
            len(panels),
        )
        for panel in panels:
            self.assertIn(f'role="tabpanel"', self.markup)
            self.assertIn(f'aria-labelledby="importTab{panel.capitalize()}"', self.markup)

    def test_import_cards_use_dashboard_card_classes(self):
        self.assertIn("profile-dashboard-card primary import-dashboard-card", self.markup)
        self.assertIn("profile-dashboard-card-head", self.markup)
        self.assertIn("profile-dashboard-card-icon", self.markup)
        for legacy in (
            "barcode-scanner-shell",
            "import-manual-card",
            "import-batch-card",
            "import-file-upload-card",
            "import-mapping-card",
            "import-scanner-card",
        ):
            self.assertNotIn(legacy, self.source, legacy)

    def test_functional_element_ids_are_preserved(self):
        for element_id in (
            "importScannerStartButton",
            "importScannerStopButton",
            "importFileUploadCard",
            "importCenterMappingCard",
            "importReviewWorkbench",
            "importCenterJobs",
            "boxSetBuilderIdentifyCard",
            "boxSetBuilderActiveCard",
            "importCenterPreview",
            "importCenterSources",
            "importCenterStartButton",
        ):
            self.assertIn(f'id="{element_id}"', self.markup, element_id)

    def test_tab_renderers_sync_aria_state(self):
        self.assertIn('button.setAttribute("aria-selected", selected ? "true" : "false");', self.source)
        self.assertIn('panel.setAttribute("aria-hidden", selected ? "false" : "true");', self.source)

    def test_import_dashboard_styles_exist(self):
        for rule in (
            ".import-dashboard {",
            ".import-dashboard-grid {",
            ".import-mode-tabs.profile-dashboard-tabs {",
            ".import-method-tabs.profile-dashboard-tabs {",
            ".import-section-tabs button[aria-selected=\"true\"]",
        ):
            self.assertIn(rule, self.source, rule)


if __name__ == "__main__":
    unittest.main()
