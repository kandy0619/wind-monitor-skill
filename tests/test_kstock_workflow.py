import sys
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from kstock_workflow import facts_from_normalized, persist_and_deliver, resume_report


class FakeClient:
    def __init__(self, dispatch_success=True):
        self.calls = []
        self.dispatch_success = dispatch_success

    def commit_facts(self, run_id, payload):
        self.calls.append(("facts", run_id, payload))
        return {"success": True}

    def commit_report(self, run_id, payload):
        self.calls.append(("report", run_id, payload))
        return {"report_id": payload["report_id"]}

    def commit_card(self, report_id, card, **kwargs):
        self.calls.append(("card", report_id, card, kwargs))
        return {"delivery": {"status": "pending_send"}}

    def dispatch_feishu(self, report_id, part_index):
        self.calls.append(("dispatch", report_id, part_index))
        return {"success": self.dispatch_success}

    def complete_run(self, run_id, *, with_limits=False):
        self.calls.append(("complete", run_id, with_limits))
        return {"run": {"status": "completed_with_limits" if with_limits else "completed"}}

    def mark_failure(self, run_id, stage, error_code, detail_redacted=None):
        self.calls.append(("fail", run_id, stage, error_code, detail_redacted))
        return {"run": {"status": f"pending_{stage}"}}


def intraday_payload():
    return {
        "trade_date": "2026-08-13",
        "planned_time": "15:00",
        "report_type": "intraday",
        "wind_data_time": "2026-08-13T15:00:00+08:00",
        "stocks": [["中芯国际", "688981.SH", 1.0, 100, 2.0, 1, 2, 3, 4]],
        "indexes": [],
        "industry_inflow_top5": [],
        "industry_outflow_top5": [],
    }


def test_intraday_pipeline_persists_before_delivery_and_completion():
    client = FakeClient()
    bundle = {
        "run_id": 7,
        "report_id": "r-1500",
        "report_type": "intraday",
        "normalized_payload": intraday_payload(),
        "calculated_payload": {"period_delta_yuan": 1},
        "recipient_config_version": "v1",
    }
    card = {"header": {}, "elements": [{"tag": "div"}]}
    with patch("kstock_workflow.render_card", return_value=card) as renderer:
        result = persist_and_deliver(client, bundle)
    assert result["status"] == "completed"
    assert [call[0] for call in client.calls] == ["facts", "report", "card", "dispatch", "complete"]
    renderer.assert_called_once_with(bundle["normalized_payload"], None)


def test_delivery_failure_never_marks_run_complete():
    client = FakeClient(dispatch_success=False)
    bundle = {
        "run_id": 8,
        "report_id": "r-1500-fail",
        "report_type": "intraday",
        "normalized_payload": intraday_payload(),
    }
    with patch("kstock_workflow.render_card", return_value={"header": {}, "elements": [{"tag": "div"}]}):
        try:
            persist_and_deliver(client, bundle)
        except Exception:
            pass
        else:
            raise AssertionError("delivery failure must propagate")
    assert [call[0] for call in client.calls] == ["facts", "report", "card", "dispatch"]


def test_fact_conversion_uses_normalized_values():
    facts = facts_from_normalized("intraday", intraday_payload())
    assert facts["observations"][0]["entity_code"] == "688981.SH"
    assert facts["observations"][0]["main_net_inflow_yuan"] == 100


def test_render_failure_is_persisted_for_stateless_retry():
    client = FakeClient()
    bundle = {
        "run_id": 9, "report_id": "r-render-fail", "report_type": "intraday",
        "normalized_payload": intraday_payload(),
    }
    with patch("kstock_workflow.render_card", side_effect=ValueError("bad card")):
        try:
            persist_and_deliver(client, bundle)
        except ValueError:
            pass
        else:
            raise AssertionError("render failure must propagate")
    assert [call[0] for call in client.calls] == ["facts", "report", "fail"]
    assert client.calls[-1][2:4] == ("render", "card_render_failed")


def test_trend_sample_is_converted_without_a_local_state_file():
    facts = facts_from_normalized("trend_sample", {
        "trade_date": "2026-08-13", "planned_time": "11:15",
        "wind_time": "2026-08-13T11:15:00+08:00",
        "boards": {"沪深300": {"stocks": [[1, "000001.SZ", "平安银行", 1.25, 0.8, 3.1]]}},
    })
    assert facts["observations"][0]["entity_code"] == "000001.SZ"
    assert facts["observations"][0]["main_net_inflow_yuan"] == "125000000"


def test_resume_renders_from_persisted_report_without_wind_or_fact_commit():
    client = FakeClient()
    client.run = lambda run_id: {
        "run": {
            "id": run_id, "task_id": 1, "trade_date": "2026-08-13",
            "planned_time": "15:00", "status": "pending_render",
        },
        "report": {
            "report_id": "resume-1500", "report_type": "intraday",
            "normalized_payload": intraday_payload(), "deliveries": [],
            "quality_status": "complete",
        },
    }
    client.previous_report = lambda *_args: None
    client.context = lambda: {"recipient_config_version": "v1"}
    card = {"header": {}, "elements": [{"tag": "div"}]}
    with patch("kstock_workflow.render_card", return_value=card):
        result = resume_report(client, 12)
    assert result["status"] == "completed"
    assert [call[0] for call in client.calls] == ["card", "dispatch", "complete"]
