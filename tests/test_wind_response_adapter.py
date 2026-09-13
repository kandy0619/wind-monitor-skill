import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
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

    def test_reads_current_wind_table_level_unit_mapping(self):
        embedded = {
            "data": {
                "columns": [
                    {"name": "最新交易日", "type": "string"},
                    {"name": "交易时间", "type": "string"},
                    {"name": "中文简称", "type": "string"},
                    {"name": "涨跌幅", "type": "string"},
                    {"name": "当日主力净流入额", "type": "string"},
                    {"name": "Wind代码", "type": "string"},
                ],
                "rows": [["20260904", "2026-09-04T15:30:58+08:00", "中芯国际", "-2.20", "-329121293", "688981.SH"]],
                "unit": {"当日主力净流入额": "元"},
            },
            "error": None,
        }
        raw = {"content": [{"type": "text", "text": __import__("json").dumps(embedded, ensure_ascii=False)}]}
        result = adapter.adapt_response(raw, "stock")
        self.assertEqual(result.records[0]["main_yuan"], -329_121_293)
        self.assertEqual(result.records[0]["trade_date"], "2026-09-04")

    def test_industry_summary_accepts_combined_five_plus_five_contract(self):
        raw = [
            {"行业名称": f"行业{index}", "主力净流入额(亿元)": 10 - index}
            for index in range(10)
        ]
        result = adapter.adapt_response(raw, "industry_summary")
        self.assertEqual(len(result.records), 10)

    def test_industry_stock_accepts_ten_industries_with_three_rows_each(self):
        raw = [
            {
                "Wind行业完整名称": f"行业{industry}",
                "Wind代码": f"{industry:03d}{rank:03d}.SZ",
                "证券简称": f"股票{industry}-{rank}",
                "当日主力净流入额(亿元)": 10 - rank,
                "涨跌幅": rank,
                "排名": rank,
            }
            for industry in range(10)
            for rank in range(1, 4)
        ]
        result = adapter.adapt_response(raw, "industry_stock")
        self.assertEqual(len(result.records), 30)

    def test_industry_stock_adapter_accepts_extra_rows_for_exact_workflow_filtering(self):
        raw = [
            {
                "Wind行业完整名称": f"行业{index // 3}",
                "Wind代码": f"{index:06d}.SZ",
                "证券简称": f"股票{index}",
                "当日主力净流入额(亿元)": 40 - index,
                "涨跌幅": index / 10,
            }
            for index in range(33)
        ]
        result = adapter.adapt_response(raw, "industry_stock")
        self.assertEqual(len(result.records), 33)

    def test_industry_stock_maps_current_wind_industry_detail_column(self):
        raw = [{
            "Wind代码": "688256.SH",
            "证券简称": "寒武纪",
            "所属WIND行业明细": "信息技术--半导体产品",
            "2026年9月4日主力净流入额(百万元)": 220.1464,
            "2026年9月4日涨跌幅(%)": -2.5446,
        }]
        result = adapter.adapt_response(raw, "industry_stock")
        self.assertEqual(result.records[0]["industry"], "信息技术--半导体产品")
        self.assertEqual(result.records[0]["main_yuan"], 220_146_400)

    def test_industry_stock_maps_wind_industry_detail_without_prefix(self):
        raw = [{
            "Wind代码": "688256.SH",
            "证券简称": "寒武纪",
            "WIND行业明细": "信息技术--半导体产品",
            "2026年9月7日主力净流入额(百万元)": 220.1464,
            "2026年9月7日涨跌幅(%)": -2.5446,
        }]
        result = adapter.adapt_response(raw, "industry_stock")
        self.assertEqual(result.records[0]["industry"], "信息技术--半导体产品")
        self.assertEqual(result.records[0]["main_yuan"], 220_146_400)

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

    def test_plain_text_no_data_envelope_is_not_treated_as_a_record(self):
        raw = {"content": [{"type": "text", "text": "没找到数据"}], "isError": False}
        with self.assertRaises(adapter.AdaptationError) as raised:
            adapter.adapt_response(raw, "industry_summary")
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

    def test_current_wind_nested_table_and_million_yuan_unit(self):
        embedded = {
            "data": {
                "data": [
                    {
                        "columns": [
                            {"name": "WIND行业"},
                            {"name": "2026年8月13日各行业主力净流入额", "unit": "百万元"},
                        ],
                        "rows": [["信息技术--软件", 123.45]],
                    }
                ]
            },
            "error": None,
        }
        raw = {"content": [{"type": "text", "text": __import__("json").dumps(embedded, ensure_ascii=False)}]}
        result = adapter.adapt_response(raw, "industry_daily_full")
        self.assertEqual(result.records[0]["industry"], "信息技术--软件")
        self.assertEqual(result.records[0]["net_yuan"], 123_450_000)

    def test_wind_million_renminbi_unit_alias(self):
        raw = {
            "data": [{
                "columns": [
                    {"name": "WIND行业"},
                    {"name": "2026年8月13日各行业主力净流入额", "unit": "百万人民币元"},
                ],
                "rows": [["信息技术--软件", 12.5]],
            }]
        }
        result = adapter.adapt_response(raw, "industry_daily_full")
        self.assertEqual(result.records[0]["net_yuan"], 12_500_000)

    def test_full_industry_profile_keeps_hundred_rows_for_reverse_union(self):
        raw = [
            {"行业": f"行业{index}", "主力净流入额(百万元)": index + 1}
            for index in range(100)
        ]
        result = adapter.adapt_response(raw, "industry_daily_full")
        self.assertEqual(len(result.records), 100)
        self.assertEqual(result.warnings[0]["code"], "possible_truncation")


if __name__ == "__main__":
    unittest.main()
