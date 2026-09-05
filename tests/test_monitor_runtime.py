import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


runtime = load_module("monitor_runtime", ROOT / "scripts/monitor_runtime.py")


class MonitorRuntimeTest(unittest.TestCase):
    def at(self, hour, minute):
        return datetime(2026, 8, 13, hour, minute, tzinfo=runtime.TIMEZONE)

    def test_intraday_frequency_and_close_are_separate(self):
        self.assertEqual([task.mode for task in runtime.tasks_for_trigger("15:00")], ["intraday"])
        self.assertEqual([task.mode for task in runtime.tasks_for_trigger("15:10")], ["close"])
        self.assertEqual(len(runtime.TRIGGER_TIMES), 30)

    def test_overlapping_intraday_and_sample_slot_runs_both(self):
        self.assertEqual(
            [task.mode for task in runtime.tasks_for_trigger("14:30")],
            ["intraday", "trend_sample"],
        )

    def test_completed_1500_does_not_suppress_1510(self):
        manifest = runtime.empty_manifest("2026-08-13")
        manifest["slots"]["15:00:intraday"] = {
            "planned_time": "15:00", "mode": "intraday", "status": "completed"
        }
        plan = runtime.plan_poll(self.at(15, 10), manifest)
        self.assertEqual(plan["tasks"], [{"key": "15:10:close", "planned_time": "15:10", "mode": "close"}])

    def test_pending_is_retried_before_current_slot(self):
        manifest = runtime.empty_manifest("2026-08-13")
        manifest["slots"]["14:50:intraday"] = {
            "planned_time": "14:50", "mode": "intraday", "status": "pending_send"
        }
        keys = [task["key"] for task in runtime.plan_poll(self.at(15, 0), manifest)["tasks"]]
        self.assertEqual(keys, ["14:50:intraday", "15:00:intraday"])

    def test_invalid_time_does_not_create_an_untriggered_historical_slot(self):
        manifest = runtime.empty_manifest("2026-08-13")
        plan = runtime.plan_poll(self.at(12, 0), manifest)
        self.assertEqual(plan["action"], "silent")
        self.assertEqual(plan["tasks"], [])

    def test_invalid_time_does_not_retry_pending_slot(self):
        manifest = runtime.empty_manifest("2026-08-13")
        manifest["slots"]["11:30:intraday"] = {
            "planned_time": "11:30", "mode": "intraday", "status": "pending_fetch"
        }
        plan = runtime.plan_poll(self.at(12, 0), manifest)
        self.assertEqual(plan["action"], "silent")
        self.assertEqual(plan["reason"], "invalid_time")

    def test_migrates_both_historical_state_shapes(self):
        nested = {
            "trade_date": "2026-08-12",
            "slots": {"15:00": {"intraday": {"completed": True, "planned_time": "15:00", "mode": "intraday"}}},
        }
        flat = {
            "15:10:close": {"planned_time": "15:10", "mode": "close", "completed_at": "now"},
            "pending": {},
        }
        self.assertEqual(runtime.migrate_manifest(nested, "2026-08-12")["slots"]["15:00:intraday"]["status"], "completed")
        self.assertEqual(runtime.migrate_manifest(flat, "2026-08-13")["slots"]["15:10:close"]["status"], "completed")

    def test_state_writes_are_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            store = runtime.SlotStateStore(path, "2026-08-13")
            task = runtime.SlotTask("15:00", "intraday")
            store.begin(task, self.at(15, 0))
            store.complete(task, self.at(15, 5), wind_data_time="15:00", delivery="feishu_success")
            first = json.loads(path.read_text(encoding="utf-8"))
            store.begin(task, self.at(15, 6))
            second = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
