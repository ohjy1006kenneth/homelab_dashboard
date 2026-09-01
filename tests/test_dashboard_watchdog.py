import importlib.util
import io
import subprocess
import unittest
from pathlib import Path
from contextlib import redirect_stdout
from unittest.mock import patch


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "dashboard_watchdog.py"
SPEC = importlib.util.spec_from_file_location("dashboard_watchdog", MODULE_PATH)
assert SPEC and SPEC.loader
watchdog = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(watchdog)


class DashboardWatchdogRedirectTest(unittest.TestCase):
    def test_same_location_with_non_302_status_is_reported(self):
        expected = "http://192.168.1.29:8080/"
        app = {
            "id": "demo",
            "web_ui_port": 8080,
            "web_ui_path": "/",
            "web_ui_url": expected,
            "open_url": "/api/apps/demo/open",
        }
        responses = {
            f"{watchdog.DASHBOARD}/api/health": {"ok": True},
            f"{watchdog.DASHBOARD}/api/apps": [app],
        }

        def fetch_json(url, host=None):
            return responses[url]

        def fetch_text(url):
            return '<script src="/static/app.js"></script>Loading dashboard'

        def run(cmd, timeout=10):
            if cmd[0] == "curl":
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    "HTTP/1.1 307 Temporary Redirect\r\nLocation: " + expected + "\r\n\r\n",
                    "",
                )
            return subprocess.CompletedProcess(cmd, 0, "", "")

        module_check = subprocess.CompletedProcess([], 0, "", "")
        with patch.object(watchdog, "fetch_json", side_effect=fetch_json), patch.object(
            watchdog, "fetch_text", side_effect=fetch_text
        ), patch.object(watchdog, "run", side_effect=run), patch.object(
            watchdog.subprocess, "run", return_value=module_check
        ), patch.object(watchdog, "PROJECT_DIR", MODULE_PATH.parents[1]):
            # The module check is mocked; the redirect response remains deterministic.
            output = io.StringIO()
            with redirect_stdout(output):
                result = watchdog.main()

        self.assertEqual(result, 1)
        self.assertIn("demo", output.getvalue())
        self.assertIn("307", output.getvalue())


class DashboardWatchdogProjectRootTest(unittest.TestCase):
    def test_repo_script_path_is_selected_when_valid(self):
        with patch.object(watchdog, "PROJECT_DIR", MODULE_PATH.parents[1]), patch.object(
            watchdog.Path, "cwd", return_value=Path("/tmp")
        ):
            self.assertEqual(watchdog.resolve_project_dir(), MODULE_PATH.parents[1])

    def test_valid_cwd_is_selected_for_copied_script(self):
        with patch.object(watchdog, "PROJECT_DIR", Path("/tmp/profile")), patch.object(
            watchdog.Path, "cwd", return_value=MODULE_PATH.parents[1]
        ):
            self.assertEqual(watchdog.resolve_project_dir(), MODULE_PATH.parents[1])

    def test_invalid_script_and_cwd_have_actionable_failure(self):
        with patch.object(watchdog, "PROJECT_DIR", Path("/tmp/profile")), patch.object(
            watchdog.Path, "cwd", return_value=Path("/tmp/work")
        ):
            self.assertIsNone(watchdog.resolve_project_dir())

        def fetch_json(url, host=None):
            return {"ok": True} if url.endswith("/api/health") else []

        with patch.object(watchdog, "fetch_json", side_effect=fetch_json), patch.object(
            watchdog, "fetch_text", return_value='<script src="/static/app.js"></script>Loading dashboard'
        ), patch.object(watchdog, "run", return_value=subprocess.CompletedProcess([], 0, "", "")), patch.object(
            watchdog, "PROJECT_DIR", Path("/tmp/profile")
        ), patch.object(watchdog.Path, "cwd", return_value=Path("/tmp/work")):
            output = io.StringIO()
            with redirect_stdout(output):
                result = watchdog.main()

        self.assertEqual(result, 1)
        self.assertIn("dashboard project root", output.getvalue())


if __name__ == "__main__":
    unittest.main()