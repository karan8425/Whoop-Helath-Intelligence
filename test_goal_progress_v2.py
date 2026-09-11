"""Goal Progress V2 - longitudinal intelligence contract + rules.

Covers:
  - source hierarchy / fallbacks (Hume preferred, Fitdays historical, WHOOP
    physiology, Apple steps, Tonal strength, typed unavailable states)
  - phase progress vs historical context separation (phase start never
    truncates history; phase change uses only phase-period data)
  - deterministic goal-intelligence scenarios
  - backward compatibility (every V1 key preserved)
  - JSON serializability
"""

import json
import os
import unittest
from datetime import date, timedelta
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault(
    "TOKEN_ENCRYPTION_KEY",
    "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
)

import goal_progress_v2 as v2


TODAY = date.today()


def _iso(d):
    return d.isoformat()


def _lin_series(days, *, start, per_day, end=None):
    end = end or (TODAY - timedelta(days=1))
    return [
        {"date": _iso(end - timedelta(days=days - 1 - i)),
         "value": round(start + per_day * i, 3)}
        for i in range(days)
    ]


def _goal(*, phase_start_days_ago=20, phase_end=None, **overrides):
    g = {
        "id": 7,
        "phase": "lean_cut",
        "phase_start_date": _iso(TODAY - timedelta(days=phase_start_days_ago)),
        "phase_end_date": phase_end,
        "phase_start_weight_lb": 190.0,
        "phase_start_body_fat_percentage": 22.0,
        "target_weight_lb": 178.0,
        "target_body_fat_percentage": 15.0,
        "daily_step_target": 9000,
        "strength_sessions_per_week": 4,
        "protein_target_grams": 180,
    }
    g.update(overrides)
    return g


def _body_progress(*, hume_weight, hume_bf, hume_fm, hume_lm,
                   fitdays_weight=None, fitdays_bf=None):
    return {
        "status": "ok",
        "metrics": {
            "weight": {"goal_current_value": 185.0, "phase_start_value": 190.0,
                       "target_value": 178.0, "distance_to_target": 7.0,
                       "goal_direction": "decrease", "progress": {}},
            "body_fat_percentage": {"goal_current_value": 20.0,
                                    "phase_start_value": 22.0,
                                    "target_value": 15.0, "goal_direction": "decrease",
                                    "progress": {}},
            "fat_mass": {"goal_current_value": 37.0, "phase_start_value": 41.8,
                         "target_value": 26.7, "goal_direction": "decrease",
                         "progress": {}},
            "lean_mass": {"goal_current_value": 148.0, "phase_start_value": 148.2,
                          "target_value": 151.3, "goal_direction": "increase",
                          "progress": {}},
        },
        "historical_context": {
            "hume": {
                "weight": hume_weight, "body_fat_percentage": hume_bf,
                "fat_mass": hume_fm, "lean_mass": hume_lm,
            },
            "fitdays": {
                "weight": fitdays_weight or [], "body_fat_percentage": fitdays_bf or [],
                "fat_mass": [], "lean_mass": [],
            },
        },
    }


def _trends(steps_7=8600, days_7=7):
    return {"activity": {"baselines": {
        "7": {"steps": steps_7, "days_available": days_7},
        "14": {"steps": 8400, "days_available": 14},
        "30": {"steps": 8100, "days_available": 30},
        "90": {"steps": 7800, "days_available": 88},
    }}}


def _whoop_rows(days=60, *, hrv_per_day=0.0, rhr_per_day=0.0,
                recovery_per_day=0.0, sleep_per_day=0.0,
                hrv0=65.0, rhr0=52.0, rec0=60.0, sleep0=7.4):
    rows = []
    for i in range(days):
        d = TODAY - timedelta(days=days - 1 - i)
        rows.append({
            "metric_date": d,
            "hrv_rmssd_milli": hrv0 + hrv_per_day * i,
            "resting_heart_rate": rhr0 + rhr_per_day * i,
            "recovery_score": rec0 + recovery_per_day * i,
            "sleep_duration_hours": sleep0 + sleep_per_day * i,
            "sleep_performance_percentage": 80.0,
            "sleep_consistency_percentage": 70.0,
        })
    return rows


def _v1(**overrides):
    base = {
        "status": "ok", "phase": "lean_cut", "direction": "on_track",
        "phase_start_date": _iso(TODAY - timedelta(days=20)),
        "phase_age_days": 20, "minimum_phase_age_days": 7,
        "body_fat": {"progress_percentage": 40.0},
        "weight": {"progress_percentage": 41.6},
        "fat_mass": {}, "lean_mass": {}, "activity": {},
        "strength": {"status": "target_met"},
        "protein": {"status": "not_connected"},
        "summary": "Lean Cut is moving toward the goal.",
        "data_notes": ["note"],
    }
    base.update(overrides)
    return base


def _strength(status="target_met", sessions=4, target=4):
    return {
        "status": status, "sessions_7d": sessions,
        "qualifying_sessions_7d": sessions, "supplemental_sessions_7d": 0,
        "target_sessions_per_week": target,
        "percentage_of_target": round(sessions / max(target, 1) * 100, 1),
        "remaining_sessions": max(0, target - sessions),
        "window_start_date": "2026-08-31", "window_end_date": "2026-09-06",
    }


def _steps_rows(days=120, *, start=6000, per_day=20.0, end=None):
    end = end or (TODAY - timedelta(days=1))
    return [
        {"date": (end - timedelta(days=days - 1 - i)),
         "value": round(start + per_day * i)}
        for i in range(days)
    ]


def _run(goal, body_progress, whoop_rows, *, trends=None, v1=None,
         strength=None, steps_rows=None):
    trends = trends or _trends()
    v1 = v1 or _v1()
    strength = strength or _strength()
    if steps_rows is None:
        steps_rows = _steps_rows()
    with patch.object(v2, "strength_adherence", lambda t: strength), \
         patch.object(v2, "goal_progress", lambda **kw: v1):
        return v2.goal_progress_v2(
            goal=goal, trends=trends, body_progress=body_progress,
            whoop_rows=whoop_rows, steps_rows=steps_rows, v1=v1,
        )


# ============================================================
# SOURCE HIERARCHY / FALLBACKS
# ============================================================

class SourceHierarchyTests(unittest.TestCase):

    def _standard_body(self, **kw):
        return _body_progress(
            hume_weight=_lin_series(40, start=190.0, per_day=-0.12),
            hume_bf=_lin_series(40, start=22.0, per_day=-0.05),
            hume_fm=_lin_series(40, start=41.8, per_day=-0.12),
            hume_lm=_lin_series(40, start=148.2, per_day=-0.003),
            **kw,
        )

    def test_hume_is_preferred_current_weight_source(self):
        res = _run(_goal(), self._standard_body(), _whoop_rows())
        self.assertEqual(res["outcomes"]["weight"]["preferred_source"], "Hume")
        self.assertEqual(res["outcomes"]["weight"]["source"], "Hume")
        self.assertEqual(res["outcomes"]["weight"]["status"], "ok")

    def test_fitdays_only_history_is_labelled_and_not_used_for_current(self):
        body = _body_progress(
            hume_weight=[], hume_bf=[], hume_fm=[], hume_lm=[],
            fitdays_weight=_lin_series(200, start=205.0, per_day=-0.03),
        )
        res = _run(_goal(), body, _whoop_rows())
        w = res["outcomes"]["weight"]
        self.assertEqual(w["status"], "insufficient_history")
        self.assertEqual(w["source"], "Fitdays")
        self.assertIn("historical", (w["source_label"] or "").lower())

    def test_source_transition_is_identifiable_in_label(self):
        body = self._standard_body(
            fitdays_weight=_lin_series(
                300, start=210.0, per_day=-0.03,
                end=TODAY - timedelta(days=60),
            )
        )
        res = _run(_goal(), body, _whoop_rows())
        self.assertIn("Fitdays before", res["outcomes"]["weight"]["source_label"])

    def test_fat_mass_retains_hume_provenance(self):
        res = _run(_goal(), self._standard_body(), _whoop_rows())
        self.assertEqual(res["outcomes"]["fat_mass"]["source"], "Hume")

    def test_lean_mass_prefers_hume(self):
        res = _run(_goal(), self._standard_body(), _whoop_rows())
        self.assertEqual(res["outcomes"]["lean_mass"]["preferred_source"], "Hume")

    def test_steps_come_from_apple_health(self):
        res = _run(_goal(), self._standard_body(), _whoop_rows())
        self.assertEqual(res["drivers"]["steps"]["source"], "Apple Health")
        self.assertEqual(res["drivers"]["steps"]["status"], "ok")

    def test_steps_no_recent_days_but_history_exists_is_still_ok(self):
        # last 7 days empty, but weeks of older step data -> classify on the
        # longer window, not "insufficient_history".
        rows = _steps_rows(days=60, start=7000, per_day=10,
                           end=TODAY - timedelta(days=10))
        res = _run(_goal(), self._standard_body(), _whoop_rows(), steps_rows=rows)
        s = res["drivers"]["steps"]
        self.assertEqual(s["status"], "ok")
        self.assertIn("sparse", (s["note"] or "").lower())

    def test_steps_insufficient_history_when_few_total_days(self):
        rows = _steps_rows(days=2, start=800, per_day=0,
                           end=TODAY - timedelta(days=1))
        res = _run(_goal(), self._standard_body(), _whoop_rows(), steps_rows=rows)
        self.assertEqual(res["drivers"]["steps"]["status"], "insufficient_history")

    def test_steps_percentage_only_present_when_usable(self):
        rows = _steps_rows(days=2, start=800, per_day=0)
        res = _run(_goal(), self._standard_body(), _whoop_rows(), steps_rows=rows)
        s = res["drivers"]["steps"]
        self.assertNotEqual(s["status"], "ok")
        # no misleading "9%"-style number next to an unusable state
        self.assertIsNone(s.get("percentage_of_target"))

    def test_steps_history_not_phase_truncated(self):
        rows = _steps_rows(days=200, start=5000, per_day=15,
                           end=TODAY - timedelta(days=1))
        res = _run(_goal(phase_start_days_ago=20), self._standard_body(),
                   _whoop_rows(), steps_rows=rows)
        hc = res["historical_context"]["steps"]
        self.assertEqual(hc["record_count"], 200)
        self.assertLess(
            date.fromisoformat(hc["earliest_date"]),
            date.fromisoformat(res["phase_detail"]["phase_start_date"]),
        )
        for k in ("7D", "14D", "30D", "90D", "6M", "1Y"):
            self.assertIn(k, hc["windows"])

    def test_strength_reuses_tonal_adherence_and_labels_source(self):
        res = _run(_goal(), self._standard_body(), _whoop_rows(),
                   strength=_strength("below_target", sessions=2))
        s = res["drivers"]["strength"]
        self.assertEqual(s["source"], "Tonal")
        self.assertEqual(s["adherence_status"], "below_target")
        self.assertEqual(s["sessions_7d"], 2)

    def test_strength_not_connected_state(self):
        res = _run(_goal(), self._standard_body(), _whoop_rows(),
                   strength={"status": "not_connected"})
        self.assertEqual(res["drivers"]["strength"]["status"], "not_connected")

    def test_sleep_hrv_rhr_recovery_come_from_whoop(self):
        res = _run(_goal(), self._standard_body(), _whoop_rows())
        for m in ("hrv", "resting_heart_rate", "recovery"):
            self.assertEqual(res["physiology"][m]["source"], "WHOOP")
            self.assertEqual(res["physiology"][m]["status"], "ok")
        self.assertEqual(res["drivers"]["sleep"]["source"], "WHOOP")

    def test_whoop_metric_insufficient_history_when_sparse(self):
        res = _run(_goal(), self._standard_body(), _whoop_rows(days=3))
        self.assertEqual(res["physiology"]["hrv"]["status"], "insufficient_history")

    def test_vo2_max_is_apple_sourced_and_unavailable_not_failure(self):
        res = _run(_goal(), self._standard_body(), _whoop_rows())
        v = res["physiology"]["vo2_max"]
        self.assertEqual(v["preferred_source"], "Apple Health")
        self.assertEqual(v["status"], "unavailable")
        self.assertIsNone(v["source"])          # no WHOOP fallback
        self.assertIn("whoop does not expose", (v["note"] or "").lower())

    def test_hydration_is_unavailable_not_target_as_consumption(self):
        res = _run(_goal(), self._standard_body(), _whoop_rows())
        h = res["drivers"]["hydration"]
        self.assertEqual(h["status"], "unavailable")
        self.assertIsNone(h["source"])

    def test_calories_and_protein_are_not_connected(self):
        res = _run(_goal(), self._standard_body(), _whoop_rows())
        self.assertEqual(res["drivers"]["calories"]["status"], "not_connected")
        self.assertEqual(res["drivers"]["protein"]["status"], "not_connected")
        # target preserved as a placeholder only
        self.assertEqual(res["drivers"]["protein"]["target_grams_per_day"], 180)


# ============================================================
# PHASE PROGRESS vs HISTORICAL CONTEXT
# ============================================================

class PhaseVsHistoryTests(unittest.TestCase):

    def _body_with_prephase_history(self):
        # 120 days of Hume weight: 100 before phase start, 20 after.
        return _body_progress(
            hume_weight=_lin_series(120, start=196.0, per_day=-0.05),
            hume_bf=_lin_series(120, start=23.0, per_day=-0.01),
            hume_fm=_lin_series(120, start=45.0, per_day=-0.03),
            hume_lm=_lin_series(120, start=151.0, per_day=-0.002),
        )

    def test_phase_start_does_not_truncate_historical_context(self):
        res = _run(_goal(phase_start_days_ago=20),
                   self._body_with_prephase_history(), _whoop_rows(days=200))
        hc = res["historical_context"]["weight"]
        # 120 daily points, all retained (pre-phase not dropped)
        self.assertEqual(hc["hume_record_count"], 120)
        earliest = date.fromisoformat(hc["hume_earliest_date"])
        phase_start = date.fromisoformat(res["phase_detail"]["phase_start_date"])
        self.assertLess(earliest, phase_start)

    def test_phase_change_uses_only_phase_period_data(self):
        body = self._body_with_prephase_history()
        res = _run(_goal(phase_start_days_ago=20), body, _whoop_rows())
        w = res["outcomes"]["weight"]
        # phase_start_value is the first Hume point on/after phase start,
        # not the first point of the whole 120-day series.
        series = body["historical_context"]["hume"]["weight"]
        phase_start = date.fromisoformat(res["phase_detail"]["phase_start_date"])
        first_in_phase = next(
            p["value"] for p in series
            if date.fromisoformat(p["date"]) >= phase_start
        )
        self.assertAlmostEqual(w["phase_start_value"], round(first_in_phase, 2), places=1)

    def test_historical_6m_and_1y_windows_present(self):
        res = _run(_goal(), self._body_with_prephase_history(),
                   _whoop_rows(days=250))
        windows = res["historical_context"]["weight"]["windows"]
        for key in ("7D", "14D", "30D", "90D", "6M", "1Y"):
            self.assertIn(key, windows)

    def test_historical_1y_returns_pre_phase_reference_when_available(self):
        res = _run(_goal(phase_start_days_ago=20),
                   self._body_with_prephase_history(), _whoop_rows(days=250))
        w90 = res["historical_context"]["weight"]["windows"]["90D"]
        self.assertTrue(w90["sufficient_data"])
        self.assertIsNotNone(w90["reference_average"])

    def test_series_provenance_preserved_hume_and_fitdays_separate(self):
        body = _body_progress(
            hume_weight=_lin_series(40, start=190.0, per_day=-0.1),
            hume_bf=_lin_series(40, start=22.0, per_day=-0.02),
            hume_fm=_lin_series(40, start=41.8, per_day=-0.1),
            hume_lm=_lin_series(40, start=148.0, per_day=-0.002),
            fitdays_weight=_lin_series(200, start=205.0, per_day=-0.02),
        )
        res = _run(_goal(), body, _whoop_rows())
        hc = res["historical_context"]["weight"]
        self.assertGreater(len(hc["hume_series"]), 0)
        self.assertGreater(len(hc["fitdays_series"]), 0)
        # never merged into one list
        self.assertNotIn("series", hc)


# ============================================================
# DETERMINISTIC GOAL INTELLIGENCE SCENARIOS
# ============================================================

class IntelligenceScenarioTests(unittest.TestCase):

    def _body(self, *, weight_per_day, bf_per_day, lm_per_day, fm_per_day):
        return _body_progress(
            hume_weight=_lin_series(40, start=190.0, per_day=weight_per_day),
            hume_bf=_lin_series(40, start=22.0, per_day=bf_per_day),
            hume_fm=_lin_series(40, start=41.8, per_day=fm_per_day),
            hume_lm=_lin_series(40, start=148.0, per_day=lm_per_day),
        )

    def test_1_fat_loss_lean_stable_strength_stable_is_positive_recomp(self):
        res = _run(
            _goal(),
            self._body(weight_per_day=-0.05, bf_per_day=-0.03,
                       lm_per_day=0.0, fm_per_day=-0.05),
            _whoop_rows(),
            strength=_strength("target_met", sessions=4),
        )
        joined = " ".join(res["intelligence"]["positive_signals"]).lower()
        self.assertIn("recomposition", joined)
        self.assertIn(res["intelligence"]["overall_status"], ("on_track", "mixed"))

    def test_2_rapid_weight_loss_plus_recovery_decline_flags_aggressive_deficit(self):
        res = _run(
            _goal(),
            self._body(weight_per_day=-0.25, bf_per_day=-0.005,
                       lm_per_day=-0.05, fm_per_day=-0.20),
            _whoop_rows(recovery_per_day=-0.5, rec0=80.0),
            strength=_strength("below_target", sessions=1),
        )
        hyp = " ".join(res["intelligence"]["hypotheses"]).lower()
        self.assertIn("aggressive deficit", hyp)
        self.assertEqual(res["intelligence"]["overall_status"], "off_track")

    def test_3_scale_plateau_with_improving_composition_is_positive(self):
        res = _run(
            _goal(),
            self._body(weight_per_day=0.0, bf_per_day=-0.04,
                       lm_per_day=0.03, fm_per_day=-0.06),
            _whoop_rows(),
        )
        joined = " ".join(res["intelligence"]["positive_signals"]).lower()
        self.assertTrue(
            "plateau" in joined or "recomposition" in joined
        )

    def test_4_good_strength_but_poor_sleep_flags_recovery_limiter(self):
        res = _run(
            _goal(),
            self._body(weight_per_day=-0.03, bf_per_day=-0.01,
                       lm_per_day=0.0, fm_per_day=-0.03),
            _whoop_rows(sleep_per_day=-0.03, sleep0=8.2),
            strength=_strength("target_met", sessions=4),
        )
        hyp = " ".join(res["intelligence"]["hypotheses"]).lower()
        self.assertIn("sleep", hyp)
        self.assertIn("limiting factor", hyp)

    def test_5_insufficient_body_composition_history(self):
        body = _body_progress(
            hume_weight=_lin_series(2, start=190.0, per_day=-0.1),
            hume_bf=_lin_series(2, start=22.0, per_day=-0.02),
            hume_fm=_lin_series(2, start=41.8, per_day=-0.1),
            hume_lm=_lin_series(2, start=148.0, per_day=0.0),
        )
        res = _run(_goal(phase_start_days_ago=3), body, _whoop_rows())
        self.assertEqual(res["intelligence"]["overall_status"], "insufficient_data")

    def test_6_missing_goal_timeline_returns_not_configured(self):
        res = _run(
            _goal(phase_end=None),
            self._body(weight_per_day=-0.05, bf_per_day=-0.02,
                       lm_per_day=0.0, fm_per_day=-0.05),
            _whoop_rows(),
        )
        self.assertEqual(res["goal_timeline"]["timeline_status"], "not_configured")
        self.assertNotIn("goal_deadline", res["goal_timeline"])

    def test_7_configured_timeline_on_track_trajectory(self):
        # deadline 60 days out, required ~ -12lb over ~11.4 weeks => ~-1.05/wk;
        # observed ~ -0.05*7 = -0.35/wk ... make weight drop faster:
        res = _run(
            _goal(phase_start_days_ago=28,
                  phase_end=_iso(TODAY + timedelta(days=56))),
            self._body(weight_per_day=-0.16, bf_per_day=-0.02,
                       lm_per_day=0.0, fm_per_day=-0.14),
            _whoop_rows(),
        )
        tl = res["goal_timeline"]
        self.assertEqual(tl["timeline_status"], "configured")
        self.assertIsNotNone(tl["required_weekly_weight_change_lb"])
        self.assertIsNotNone(tl["observed_weekly_weight_change_lb"])
        self.assertIsNotNone(tl["projected_completion_date"])

    def test_8_configured_timeline_off_track_trajectory(self):
        res = _run(
            _goal(phase_start_days_ago=28,
                  phase_end=_iso(TODAY + timedelta(days=21))),
            self._body(weight_per_day=-0.01, bf_per_day=-0.005,
                       lm_per_day=0.0, fm_per_day=-0.01),
            _whoop_rows(),
        )
        tl = res["goal_timeline"]
        self.assertEqual(tl["timeline_status"], "configured")
        self.assertFalse(tl["on_track"])


# ============================================================
# BACKWARD COMPAT + SERIALIZATION
# ============================================================

class BackwardCompatTests(unittest.TestCase):

    def _res(self):
        body = _body_progress(
            hume_weight=_lin_series(40, start=190.0, per_day=-0.1),
            hume_bf=_lin_series(40, start=22.0, per_day=-0.02),
            hume_fm=_lin_series(40, start=41.8, per_day=-0.1),
            hume_lm=_lin_series(40, start=148.0, per_day=-0.002),
        )
        return _run(_goal(), body, _whoop_rows())

    def test_every_v1_key_is_preserved(self):
        res = self._res()
        for key in ("status", "phase", "direction", "phase_start_date",
                    "phase_age_days", "minimum_phase_age_days", "body_fat",
                    "weight", "fat_mass", "lean_mass", "activity", "strength",
                    "protein", "summary", "data_notes"):
            self.assertIn(key, res, key)
        # V1 shapes unchanged: phase is a string, summary is a sentence
        self.assertIsInstance(res["phase"], str)
        self.assertIsInstance(res["summary"], str)

    def test_v2_sections_added_under_non_colliding_keys(self):
        res = self._res()
        for key in ("version", "phase_detail", "summary_detail", "outcomes",
                    "drivers", "physiology", "historical_context",
                    "historical_windows", "goal_timeline", "intelligence",
                    "methodology"):
            self.assertIn(key, res, key)
        self.assertEqual(res["version"], 2)
        self.assertIsInstance(res["phase_detail"], dict)

    def test_response_is_json_serializable(self):
        json.dumps(self._res())

    def test_no_active_goal_returns_typed_state(self):
        with patch.object(v2, "get_active_goal", return_value=None):
            res = v2.goal_progress_v2(goal=None)
        self.assertEqual(res["status"], "no_active_goal")
        self.assertEqual(res["version"], 2)


# ============================================================
# V2.1 - DIRECTION vs INTERPRETATION SEMANTICS
# ============================================================

class SemanticInterpretationTests(unittest.TestCase):
    """`direction` is the raw movement; `interpretation` applies goal context
    and drives colour. Reuse deterministic bands so noise stays neutral."""

    def _standard_body(self, **kw):
        return _body_progress(
            hume_weight=_lin_series(45, start=190.0, per_day=-0.05),
            hume_bf=_lin_series(45, start=22.0, per_day=-0.02),
            hume_fm=_lin_series(45, start=41.8, per_day=-0.05),
            hume_lm=_lin_series(45, start=148.0, per_day=0.0),
            **kw,
        )

    def _phys(self, metric, per_day, **kw):
        kwargs = {"hrv_per_day": 0.0, "rhr_per_day": 0.0,
                  "recovery_per_day": 0.0, "sleep_per_day": 0.0}
        kwargs[f"{metric}_per_day"] = per_day
        kwargs.update(kw)
        return _whoop_rows(days=60, **kwargs)

    # ---- HRV ----
    def test_hrv_increase_is_favorable(self):
        res = _run(_goal(), self._standard_body(),
                   self._phys("hrv", 0.4, hrv0=45.0))
        h = res["physiology"]["hrv"]
        self.assertEqual(h["direction"], "increasing")
        self.assertEqual(h["interpretation"], "favorable")

    def test_hrv_decline_is_unfavorable(self):
        res = _run(_goal(), self._standard_body(),
                   self._phys("hrv", -0.5, hrv0=80.0))
        h = res["physiology"]["hrv"]
        self.assertEqual(h["direction"], "decreasing")
        self.assertEqual(h["interpretation"], "unfavorable")

    # ---- Resting HR ----
    def test_rhr_decline_is_favorable(self):
        res = _run(_goal(), self._standard_body(),
                   self._phys("rhr", -0.15, rhr0=62.0))
        r = res["physiology"]["resting_heart_rate"]
        self.assertEqual(r["direction"], "decreasing")
        self.assertEqual(r["interpretation"], "favorable")

    def test_rhr_increase_is_unfavorable(self):
        res = _run(_goal(), self._standard_body(),
                   self._phys("rhr", 0.15, rhr0=48.0))
        r = res["physiology"]["resting_heart_rate"]
        self.assertEqual(r["direction"], "increasing")
        self.assertEqual(r["interpretation"], "unfavorable")

    # ---- Recovery ----
    def test_recovery_flat_is_neutral(self):
        res = _run(_goal(), self._standard_body(),
                   self._phys("recovery", 0.0, rec0=60.0))
        rc = res["physiology"]["recovery"]
        self.assertEqual(rc["direction"], "flat")
        self.assertEqual(rc["interpretation"], "neutral")

    # ---- Body comp in a Lean Cut ----
    def test_body_fat_decline_in_lean_cut_is_favorable(self):
        res = _run(_goal(),
                   _body_progress(
                       hume_weight=_lin_series(45, start=190.0, per_day=-0.05),
                       hume_bf=_lin_series(45, start=22.0, per_day=-0.03),
                       hume_fm=_lin_series(45, start=41.8, per_day=-0.05),
                       hume_lm=_lin_series(45, start=148.0, per_day=0.0)),
                   _whoop_rows())
        b = res["outcomes"]["body_fat"]
        self.assertEqual(b["direction"], "decreasing")
        self.assertEqual(b["interpretation"], "favorable")

    def test_fat_mass_decline_in_lean_cut_is_favorable(self):
        res = _run(_goal(),
                   _body_progress(
                       hume_weight=_lin_series(45, start=190.0, per_day=-0.06),
                       hume_bf=_lin_series(45, start=22.0, per_day=-0.02),
                       hume_fm=_lin_series(45, start=41.8, per_day=-0.06),
                       hume_lm=_lin_series(45, start=148.0, per_day=0.0)),
                   _whoop_rows())
        f = res["outcomes"]["fat_mass"]
        self.assertEqual(f["direction"], "decreasing")
        self.assertEqual(f["interpretation"], "favorable")

    def test_lean_mass_meaningful_decline_is_unfavorable(self):
        res = _run(_goal(),
                   _body_progress(
                       hume_weight=_lin_series(45, start=190.0, per_day=-0.15),
                       hume_bf=_lin_series(45, start=22.0, per_day=-0.005),
                       hume_fm=_lin_series(45, start=41.8, per_day=-0.10),
                       hume_lm=_lin_series(45, start=148.0, per_day=-0.05)),
                   _whoop_rows())
        lm = res["outcomes"]["lean_mass"]
        self.assertEqual(lm["direction"], "decreasing")
        self.assertEqual(lm["interpretation"], "unfavorable")

    def test_weight_interpretation_respects_goal_trajectory(self):
        # Lean cut -> goal_direction decrease -> a clear weight drop = favorable
        res = _run(_goal(),
                   _body_progress(
                       hume_weight=_lin_series(45, start=192.0, per_day=-0.14),
                       hume_bf=_lin_series(45, start=22.0, per_day=-0.02),
                       hume_fm=_lin_series(45, start=41.8, per_day=-0.10),
                       hume_lm=_lin_series(45, start=148.0, per_day=0.0)),
                   _whoop_rows())
        w = res["outcomes"]["weight"]
        self.assertEqual(w["direction"], "decreasing")
        self.assertEqual(w["interpretation"], "favorable")

    def test_weight_up_during_lean_cut_is_unfavorable(self):
        res = _run(_goal(),
                   _body_progress(
                       hume_weight=_lin_series(45, start=185.0, per_day=0.08),
                       hume_bf=_lin_series(45, start=21.0, per_day=0.01),
                       hume_fm=_lin_series(45, start=39.0, per_day=0.06),
                       hume_lm=_lin_series(45, start=146.0, per_day=0.0)),
                   _whoop_rows())
        w = res["outcomes"]["weight"]
        self.assertEqual(w["direction"], "increasing")
        self.assertEqual(w["interpretation"], "unfavorable")

    def test_flat_change_stays_neutral(self):
        res = _run(_goal(),
                   _body_progress(
                       hume_weight=_lin_series(45, start=185.0, per_day=0.0),
                       hume_bf=_lin_series(45, start=21.0, per_day=0.0),
                       hume_fm=_lin_series(45, start=39.0, per_day=0.0),
                       hume_lm=_lin_series(45, start=146.0, per_day=0.0)),
                   self._phys("hrv", 0.0, hrv0=60.0))
        self.assertEqual(res["outcomes"]["weight"]["interpretation"], "neutral")
        self.assertEqual(res["physiology"]["hrv"]["interpretation"], "neutral")

    def test_insignificant_change_does_not_trigger_adverse(self):
        # tiny HRV drop within the 4% band -> flat / neutral, not unfavorable
        res = _run(_goal(), self._standard_body(),
                   self._phys("hrv", -0.01, hrv0=60.0))
        h = res["physiology"]["hrv"]
        self.assertEqual(h["direction"], "flat")
        self.assertEqual(h["interpretation"], "neutral")

    def test_start_current_delta_are_internally_consistent(self):
        res = _run(_goal(), self._standard_body(), _whoop_rows())
        for sec in ("outcomes",):
            for m in res[sec].values():
                c = m.get("comparison")
                if not c:
                    continue
                self.assertAlmostEqual(
                    c["current_value"] - c["start_value"],
                    c["absolute_change"], places=1,
                )

    def test_source_provenance_survives_simplification(self):
        body = _body_progress(
            hume_weight=_lin_series(40, start=190.0, per_day=-0.1),
            hume_bf=_lin_series(40, start=22.0, per_day=-0.02),
            hume_fm=_lin_series(40, start=41.8, per_day=-0.1),
            hume_lm=_lin_series(40, start=148.0, per_day=0.0),
            fitdays_weight=_lin_series(200, start=205.0, per_day=-0.02),
        )
        res = _run(_goal(), body, _whoop_rows())
        w = res["outcomes"]["weight"]
        self.assertEqual(w["source_short"], "Hume")
        self.assertEqual(w["history_source"], "Fitdays")
        # full provenance still present
        self.assertIn("Fitdays before", w["source_label"])

    def test_intelligence_consumes_interpretation_hrv_down_rhr_up(self):
        rows = _whoop_rows(days=60, hrv_per_day=-0.5, hrv0=80.0,
                           rhr_per_day=0.15, rhr0=48.0)
        res = _run(_goal(phase_start_days_ago=30), self._standard_body(), rows)
        joined = " ".join(
            res["intelligence"]["constraints"] + res["intelligence"]["hypotheses"]
        ).lower()
        self.assertIn("resting heart rate up", joined)

    def test_sleep_history_not_phase_truncated(self):
        res = _run(_goal(phase_start_days_ago=20), self._standard_body(),
                   _whoop_rows(days=200))
        hc = res["historical_context"]["sleep_duration"]
        self.assertGreaterEqual(hc["record_count"], 150)
        self.assertLess(
            date.fromisoformat(hc["earliest_date"]),
            date.fromisoformat(res["phase_detail"]["phase_start_date"]),
        )
        for k in ("7D", "14D", "30D", "90D", "6M", "1Y"):
            self.assertIn(k, hc["windows"])


# ============================================================
# Goal Setting V2 -> Goal Progress timeline integration
# ============================================================

class GoalProgressTimelineFromV2GoalTests(unittest.TestCase):

    def _body(self):
        return _body_progress(
            hume_weight=_lin_series(40, start=190.0, per_day=-0.1),
            hume_bf=_lin_series(40, start=22.0, per_day=-0.02),
            hume_fm=_lin_series(40, start=41.8, per_day=-0.1),
            hume_lm=_lin_series(40, start=148.0, per_day=0.0),
        )

    def test_activated_v2_goal_makes_timeline_configured(self):
        target_date = _iso(TODAY + timedelta(days=90))
        goal = _goal(
            phase_start_days_ago=28,
            target_date=target_date,
            selected_pace="comfortable",
            expected_weekly_weight_change_lb=-0.7,
            timeline_status="configured",
            goal_version=2,
        )
        res = _run(goal, self._body(), _whoop_rows())
        tl = res["goal_timeline"]
        self.assertEqual(tl["timeline_status"], "configured")
        self.assertEqual(tl["goal_deadline"], target_date)
        self.assertEqual(tl["selected_pace"], "comfortable")
        # uses the persisted selected pace, not a re-derived rate
        self.assertEqual(tl["required_weekly_weight_change_lb"], -0.7)

    def test_outside_supported_range_is_carried_through(self):
        goal = _goal(
            phase_start_days_ago=14,
            target_date=_iso(TODAY + timedelta(days=120)),
            aspirational_target_date=_iso(TODAY + timedelta(days=20)),
            selected_pace="recommended",
            expected_weekly_weight_change_lb=-0.9,
            timeline_status="outside_supported_range",
            goal_version=2,
        )
        res = _run(goal, self._body(), _whoop_rows())
        tl = res["goal_timeline"]
        self.assertEqual(tl["timeline_status"], "outside_supported_range")
        self.assertEqual(
            tl["aspirational_target_date"], _iso(TODAY + timedelta(days=20))
        )

    def test_legacy_goal_with_only_phase_end_still_configures(self):
        goal = _goal(phase_start_days_ago=28,
                     phase_end=_iso(TODAY + timedelta(days=60)))
        res = _run(goal, self._body(), _whoop_rows())
        self.assertEqual(res["goal_timeline"]["timeline_status"], "configured")

    def test_no_deadline_still_not_configured(self):
        res = _run(_goal(), self._body(), _whoop_rows())
        self.assertEqual(
            res["goal_timeline"]["timeline_status"], "not_configured"
        )

    def test_v2_goal_preserves_all_six_history_windows_and_phase_day(self):
        goal = _goal(phase_start_days_ago=20,
                     target_date=_iso(TODAY + timedelta(days=90)),
                     timeline_status="configured", goal_version=2)
        res = _run(goal, self._body(), _whoop_rows(days=200))
        self.assertEqual(res["phase_detail"]["phase_day"], 21)
        for k in ("7D", "14D", "30D", "90D", "6M", "1Y"):
            self.assertIn(k, res["historical_context"]["weight"]["windows"])


if __name__ == "__main__":
    unittest.main()
