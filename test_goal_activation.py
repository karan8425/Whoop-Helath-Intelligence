"""Goal Setting V2 - preview never persists; activation persists the full
contract and starts the phase atomically without mutating prior phase rows."""

import os
import re
import unittest
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault(
    "TOKEN_ENCRYPTION_KEY",
    "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
)

import goals


CUR_SNAPSHOT = {
    "phase_start_weight_lb": 184.2,
    "phase_start_body_fat_percentage": 21.2,
    "phase_start_recorded_at": datetime(2026, 8, 30, tzinfo=timezone.utc),
}


class _Cursor:
    def __init__(self, sink):
        self.sink = sink

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.sink.append((re.sub(r"\s+", " ", sql).strip().lower(), params))

    def fetchone(self):
        # Echo an inserted row back for _serialize.
        last_sql, last_params = self.sink[-1]
        if last_sql.startswith("insert into health_goal_profiles"):
            return {
                "id": 42,
                "phase": last_params[0],
                "goal_type": last_params[1],
                "lean_mass_priority": last_params[2],
                "target_body_fat_percentage": last_params[3],
                "target_weight_lb": last_params[4],
                "target_fat_mass_lb": last_params[5],
                "target_lean_mass_lb": last_params[6],
                "daily_step_target": last_params[7],
                "strength_sessions_per_week": last_params[8],
                "protein_target_grams": last_params[9],
                "phase_start_weight_lb": last_params[10],
                "phase_start_body_fat_percentage": last_params[11],
                "phase_start_fat_mass_lb": last_params[12],
                "phase_start_lean_mass_lb": last_params[13],
                "phase_start_recorded_at": CUR_SNAPSHOT["phase_start_recorded_at"],
                "phase_start_date": date.fromisoformat(last_params[14]),
                "target_date": (date.fromisoformat(last_params[15])
                                if last_params[15] else None),
                "aspirational_target_date": (date.fromisoformat(last_params[16])
                                             if last_params[16] else None),
                "selected_pace": last_params[17],
                "expected_weekly_weight_change_lb": last_params[18],
                "timeline_status": last_params[19],
                "goal_version": 2,
                "is_active": True,
                "phase_end_date": None,
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }
        return None


class _Conn:
    def __init__(self, sink):
        self._sink = sink

    def cursor(self):
        return _Cursor(self._sink)


def _patch_db(sink):
    @contextmanager
    def factory():
        yield _Conn(sink)

    return factory


def _target(w=175.0, bf=15.0):
    return {"target_weight_lb": w, "target_body_fat_percentage": bf}


class PreviewDoesNotPersistTests(unittest.TestCase):

    def test_preview_runs_no_write(self):
        sink = []
        with patch.object(goals, "get_conn", _patch_db(sink)), \
             patch.object(goals, "_latest_hume_start_snapshot",
                          return_value=CUR_SNAPSHOT), \
             patch.object(goals, "init_goal_profiles"):
            out = goals.preview_goal({
                "goal_type": "lose_body_fat",
                "target": _target(),
                "lean_mass_priority": "preserve_build",
            })
        self.assertEqual(out["status"], "ok")
        self.assertIn("timeline_options", out)
        self.assertIn("compatibility", out)
        # no INSERT / UPDATE issued
        self.assertFalse(any(
            s.startswith(("insert", "update")) for s, _ in sink
        ))

    def test_preview_rejects_unknown_goal_type(self):
        with patch.object(goals, "_latest_hume_start_snapshot",
                          return_value=CUR_SNAPSHOT), \
             patch.object(goals, "init_goal_profiles"), \
             patch.object(goals, "get_conn", _patch_db([])):
            with self.assertRaises(ValueError):
                goals.preview_goal({"goal_type": "nope", "target": _target()})


class ActivationPersistsContractTests(unittest.TestCase):

    def _activate(self, payload):
        sink = []
        with patch.object(goals, "get_conn", _patch_db(sink)), \
             patch.object(goals, "_latest_hume_start_snapshot",
                          return_value=CUR_SNAPSHOT), \
             patch.object(goals, "init_goal_profiles"):
            out = goals.activate_goal(payload)
        return out, sink

    def test_activation_persists_full_contract(self):
        out, sink = self._activate({
            "goal_type": "lose_body_fat",
            "target": _target(),
            "lean_mass_priority": "preserve_build",
            "selected_pace": "recommended",
        })
        g = out["goal"]
        self.assertEqual(out["status"], "ok")
        self.assertEqual(g["goal_type"], "lose_body_fat")
        self.assertEqual(g["phase"], "lean_cut")
        self.assertEqual(g["lean_mass_priority"], "preserve_build")
        self.assertEqual(g["goal_version"], 2)
        self.assertEqual(g["selected_pace"], "recommended")
        self.assertEqual(g["timeline_status"], "configured")
        # snapshotted starting measurements
        self.assertEqual(g["phase_start_weight_lb"], 184.2)
        self.assertEqual(g["phase_start_body_fat_percentage"], 21.2)
        self.assertIsNotNone(g["phase_start_fat_mass_lb"])
        self.assertIsNotNone(g["phase_start_lean_mass_lb"])
        # target implied composition persisted
        self.assertIsNotNone(g["target_fat_mass_lb"])
        self.assertIsNotNone(g["target_lean_mass_lb"])
        # target date + expected rate persisted
        self.assertIsNotNone(g["target_date"])
        self.assertIsNotNone(g["expected_weekly_weight_change_lb"])

    def test_activation_closes_prior_phase_without_touching_its_snapshot(self):
        _, sink = self._activate({
            "goal_type": "lose_body_fat", "target": _target(),
            "selected_pace": "recommended",
        })
        updates = [s for s, _ in sink if s.startswith("update health_goal_profiles")]
        self.assertEqual(len(updates), 1)
        u = updates[0]
        self.assertIn("set is_active = false", u)
        self.assertIn("phase_end_date =", u)
        # the close-out UPDATE must not write any phase_start_* column
        self.assertNotIn("phase_start_weight_lb", u)
        self.assertNotIn("phase_start_body_fat_percentage", u)

    def test_phase_starts_only_on_activation(self):
        out, _ = self._activate({
            "goal_type": "lose_body_fat", "target": _target(),
            "selected_pace": "comfortable",
        })
        self.assertEqual(
            out["goal"]["phase_start_date"],
            datetime.now(timezone.utc).date().isoformat(),
        )
        self.assertTrue(out["goal"]["is_active"])

    def test_custom_date_inside_range_persists_as_custom(self):
        d = (date.today() + timedelta(days=300)).isoformat()
        out, _ = self._activate({
            "goal_type": "lose_body_fat", "target": _target(),
            "custom_target_date": d,
        })
        g = out["goal"]
        self.assertEqual(g["selected_pace"], "custom")
        self.assertEqual(g["target_date"], d)
        self.assertEqual(g["timeline_status"], "configured")
        self.assertIsNone(g["aspirational_target_date"])

    def test_custom_date_outside_range_clamps_but_keeps_aspiration(self):
        d = (date.today() + timedelta(days=21)).isoformat()   # far too fast
        out, _ = self._activate({
            "goal_type": "lose_body_fat", "target": _target(),
            "custom_target_date": d,
        })
        g = out["goal"]
        self.assertEqual(g["timeline_status"], "outside_supported_range")
        self.assertEqual(g["aspirational_target_date"], d)
        # actionable target_date is the recommended one, NOT the aspirational
        self.assertNotEqual(g["target_date"], d)
        self.assertIsNotNone(g["target_date"])
        self.assertEqual(g["selected_pace"], "recommended")

    def test_activation_requires_a_current_weight(self):
        with patch.object(goals, "get_conn", _patch_db([])), \
             patch.object(goals, "_latest_hume_start_snapshot",
                          return_value={"phase_start_weight_lb": None,
                                        "phase_start_body_fat_percentage": None,
                                        "phase_start_recorded_at": None}), \
             patch.object(goals, "init_goal_profiles"):
            with self.assertRaises(ValueError):
                goals.activate_goal({"goal_type": "lose_body_fat",
                                     "target": _target()})

    def test_default_pace_is_the_recommended_band(self):
        out, _ = self._activate({
            "goal_type": "lose_body_fat", "target": _target(),
            "lean_mass_priority": "preserve_build",
        })
        # preserve_build -> recommended band is "comfortable"
        self.assertEqual(out["goal"]["selected_pace"], "comfortable")
        self.assertEqual(out["goal"]["timeline_status"], "configured")


class SchemaMigrationTests(unittest.TestCase):

    def test_init_adds_v2_columns_idempotently(self):
        sink = []
        with patch.object(goals, "get_conn", _patch_db(sink)):
            goals.init_goal_profiles()
        joined = " ".join(s for s, _ in sink)
        for col, _type in goals._V2_COLUMNS:
            self.assertIn(f"add column if not exists {col}", joined)


class GoalChangeCacheInvalidationTests(unittest.TestCase):
    """Goal activation must drop the goal-dependent Today plan cache
    (V2.1 stale-state fix). Goal Progress is computed fresh per request."""

    def _activate(self, payload, *, invalidate_ok=True):
        sink = []
        calls = {"n": 0}
        import todays_plan_store as tps

        def fake_invalidate():
            calls["n"] += 1
            if not invalidate_ok:
                raise RuntimeError("cache down")
            return "2026-09-07"

        with patch.object(goals, "get_conn", _patch_db(sink)), \
             patch.object(goals, "_latest_hume_start_snapshot",
                          return_value=CUR_SNAPSHOT), \
             patch.object(goals, "init_goal_profiles"), \
             patch.object(tps, "invalidate_todays_plan", fake_invalidate):
            out = goals.activate_goal(payload)
        return out, calls["n"]

    def test_activation_invalidates_todays_plan_cache(self):
        out, n = self._activate({
            "goal_type": "lose_body_fat", "target": _target(180.0, 18.0),
            "selected_pace": "recommended",
        })
        self.assertEqual(n, 1)
        self.assertTrue(out["cache"]["todays_plan_invalidated"])
        self.assertEqual(out["status"], "ok")

    def test_cache_failure_does_not_fail_activation(self):
        out, n = self._activate({
            "goal_type": "lose_body_fat", "target": _target(180.0, 18.0),
            "selected_pace": "recommended",
        }, invalidate_ok=False)
        self.assertEqual(n, 1)
        self.assertEqual(out["status"], "ok")          # activation still valid
        self.assertFalse(out["cache"]["todays_plan_invalidated"])
        self.assertIsNotNone(out["goal"]["target_date"])

    def test_activation_failure_does_not_invalidate_cache(self):
        # bad target_body_fat -> ValueError before any write / cache call
        import todays_plan_store as tps
        calls = {"n": 0}
        with patch.object(goals, "get_conn", _patch_db([])), \
             patch.object(goals, "_latest_hume_start_snapshot",
                          return_value=CUR_SNAPSHOT), \
             patch.object(goals, "init_goal_profiles"), \
             patch.object(tps, "invalidate_todays_plan",
                          lambda: calls.__setitem__("n", calls["n"] + 1)):
            with self.assertRaises(ValueError):
                goals.activate_goal({
                    "goal_type": "lose_body_fat",
                    "target": _target(180.0, 200.0),   # invalid body fat
                })
        self.assertEqual(calls["n"], 0)


class ActivationRegressionTests(unittest.TestCase):
    """Item 13 - the exact stale-state repro, at the persistence boundary."""

    def test_new_target_replaces_old_immediately(self):
        sink = []
        import todays_plan_store as tps
        with patch.object(goals, "get_conn", _patch_db(sink)), \
             patch.object(goals, "_latest_hume_start_snapshot",
                          return_value=CUR_SNAPSHOT), \
             patch.object(goals, "init_goal_profiles"), \
             patch.object(tps, "invalidate_todays_plan", lambda: None):
            out = goals.activate_goal({
                "goal_type": "lose_body_fat",
                "target": _target(180.0, 18.0),          # was 15%
                "lean_mass_priority": "balanced",
                "selected_pace": "recommended",
            })
        g = out["goal"]
        # The persisted, authoritative contract carries the NEW targets.
        self.assertEqual(g["target_weight_lb"], 180.0)
        self.assertEqual(g["target_body_fat_percentage"], 18.0)
        self.assertNotEqual(g["target_body_fat_percentage"], 15.0)
        self.assertTrue(g["is_active"])
        self.assertEqual(g["goal_version"], 2)
        # prior phase was closed, not mutated
        updates = [s for s, _ in sink if s.startswith("update health_goal_profiles")]
        self.assertEqual(len(updates), 1)
        self.assertIn("set is_active = false", updates[0])


if __name__ == "__main__":
    unittest.main()
