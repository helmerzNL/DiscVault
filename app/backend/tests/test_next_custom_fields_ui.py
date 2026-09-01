"""The custom-fields admin screen and the edit-form inputs, read as source text.

Two claims here are worth the assertions.

**Field labels are never translated.** The owner typed them. Every other label in
this SPA goes through `tNext`, so a reviewer's instinct is to wrap these too --
and doing so would look up a translation key that can never exist and render the
fallback, which is the raw name, which is what it should have shown anyway. It
would work by accident until someone "fixed" the missing keys.

**A stored select value whose option was removed is injected back.** Otherwise
opening a film and pressing save on an unrelated field silently rewrites it to
blank -- the failure `trackSelectHtml` already guards one feature over.
"""

import os
import sys
import unittest


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
NEXT_VIEWS_UI_PATH = os.path.join(BACKEND_DIR, "next_views_ui.py")
I18N_DIR = os.path.abspath(os.path.join(BACKEND_DIR, "..", "frontend", "i18n", "next"))

if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)


def _source() -> str:
    with open(NEXT_VIEWS_UI_PATH, "r", encoding="utf-8") as handle:
        return handle.read()


class AdminTabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = _source()

    def test_the_tab_exists_and_is_reachable(self):
        self.assertIn('admin_tab("custom_fields"', self.source)
        self.assertIn('id="appAdminPanelCustomFields"', self.source)
        self.assertIn('data-app-admin-panel="custom_fields"', self.source)

    def test_the_tab_is_in_the_allowed_list(self):
        # A panel that no allowed-tabs entry names is unreachable: setAppAdminTab
        # falls back to the first allowed tab and the panel never activates.
        start = self.source.index("function allowedAppAdminTabs")
        block = self.source[start : start + 400]
        self.assertIn('"custom_fields"', block)

    def test_the_tab_is_gated_on_the_permission_its_routes_require(self):
        start = self.source.index("adminTabs: {")
        block = self.source[start : start + 2600]
        self.assertIn('custom_fields: ["collection.edit_all"]', block)

    def test_activating_the_tab_loads_the_definitions(self):
        start = self.source.index("function setAppAdminTab")
        block = self.source[start : start + 2200]
        self.assertIn('appAdmin.activeTab === "custom_fields"', block)
        self.assertIn("loadAppAdminCustomFields()", block)

    def test_every_action_has_a_handler(self):
        for attribute in (
            "data-custom-field-archive",
            "data-custom-field-rename",
            "data-custom-field-move",
        ):
            with self.subTest(attribute=attribute):
                self.assertIn(f'{attribute}="', self.source)
                self.assertIn(f'closest("[{attribute}]")', self.source)


class LabelsAreNotTranslatedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = _source()

    def test_a_field_name_is_rendered_raw(self):
        # If this ever becomes tNext(field.name), it looks up a key that cannot
        # exist and renders the fallback -- which is the name, so it "works"
        # until someone adds the missing keys and breaks it.
        start = self.source.index("function renderMovieEditCustomFields")
        block = self.source[start : start + 3200]
        self.assertIn("escapeHtml(field.name || field.key)", block)
        self.assertNotIn("tNext(field.name", block)

    def test_no_i18n_key_was_added_for_a_field_label(self):
        with open(os.path.join(I18N_DIR, "en-US.json"), encoding="utf-8") as handle:
            import json

            keys = set(json.load(handle))
        self.assertFalse([key for key in keys if key.startswith("customField.")])


class UnknownTypeAndRemovedOptionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = _source()

    def test_an_unknown_type_shows_as_itself(self):
        # This screen may outlive a server that knows a type it does not
        # (contract §4e.4). Blanking the label would hide a real field.
        start = self.source.index("function customFieldTypeLabel")
        block = self.source[start : start + 900]
        self.assertIn("return labels[fieldType] || fieldType;", block)

    def test_a_removed_option_is_injected_back_into_the_select(self):
        start = self.source.index("function renderMovieEditCustomFields")
        block = self.source[start : start + 3200]
        self.assertIn("const known = options.some(", block)
        self.assertIn("selected>${escapeHtml(String(value))}</option>", block)

    def test_an_unknown_type_falls_back_to_a_text_input(self):
        start = self.source.index("function renderMovieEditCustomFields")
        block = self.source[start : start + 3200]
        self.assertIn('field.fieldType === "date" ? "date" : "text"', block)


class EditFormTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = _source()

    def test_archived_fields_get_no_input(self):
        start = self.source.index("function renderMovieEditCustomFields")
        block = self.source[start : start + 3200]
        self.assertIn("filter((field) => !field.archivedAt)", block)

    def test_an_emptied_input_clears_rather_than_storing_a_blank(self):
        start = self.source.index("function collectMovieEditCustomValues")
        block = self.source[start : start + 900]
        self.assertIn("values[key] = null;", block)

    def test_values_are_saved_alongside_the_rest_of_the_edit(self):
        self.assertIn("await saveMovieCustomValues();", self.source)
        self.assertIn("/custom-values", self.source)

    def test_definitions_reach_the_spa_on_the_snapshot(self):
        # Like locations: available at first paint, so a field the owner added
        # renders without a deploy.
        start = self.source.index("function customFieldDefinitions")
        block = self.source[start : start + 400]
        self.assertIn("state?.customFields", block)


class SnapshotPublishesDefinitionsTests(unittest.TestCase):
    def test_the_dashboard_snapshot_carries_them_in_both_shapes(self):
        with open(os.path.join(BACKEND_DIR, "next_app.py"), encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn('"customFields": all_custom_field_entities(conn)', source)
        # And the empty snapshot, or a first paint with no collection would read
        # `undefined` where every other path reads a list.
        self.assertIn('"customFields": []', source)


if __name__ == "__main__":
    unittest.main()
