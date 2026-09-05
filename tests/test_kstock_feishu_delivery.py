import logging
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from kstock_feishu_delivery import (
    DeliveryError,
    PREFERRED_TASK_NAME,
    _RecipientRedactionFilter,
    select_chat_task,
)


def task(name, chat_id, enabled=True):
    return SimpleNamespace(name=name, feishu_chat_id=chat_id, enabled=enabled)


class KStockFeishuDeliveryTests(unittest.TestCase):
    def test_preferred_task_wins(self):
        selected = select_chat_task([task("other", "chat-a"), task(PREFERRED_TASK_NAME, "chat-b")])
        self.assertEqual(selected.name, PREFERRED_TASK_NAME)

    def test_same_chat_across_tasks_is_unambiguous(self):
        selected = select_chat_task([task("a", "same"), task("b", "same")])
        self.assertEqual(selected.feishu_chat_id, "same")

    def test_multiple_chats_without_preferred_task_fail_closed(self):
        with self.assertRaises(DeliveryError) as raised:
            select_chat_task([task("a", "one"), task("b", "two")])
        self.assertEqual(raised.exception.code, "recipient_ambiguous")

    def test_disabled_and_empty_tasks_are_ignored(self):
        with self.assertRaises(DeliveryError) as raised:
            select_chat_task([task("a", "secret", False), task("b", "")])
        self.assertEqual(raised.exception.code, "recipient_missing")

    def test_backend_success_log_redacts_recipient(self):
        record = logging.LogRecord("test", logging.INFO, __file__, 1, "sent receive_id=%s type=%s", ("secret-chat", "interactive"), None)
        self.assertTrue(_RecipientRedactionFilter().filter(record))
        self.assertNotIn("secret-chat", record.getMessage())
        self.assertIn("<redacted>", record.getMessage())


if __name__ == "__main__":
    unittest.main()
