import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


adapter = load_module("wind_response_adapter", ROOT / "scripts/wind_response_adapter.py")


class WindResponseAdapterTest(unittest.TestCase):
    def test_unwraps_cli_content_and_normalizes_units(self):
        embedded = {
            "data": {
                "columns": ["行业名称", "主力净流入额(亿元)", "主力流入额(亿元)", "主力流出额(亿元)"],
                "rows": [["信息技术--软件", 1.25, 3.0, 1.75]],
            }
        }
        raw = {"content": [{"type": "text", "text": __import__("json").dumps(embedded, ensure_ascii=False)}]}
        result = adapter.adapt_response(raw, "industry_summary")
        row = result.records[0]
        self.assertEqual(row["industry"], "信息技术--软件")
        self.assertEqual(row["net_yuan"], 125_000_000)
        self.assertEqual(row["gross_inflow_yuan"], 300_000_000)

    def test_model_candidate_maps_unknown_label_but_not_values(self):
        raw = [{"领域": "半导体", "资金差": 2.5}]
        candidate = {
            "profile": "industry_summary",
            "fields": {
                "industry": {"source_key": "领域"},
                "net_yuan": {"source_key": "资金差", "unit": "亿元"},
            },
        }
        result = adapter.adapt_response(raw, "industry_summary", candidate)
        self.assertEqual(result.adapter_mode, "llm_fallback")
        self.assertEqual(result.records[0]["net_yuan"], 250_000_000)
        self.assertEqual(result.provenance[0]["fields"]["net_yuan"]["raw_value"], 2.5)

    def test_missing_amount_unit_is_not_guessed(self):
        raw = [{"行业": "半导体", "净额": 2.5}]
        with self.assertRaises(adapter.AdaptationError) as raised:
            adapter.adapt_response(raw, "industry_summary")
        self.assertEqual(raised.exception.code, "unit_ambiguous")

    def test_raw_persistence_redacts_secrets(self):
        raw = {"data": [{"行业": "软件"}], "WIND_API_KEY": "secret", "feishu_chat_id": "recipient"}
        with tempfile.TemporaryDirectory() as directory:
            path = adapter.persist_raw_response(
                raw, Path(directory), trade_date="2026-08-13", slot="15:10", request_id="industry"
            )
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("secret", text)
            self.assertNotIn("recipient", text)
            self.assertIn("***REDACTED***", text)

    def test_fallback_request_distinguishes_parse_failure(self):
        raw = [{"未知列": "值"}]
        try:
            adapter.adapt_response(raw, "industry_summary")
        except adapter.AdaptationError as error:
            request = adapter.fallback_request(raw, "industry_summary", error)
        self.assertEqual(request["request_type"], "wind_mapping_fallback")
        self.assertEqual(request["failure"]["code"], "field_missing")
        self.assertIn("未知列", request["available_fields"])

    def test_explicit_empty_result_is_no_data(self):
        with self.assertRaises(adapter.AdaptationError) as raised:
            adapter.adapt_response({"data": []}, "industry_summary")
        self.assertEqual(raised.exception.code, "no_data")

    def test_model_candidate_can_identify_new_record_path(self):
        raw = {"payload": {"blocks": [[{"领域": "软件", "资金差": 1.0}]]}}
        candidate = {
            "profile": "industry_summary",
            "record_path": ["payload", "blocks", 0],
            "fields": {
                "industry": {"source_key": "领域"},
                "net_yuan": {"source_key": "资金差", "unit": "亿元"},
            },
        }
        result = adapter.adapt_response(raw, "industry_summary", candidate)
        self.assertEqual(result.records[0]["net_yuan"], 100_000_000)

    def test_exactly_one_hundred_rows_is_classified_as_truncated(self):
        raw = [
            {"行业": f"行业{index}", "净额(元)": index + 1}
            for index in range(100)
        ]
        with self.assertRaises(adapter.AdaptationError) as raised:
            adapter.adapt_response(raw, "industry_summary")
        self.assertEqual(raised.exception.code, "truncated")


if __name__ == "__main__":
    unittest.main()
