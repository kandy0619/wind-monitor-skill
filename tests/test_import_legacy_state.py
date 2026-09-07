import json
import sys
import tempfile
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from import_legacy_state import discover_records, import_records


class FakeClient:
    def __init__(self):
        self.calls = []
        self.next_run = 1

    def claim_run(self, payload):
        self.calls.append(("claim", payload))
        run_id = self.next_run
        self.next_run += 1
        return {"created": True, "run": {"id": run_id, "status": "collecting"}}

    def record_attempt(self, run_id, payload):
        self.calls.append(("attempt", run_id, payload))
        return {"attempt_id": run_id}

    def commit_facts(self, run_id, payload):
        self.calls.append(("facts", run_id, payload))
        return {"observations": len(payload["observations"]), "rankings": len(payload["rankings"])}

    def complete_run(self, run_id, *, with_limits=False):
        self.calls.append(("complete", run_id, with_limits))
        return {"run": {"status": "completed_with_limits"}}


def test_discover_and_import_never_dispatches_feishu():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        normalized = root / "a-share-monitor-normalized"
        normalized.mkdir()
        (normalized / "20260813-1500.json").write_text(json.dumps({
            "trade_date": "2026-08-13", "planned_time": "15:00",
            "wind_data_time": "2026-08-13T15:00:00+08:00",
            "stocks": [["中芯国际", "688981.SH", 1, 100, 2, 1, 2, 3, 4]],
            "indexes": [], "industry_inflow_top5": [], "industry_outflow_top5": [],
        }, ensure_ascii=False), encoding="utf-8")
        records = discover_records(root)
    assert len(records) == 1
    client = FakeClient()
    summary = import_records(client, 3, records)
    assert summary == {"imported": 1, "skipped": 0, "observations": 1, "rankings": 0}
    assert [call[0] for call in client.calls] == ["claim", "attempt", "facts", "complete"]
    assert client.calls[0][1]["run_kind"] == "legacy_import"
    assert client.calls[2][2]["quality_status"] == "raw_missing"


def test_flat_legacy_samples_are_grouped_by_slot():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        samples = root / "a-share-close-main-add-samples"
        samples.mkdir()
        (samples / "20260812.json").write_text(json.dumps({
            "trade_date": "2026-08-12",
            "samples": [
                {"planned_time": "10:00", "wind_time": "2026-08-12T10:00:01+08:00", "board": "沪市主板", "rank": 1, "windcode": "600001.SH", "name": "甲", "main_net_raw": 1, "main_net_unit": "亿元", "change_pct": 1, "main_ratio_pct": 2},
                {"planned_time": "10:00", "wind_time": "2026-08-12T10:00:02+08:00", "source_board": "深市主板", "board_rank": 1, "wind_code": "000001.SZ", "name": "乙", "main_net_inflow_yuan": 200000000, "unit": "元", "change_pct": 2, "main_inflow_ratio": 3},
            ],
        }, ensure_ascii=False), encoding="utf-8")
        records = discover_records(root)
    assert len(records) == 1
    assert records[0].planned_time == "10:00"
    assert len(records[0].facts["observations"]) == 2
    assert records[0].facts["observations"][1]["main_net_inflow_yuan"] == "200000000"


def test_import_normalizes_legacy_wind_time_range_for_mysql():
    record = type("Record", (), {
        "trade_date": "2026-08-12",
        "planned_time": "10:30",
        "mode": "intraday",
        "report_type": "intraday",
        "wind_data_time": "2026-08-12 10:32—10:33",
        "source_name": "20260812-1030.json",
        "facts": {"observations": [], "rankings": []},
    })()
    client = FakeClient()
    import_records(client, 3, [record])
    facts_call = next(call for call in client.calls if call[0] == "facts")
    assert facts_call[2]["wind_data_time"] == "2026-08-12T10:32:00"
