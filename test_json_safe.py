import json
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

from json_safe import json_safe


class JsonSafeLeafCoercionTests(unittest.TestCase):

    def test_date_becomes_iso_calendar_string(self):
        self.assertEqual(json_safe(date(2026, 9, 6)), "2026-09-06")

    def test_datetime_becomes_iso_timestamp_string(self):
        value = datetime(2026, 9, 6, 8, 41, 52, 933000, tzinfo=timezone.utc)
        self.assertEqual(json_safe(value), "2026-09-06T08:41:52.933000+00:00")

    def test_naive_datetime_is_preserved_without_offset(self):
        value = datetime(2026, 9, 6, 5, 30, 0)
        self.assertEqual(json_safe(value), "2026-09-06T05:30:00")

    def test_uuid_becomes_canonical_string(self):
        raw = UUID("1170a900-5a8c-47e1-b07e-404f28fd4a22")
        self.assertEqual(json_safe(raw), "1170a900-5a8c-47e1-b07e-404f28fd4a22")

    def test_integral_decimal_becomes_int(self):
        result = json_safe(Decimal("7"))
        self.assertEqual(result, 7)
        self.assertIsInstance(result, int)

    def test_fractional_decimal_becomes_float(self):
        result = json_safe(Decimal("64.4"))
        self.assertAlmostEqual(result, 64.4)
        self.assertIsInstance(result, float)

    def test_non_finite_decimal_becomes_none(self):
        self.assertIsNone(json_safe(Decimal("NaN")))
        self.assertIsNone(json_safe(Decimal("Infinity")))

    def test_primitives_pass_through_unchanged(self):
        for value in ["text", 5, 5.5, True, False, None]:
            self.assertEqual(json_safe(value), value)


class JsonSafeStructureTests(unittest.TestCase):

    def test_nested_dict_and_list_are_coerced_recursively(self):
        payload = {
            "plan_date": date(2026, 9, 6),
            "daily_coaching_summary": {
                "as_of": datetime(2026, 9, 6, 9, 31, 30, tzinfo=timezone.utc),
                "phases": [
                    {"start": date(2026, 8, 1), "rmssd": Decimal("41.2")},
                    {"start": date(2026, 8, 15), "rmssd": Decimal("52")},
                ],
            },
            "sleep_id": UUID("1170a900-5a8c-47e1-b07e-404f28fd4a22"),
        }

        safe = json_safe(payload)

        # Round-trips through the stdlib encoder psycopg's JSONB adapter uses.
        encoded = json.dumps(safe)
        restored = json.loads(encoded)

        self.assertEqual(restored["plan_date"], "2026-09-06")
        self.assertEqual(
            restored["daily_coaching_summary"]["as_of"],
            "2026-09-06T09:31:30+00:00",
        )
        self.assertEqual(
            restored["daily_coaching_summary"]["phases"][0]["start"],
            "2026-08-01",
        )
        self.assertEqual(
            restored["daily_coaching_summary"]["phases"][0]["rmssd"], 41.2
        )
        self.assertEqual(
            restored["daily_coaching_summary"]["phases"][1]["rmssd"], 52
        )
        self.assertEqual(
            restored["sleep_id"], "1170a900-5a8c-47e1-b07e-404f28fd4a22"
        )

    def test_tuples_become_lists(self):
        self.assertEqual(json_safe((date(2026, 9, 6), 2)), ["2026-09-06", 2])

    def test_unknown_type_is_not_silently_stringified(self):
        class Opaque:
            pass

        # json_safe leaves unknown objects untouched, so the real serializer
        # still raises instead of masking a programming mistake.
        with self.assertRaises(TypeError):
            json.dumps(json_safe({"x": Opaque()}))

    def test_original_payload_is_not_mutated(self):
        original = {"d": date(2026, 9, 6), "nested": {"d": date(2026, 9, 7)}}
        json_safe(original)
        self.assertIsInstance(original["d"], date)
        self.assertIsInstance(original["nested"]["d"], date)


if __name__ == "__main__":
    unittest.main()
