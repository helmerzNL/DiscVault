"""The Scheduled tasks admin tab: Jellyfin and Plex syncs on a timer (issue #812)."""

import os
import sys
import unittest

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

SOURCE_PATH = os.path.join(os.path.dirname(__file__), "..", "next_views_ui.py")


class ScheduledTasksUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(SOURCE_PATH, encoding="utf-8") as handle:
            cls.source = handle.read()

    def test_the_tab_and_its_panel_exist(self):
        self.assertIn('admin_tab("scheduled_tasks"', self.source)
        self.assertIn('id="appAdminPanelScheduledTasks"', self.source)
        self.assertIn('data-app-admin-panel="scheduled_tasks"', self.source)

    def test_the_tab_is_in_the_allowed_list(self):
        start = self.source.index("function allowedAppAdminTabs")
        self.assertIn('"scheduled_tasks"', self.source[start : start + 400])

    def test_the_tab_is_gated_on_the_permissions_its_routes_accept(self):
        start = self.source.index("adminTabs: {")
        self.assertIn(
            'scheduled_tasks: ["admin.backup", "digital_sources.sync"]',
            self.source[start : start + 3200],
        )

    def test_activating_the_tab_loads_the_schedule(self):
        start = self.source.index("function setAppAdminTab")
        block = self.source[start : start + 2600]
        self.assertIn('appAdmin.activeTab === "scheduled_tasks"', block)
        self.assertIn("refreshAppAdminSchedule()", block)

    def test_jellyfin_and_plex_each_have_a_full_set_of_controls(self):
        for key in ("Jellyfin", "Plex"):
            for suffix in ("Enabled", "Interval", "Watched", "Status"):
                with self.subTest(key=key, suffix=suffix):
                    self.assertIn(f'id="appAdminTasks{key}{suffix}"', self.source)

    def test_jellyfin_moved_out_of_the_backup_schedule(self):
        self.assertNotIn('id="appAdminScheduleJellyfinEnabled"', self.source)
        self.assertNotIn("appAdminScheduleJellyfin", self.source)

    def test_each_tab_saves_only_its_own_jobs(self):
        start = self.source.index("async function saveAppAdminSchedule(")
        backup_save = self.source[start : self.source.index("async function saveAppAdminScheduledTasks")]
        self.assertNotIn("jellyfin", backup_save)
        self.assertNotIn("plex", backup_save)
        tasks_start = self.source.index("async function saveAppAdminScheduledTasks")
        tasks_save = self.source[tasks_start : self.source.index("async function refreshAppAdminBackupStatus")]
        self.assertIn("jellyfin: section(", tasks_save)
        self.assertIn("plex: section(", tasks_save)
        self.assertNotIn("backup", tasks_save)

    def test_the_form_is_wired_to_its_handler(self):
        self.assertIn('getElementById("appAdminTasksForm")?.addEventListener("submit", saveAppAdminScheduledTasks)', self.source)


if __name__ == "__main__":
    unittest.main()
