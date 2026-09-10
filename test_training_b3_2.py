import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from integrations.tonal import training_priority as tp


MUSCLES = ("Chest", "Back", "Shoulders", "Biceps", "Triceps", "Core", "Glutes", "Hamstrings", "Quads")


def analytics(gaps=None):
    gaps = gaps or {}
    muscle_rows = {muscle: {"primary_sessions": 1, "secondary_sessions": 0,
                            "primary_sets": 6, "secondary_sets": 0,
                            "days_since_primary_training": gaps.get(muscle, 3),
                            "last_primary_trained_at": None}
                   for muscle in MUSCLES}
    return {"windows": {"7": {"muscles": muscle_rows}}, "latest_strength_scores": None}


def readiness(states=None):
    states = states or {}
    return {"selection_confidence": "high", "latest_workout_age_hours": 12,
            "latest_tonal_workout_at": "2026-07-14T11:00:00+00:00",
            "muscles": [{"muscle": muscle, "readiness_state": states.get(muscle, "READY"),
                         "readiness_score": 80, "effective_sets_7d": 0}
                        for muscle in MUSCLES]}


class ProgramBalanceCalibrationTests(unittest.TestCase):
    def priority(self, gaps=None, states=None, history=None, calibration="balanced"):
        with patch.object(tp, "strength_analytics", return_value=analytics(gaps)), \
             patch.object(tp, "calculate_muscle_readiness", return_value=readiness(states)):
            return tp.build_training_priority(
                now=datetime(2026, 7, 15, 11, tzinfo=timezone.utc),
                recommendation_history=history or [], calibration=calibration)

    def test_neglect_accumulates_smoothly_and_is_bounded(self):
        ten = self.priority({"Back": 10})
        twenty = self.priority({"Back": 20})
        score10 = next(row for row in ten["ranked_muscles"] if row["muscle"] == "Back")["program_coverage_score"]
        score20 = next(row for row in twenty["ranked_muscles"] if row["muscle"] == "Back")["program_coverage_score"]
        self.assertGreater(score20, score10)
        self.assertLess(score20, tp.CALIBRATION_PROFILES["balanced"]["coverage_max"])

    def test_actual_training_reduces_coverage_pressure(self):
        neglected = self.priority({"Back": 20})
        trained = self.priority({"Back": 1})
        def score(result):
            return next(row for row in result["ranked_muscles"] if row["muscle"] == "Back")["program_coverage_score"]
        self.assertGreater(score(neglected), score(trained))

    def test_repeat_overlap_favors_equally_suitable_alternative(self):
        ranked = [{"muscle": muscle, "priority_score": 100} for muscle in MUSCLES]
        history = [{"focus": "Lower Body", "selected_muscles": ["Glutes", "Hamstrings", "Quads"]}]
        scores = tp._score_session_templates(ranked, readiness(), history, tp.CALIBRATION_PROFILES["balanced"])
        lower = next(row for row in scores if row["session_type"] == "Lower Body")
        upper = next(row for row in scores if row["session_type"] == "Upper Push")
        self.assertLess(lower["score"], upper["score"])

    def test_no_hard_rotation_when_only_prior_region_is_safe(self):
        states = {muscle: "FATIGUED" for muscle in MUSCLES}
        states.update({"Glutes": "READY", "Hamstrings": "READY", "Quads": "READY"})
        result = self.priority(states=states, history=[{"focus": "Lower Body", "selected_muscles": ["Glutes", "Hamstrings", "Quads"]}])
        self.assertEqual("Lower Body", result["recommended_session"]["session_type"])

    def test_fatigued_and_suppressed_never_become_template_eligible(self):
        ranked = [{"muscle": muscle, "priority_score": 9999} for muscle in MUSCLES]
        states = readiness({"Back": "SUPPRESSED", "Biceps": "FATIGUED"})
        scores = tp._score_session_templates(ranked, states, [], tp.CALIBRATION_PROFILES["balanced"])
        pull = next(row for row in scores if row["session_type"] == "Upper Pull")
        self.assertFalse(pull["eligible"])

    def test_ready_beats_recovering_when_need_is_comparable(self):
        result = self.priority(states={"Back": "READY", "Glutes": "RECOVERING"})
        rows = {row["muscle"]: row for row in result["ranked_muscles"]}
        self.assertGreater(rows["Back"]["priority_score"], rows["Glutes"]["priority_score"])

    def test_recovering_can_win_with_materially_stronger_need(self):
        result = self.priority(gaps={"Glutes": 25, "Back": 1},
                               states={"Glutes": "RECOVERING", "Back": "READY"})
        rows = {row["muscle"]: row for row in result["ranked_muscles"]}
        self.assertGreater(rows["Glutes"]["program_coverage_score"], rows["Back"]["program_coverage_score"])




class BoundedCoverageTests(unittest.TestCase):
    def setUp(self):
        from integrations.tonal import program_balance as pb
        self.pb=pb
        self.now=datetime(2026,7,15,11,tzinfo=timezone.utc)
        self.states={m:{'readiness_state':'READY'} for m in MUSCLES}

    def coverage(self, gap=20, secondary=None, state='READY', history=None):
        rows={m:{'primary_sets':20,'days_since_primary_training':3} for m in MUSCLES}
        rows['Chest']={'primary_sets':0,'secondary_sets':0,'days_since_primary_training':gap,
                       'days_since_secondary_training':secondary}
        if secondary is not None:rows['Chest']['secondary_sets']=6
        self.states['Chest']['readiness_state']=state
        return self.pb.coverage_state(rows,self.states,history or [],self.now)['Chest']

    def test_recent_actual_primary_resolves_coverage(self):
        self.assertEqual(self.coverage(gap=1)['coverage_score'],0)
        self.assertGreater(self.coverage(gap=20)['coverage_score'],0)

    def test_secondary_exposure_partially_reduces_need(self):
        primary=self.coverage(gap=1)['coverage_score']
        secondary=self.coverage(gap=20,secondary=1)['coverage_score']
        none=self.coverage(gap=20)['coverage_score']
        self.assertLess(primary,secondary);self.assertLess(secondary,none)

    def test_gradual_increase_and_bounded_maximum(self):
        scores=[self.coverage(gap=n)['coverage_score'] for n in [4,6,10,20,30,10000]]
        self.assertEqual(scores,sorted(scores));self.assertLessEqual(max(scores),40)
        self.assertEqual(scores[-1],scores[-2])

    def test_recovering_pressure_is_constrained(self):
        ready=self.coverage()['coverage_score']
        recovering=self.coverage(state='RECOVERING')['coverage_score']
        self.assertAlmostEqual(recovering,ready*.5,places=2)

    def test_fatigued_suppressed_get_no_coverage_rescue(self):
        for state in ['FATIGUED','SUPPRESSED']:
            self.assertEqual(self.coverage(state=state)['coverage_score'],0)

    def test_recommendations_are_weaker_than_actual_training(self):
        history=[{'plan_date':'2026-07-14','primary_focus':['Chest'],'selected_muscles':['Chest']}]
        recommendation=self.coverage(history=history)['coverage_score']
        self.assertGreater(recommendation,self.coverage(gap=1)['coverage_score'])
        self.assertLess(recommendation,self.coverage()['coverage_score'])

    def test_secondary_recommendation_reduces_less_than_primary(self):
        primary=[{'plan_date':'2026-07-14','primary_focus':['Chest']}]
        secondary=[{'plan_date':'2026-07-14','primary_focus':[],'secondary_focus':['Chest']}]
        self.assertLess(self.coverage(history=primary)['coverage_score'],self.coverage(history=secondary)['coverage_score'])

    def test_future_and_same_day_recommendations_do_not_leak(self):
        expected=self.coverage()
        for day in ['2026-07-15','2026-07-16']:
            self.assertEqual(expected,self.coverage(history=[{'plan_date':day,'primary_focus':['Chest']}]))

    def test_as_of_timestamp_controls_actual_recency(self):
        rows={m:{'primary_sets':0,'last_primary_trained_at':'2026-07-14T11:00:00+00:00'} for m in MUSCLES}
        result=self.pb.coverage_state(rows,self.states,[],self.now)
        self.assertTrue(all(r['actual_primary_gap_days']==1 for r in result.values()))
        self.assertTrue(all(r['coverage_score']==0 for r in result.values()))

    def test_historical_tonal_query_has_upper_boundary(self):
        from integrations.tonal import strength_analytics as sa
        from contextlib import nullcontext
        from unittest.mock import MagicMock
        cursor=MagicMock();cursor.fetchall.return_value=[]
        conn=MagicMock();conn.cursor.return_value=nullcontext(cursor)
        with patch.object(sa,'get_conn',return_value=nullcontext(conn)):
            sa._load_training_rows(30,now=self.now)
        query,params=cursor.execute.call_args.args
        self.assertIn('w.begin_time <= %s',query)
        self.assertEqual(params[-1],self.now)


class OverlapCalibrationTests(unittest.TestCase):
    def setUp(self):
        from integrations.tonal import program_balance as pb
        self.pb=pb
        self.now=datetime(2026,7,15,11,tzinfo=timezone.utc)
        self.weights=pb.PROFILES['balanced']['overlap_weights']
        self.history=[{'plan_date':'2026-07-'+str(d),'selected_muscles':['Chest','Shoulders']} for d in [14,13,12]]

    def test_progressive_bounded_overlap(self):
        penalties=[self.pb.overlap_rotation(['Chest','Shoulders'],self.history[:n],{},self.now,self.weights)[0] for n in [1,2,3]]
        self.assertGreater(penalties[1],penalties[0]);self.assertGreaterEqual(penalties[2],penalties[1])
        self.assertLessEqual(max(penalties),self.pb.OVERLAP_ROTATION_CAP)

    def test_disjoint_recommendations_have_no_overlap_penalty(self):
        self.assertEqual(self.pb.overlap_rotation(['Quads','Glutes'],self.history,{},self.now,self.weights)[0],0)

    def test_actual_intervening_training_changes_repeat_state(self):
        old=self.pb.overlap_rotation(['Chest','Shoulders'],self.history[:1],{},self.now,self.weights)[0]
        actual={m:{'actual_primary_gap_days':.5} for m in ['Chest','Shoulders']}
        new,parts=self.pb.overlap_rotation(['Chest','Shoulders'],self.history[:1],actual,self.now,self.weights)
        self.assertNotEqual(old,new);self.assertGreater(parts[0]['fulfilled_fraction'],0)

    def test_future_history_is_ignored(self):
        future=[{'plan_date':'2026-07-16','selected_muscles':['Chest']}]
        self.assertEqual(self.pb.overlap_rotation(['Chest'],future,{},self.now,self.weights)[0],0)


class CorrelationAggregationTests(unittest.TestCase):
    def score(self,values=None,states=None):
        return tp._score_session_templates([{'muscle':m,'priority_score':(values or {}).get(m,100.)} for m in MUSCLES],
                                          readiness(states),[],tp.CALIBRATION_PROFILES['correlation'])

    def test_equal_priority_has_no_template_size_advantage(self):
        self.assertTrue(all(t['score']==100 for t in self.score() if t['eligible']))

    def test_correlated_region_does_not_multiply_score(self):
        self.assertTrue(all(t['score']<=200 for t in self.score({m:200 for m in ['Quads','Glutes','Hamstrings']})))

    def test_neglected_anchor_remains_competitive_with_covered_support(self):
        scores=self.score({'Chest':200,'Biceps':20})
        chest=next(t for t in scores if t['session_type']=='Chest + Biceps')
        lower=next(t for t in scores if t['session_type']=='Lower Body')
        self.assertGreater(chest['score'],lower['score'])

    def test_full_body_score_includes_both_regions(self):
        t=next(t for t in self.score({'Quads':250,'Glutes':249,'Hamstrings':248,'Core':247}) if t['session_type']=='Full Body')
        self.assertTrue(set(t['scored_focus'])&set(tp.REGION_MUSCLES['upper']))
        self.assertTrue(set(t['scored_focus'])&set(tp.REGION_MUSCLES['lower']))

    def test_emitted_focus_is_exactly_the_scored_focus(self):
        ranked=[{'muscle':m,'priority_score':100.} for m in MUSCLES]
        for t in self.score():
            session=tp._session_from_templates([t],ranked)
            self.assertEqual(session['primary_focus']+session['secondary_focus'],t['scored_focus'])

    def test_core_accessories_cannot_score_without_core(self):
        t=next(t for t in self.score({'Core':-50}) if t['session_type']=='Core + Accessories')
        self.assertIn('Core',t['scored_focus'])

    def test_fatigued_support_excluded_even_with_extreme_urgency(self):
        for t in self.score({'Back':1e9,'Biceps':1e9},{'Back':'FATIGUED','Biceps':'SUPPRESSED'}):
            self.assertFalse(set(t.get('scored_focus') or [])&{'Back','Biceps'})

    def test_only_safe_region_can_repeat_legitimately(self):
        states={m:'FATIGUED' for m in MUSCLES};states.update({m:'READY' for m in ['Quads','Glutes','Hamstrings']})
        scores=tp._score_session_templates([{'muscle':m,'priority_score':100} for m in MUSCLES],readiness(states),
                [{'focus':'Lower Body','selected_muscles':['Quads','Glutes','Hamstrings']}],tp.CALIBRATION_PROFILES['correlation'])
        self.assertEqual(next(t['session_type'] for t in scores if t['eligible']),'Lower Body')

    def test_capped_stimulus_retains_useful_urgency(self):
        with patch.object(tp,'strength_analytics',return_value=analytics()),patch.object(tp,'calculate_muscle_readiness',return_value=readiness()),patch.object(tp,'_muscle_priority_score',return_value=1000.):
            r=tp.build_training_priority(now=datetime(2026,7,15,11,tzinfo=timezone.utc),recommendation_history=[],calibration='balanced')
        self.assertTrue(all(0<row['bounded_stimulus_score']<=105 for row in r['ranked_muscles']))


class MovementFamilyRegressionTests(unittest.TestCase):
    def profiles(self):
        return [{"movement_id": name, "name": name, "muscle_groups": [muscle],
                 "performance": {"status": "usable"}}
                for name, muscle in [("Seated Row", "Back"), ("Biceps Curl", "Biceps"),
                                     ("Goblet Squat", "Quads"), ("Barbell Front Squat", "Quads")]]

    def test_distinct_unknown_families_cover_upper_pull_targets(self):
        from integrations.tonal.workout_prescription import _select_movements
        selected = _select_movements(self.profiles(), ["Back", "Biceps"], [])
        self.assertEqual({p["muscle_groups"][0] for p in selected}, {"Back", "Biceps"})

    def test_known_families_still_deduplicate(self):
        from integrations.tonal.workout_prescription import _select_movements
        self.assertEqual(len(_select_movements(self.profiles(), ["Quads"], [])), 1)

    def test_baseline_retains_original_family_defect_for_comparison(self):
        from integrations.tonal.workout_prescription import _select_movements
        selected = _select_movements(self.profiles(), ["Back", "Biceps"], [], legacy_unknown_family=True)
        self.assertEqual(len(selected), 1)

    def test_family_fix_does_not_admit_suppressed_movements(self):
        from integrations.tonal.workout_prescription import _select_movements
        profiles = self.profiles()
        profiles[0]["muscle_groups"].append("Triceps")
        selected = _select_movements(profiles, ["Back", "Biceps"], [], suppressed_muscles=["Triceps"])
        self.assertEqual([p["name"] for p in selected], ["Biceps Curl"])


if __name__ == "__main__":
    unittest.main()
