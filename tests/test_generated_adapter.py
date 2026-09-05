import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


generated = load_module("validate_generated_adapter", SCRIPTS / "validate_generated_adapter.py")


class GeneratedAdapterTest(unittest.TestCase):
    def test_generated_code_can_only_propose_mapping(self):
        source = '''
def adapt(raw):
    return {
        "profile": "industry_summary",
        "fields": {
            "industry": {"source_key": "领域"},
            "net_yuan": {"source_key": "资金差", "unit": "亿元"}
        }
    }
'''
        result = generated.validate_generated_adapter(source, [{"领域": "软件", "资金差": 1.2}], "industry_summary")
        self.assertTrue(result["generated_adapter_validated"])
        self.assertEqual(result["records"][0]["net_yuan"], 120_000_000)

    def test_file_access_is_rejected(self):
        source = '''
def adapt(raw):
    return open("secret").read()
'''
        with self.assertRaises(ValueError):
            generated.validate_source(source)

    def test_import_is_rejected(self):
        source = '''
import os
def adapt(raw):
    return {}
'''
        with self.assertRaises(ValueError):
            generated.validate_source(source)


if __name__ == "__main__":
    unittest.main()
