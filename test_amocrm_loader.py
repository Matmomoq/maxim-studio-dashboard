import unittest
from datetime import datetime

from amocrm_loader import (
    first_field_value,
    is_message_type,
    missing_entity_ids,
    normalize_phone,
    normalized_call_status,
    sanitize_call_payload,
    safe_date,
    safe_transition_date,
)


class LoaderHelpersTest(unittest.TestCase):
    def test_phone_normalization(self):
        self.assertEqual(normalize_phone("8 (999) 123-45-67"), "+79991234567")
        self.assertEqual(normalize_phone("9991234567"), "+79991234567")
        self.assertIsNone(normalize_phone(""))

    def test_call_status(self):
        self.assertEqual(normalized_call_status(4, None, 0), "accepted")
        self.assertEqual(normalized_call_status(2, None, 30), "missed")
        self.assertEqual(
            normalized_call_status(None, "Не дозвонились", 10), "missed"
        )
        self.assertEqual(normalized_call_status(None, None, 5), "accepted")

    def test_message_exclusion(self):
        self.assertTrue(is_message_type("incoming_chat_message"))
        self.assertTrue(is_message_type("outgoing_sms"))
        self.assertFalse(is_message_type("lead_status_changed"))

    def test_call_links_removed_recursively(self):
        payload = {
            "params": {
                "link": "https://example.test/record",
                "recording_url": "https://example.test/audio",
                "duration": 15,
            },
            "_links": {"self": "https://example.test/note"},
        }
        clean = sanitize_call_payload(payload)
        self.assertEqual(clean, {"params": {"duration": 15}})

    def test_first_field_value(self):
        fields = [
            {"field_id": 10, "values": [{"value": "x"}]},
            {"field_id": 20, "values": [{"value": "y"}]},
        ]
        self.assertEqual(first_field_value(fields, 20), "y")
        self.assertIsNone(first_field_value(fields, 30))

    def test_unix_date_is_interpreted_in_moscow_timezone(self):
        self.assertEqual(str(safe_date(1785704400)), "2026-08-03")
        self.assertEqual(str(safe_date("1785618000")), "2026-08-02")

    def test_transition_date_cannot_precede_lead_creation(self):
        result = safe_transition_date(
            "2026-07-02",
            datetime(2026, 7, 3, 12, 0, 0),
        )
        self.assertEqual(str(result), "2026-07-03")

    def test_missing_entity_ids_detects_merged_or_deleted_leads(self):
        self.assertEqual(
            missing_entity_ids(
                [37164301, 37237047, 37244835, 37426719],
                [{"id": 37426719}],
            ),
            [37164301, 37237047, 37244835],
        )

    def test_missing_entity_ids_ignores_order_and_duplicates(self):
        self.assertEqual(
            missing_entity_ids(
                [3, 1, 2, 2],
                [{"id": 2}, {"id": "3"}, {"id": 1}],
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
