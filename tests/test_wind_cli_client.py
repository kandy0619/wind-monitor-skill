import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from wind_cli_client import WindCliError, call_wind, discover_runtime


class WindCliClientTests(unittest.TestCase):
    def _runtime(self, root: Path) -> Path:
        skill = root / ".agents" / "skills" / "wind-mcp-skill"
        (skill / "scripts").mkdir(parents=True)
        (skill / "SKILL.md").write_text("---\nname: wind-mcp-skill\n---\n", encoding="utf-8")
        (skill / "scripts" / "cli.mjs").write_text("// fixture\n", encoding="utf-8")
        return skill

    def test_project_local_runtime_has_priority(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            expected = self._runtime(project)
            with patch("wind_cli_client.shutil.which", return_value="node"):
                runtime = discover_runtime(project)
            self.assertEqual(runtime.root, expected.resolve())

    def test_project_run_does_not_fall_back_to_user_skill(self):
        with tempfile.TemporaryDirectory() as project_dir, tempfile.TemporaryDirectory() as profile_dir:
            profile = Path(profile_dir)
            global_skill = profile / ".codex" / "skills" / "wind-mcp-skill"
            (global_skill / "scripts").mkdir(parents=True)
            (global_skill / "SKILL.md").write_text("skill", encoding="utf-8")
            (global_skill / "scripts" / "cli.mjs").write_text("// fixture", encoding="utf-8")
            with patch.dict(os.environ, {"USERPROFILE": str(profile)}), patch("wind_cli_client.shutil.which", return_value="node"):
                with self.assertRaises(WindCliError) as raised:
                    discover_runtime(Path(project_dir))
            self.assertEqual(raised.exception.code, "wind_skill_missing")

    def test_request_file_is_unique_and_removed(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            skill = self._runtime(project)
            observed = {}

            def runner(command, **kwargs):
                request_arg = command[-1]
                request_path = skill / request_arg[1:]
                observed["name"] = request_path.name
                observed["params"] = json.loads(request_path.read_text(encoding="utf-8"))
                return subprocess.CompletedProcess(command, 0, stdout='{"data":{"ok":true}}', stderr="")

            with patch("wind_cli_client.shutil.which", return_value="node"):
                value = call_wind("stock_data", "tool", {"name": "中文"}, project_root=project, runner=runner)
            self.assertTrue(value["data"]["ok"])
            self.assertEqual(observed["params"], {"name": "中文"})
            self.assertRegex(observed["name"], r"^request-[0-9a-f]{32}\.json$")
            self.assertFalse((skill / "scripts" / observed["name"]).exists())

    def test_structured_error_is_classified_without_echoing_raw_streams(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            self._runtime(project)

            def runner(command, **kwargs):
                envelope = {"error": {"code": "AUTH_ERROR", "message": "not configured", "details": {"secret": "do-not-return"}}}
                return subprocess.CompletedProcess(command, 1, stdout=json.dumps(envelope), stderr="sensitive diagnostic")

            with patch("wind_cli_client.shutil.which", return_value="node"):
                with self.assertRaises(WindCliError) as raised:
                    call_wind("stock_data", "tool", {}, project_root=project, runner=runner)
            self.assertEqual(raised.exception.code, "AUTH_ERROR")
            serialized = json.dumps(raised.exception.as_dict())
            self.assertNotIn("do-not-return", serialized)
            self.assertNotIn("sensitive diagnostic", serialized)

    def test_timeout_removes_request_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            skill = self._runtime(project)

            def runner(command, **kwargs):
                raise subprocess.TimeoutExpired(command, 1)

            with patch("wind_cli_client.shutil.which", return_value="node"):
                with self.assertRaises(WindCliError) as raised:
                    call_wind("stock_data", "tool", {}, project_root=project, runner=runner)
            self.assertEqual(raised.exception.code, "wind_timeout")
            self.assertEqual(list((skill / "scripts").glob("request-*.json")), [])


if __name__ == "__main__":
    unittest.main()
