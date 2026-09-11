import json
import unittest
from datetime import date
from uuid import UUID
from unittest.mock import patch

from training_calibration_metrics import recovering_audit, calibration_metrics
from training_replay import _json_safe, ReplayContext, replay_day
import training_replay
from test_training_replay import _ReadOnlyConnection


def day(alternative=None):
    winner={'session_type':'Lower Body','eligible':True,'movement_eligible':True,'score':100,
            'program_need_score':80,'aggregated_muscles':['Quads'],
            'candidate_readiness':{'Quads':'READY','Glutes':'RECOVERING'}}
    alt={'session_type':'Upper Pull','eligible':True,'movement_eligible':True,'score':90,
         'program_need_score':75,'candidate_readiness':{'Back':'READY','Biceps':'READY'}}
    alt.update(alternative or {})
    return {'replay_date':'2026-07-15','recommendation':{'session_type':'Lower Body','selected_muscles':['Quads','Glutes'],'target_sets':8},
            'inputs':{'training':{'ranked_muscles':[{'muscle':'Quads','readiness_state':'READY'},
                                                 {'muscle':'Glutes','readiness_state':'RECOVERING'}],
                                  'template_scores':[winner,alt]}}}


class ViableAlternativeTests(unittest.TestCase):
    def test_viable_alternative_requires_actual_movement_eligibility(self):
        self.assertIsNone(recovering_audit(day({'movement_eligible':False}))['viable_alternative'])

    def test_viable_alternative_requires_comparable_need(self):
        audit=recovering_audit(day({'program_need_score':0}))
        self.assertIsNone(audit['viable_alternative'])
        self.assertEqual(audit['category'],'materially_stronger_program_need')

    def test_unscored_recovering_muscle_is_aggregation_inversion(self):
        audit=recovering_audit(day())
        self.assertEqual(audit['viable_alternative'],'Upper Pull')
        self.assertEqual(audit['category'],'template_aggregation_inversion')

    def test_repeat_count_is_not_longest_streak(self):
        days=[]
        for i,name in enumerate(['Lower Body','Lower Body','Upper Pull','Upper Pull']):
            d=day();d['replay_date']='2026-07-'+str(15+i);d['recommendation']['session_type']=name;days.append(d)
        self.assertEqual(calibration_metrics(days)['longest_identical_template_streak'],2)

    def test_uuid_outcomes_serialize_without_changing_observations(self):
        value=UUID('00000000-0000-0000-0000-000000000001')
        self.assertEqual(json.loads(json.dumps(_json_safe({'activity_id':value})))['activity_id'],str(value))

    def test_readonly_setup_failure_prevents_recommendation_execution(self):
        from contextlib import nullcontext
        from test_training_replay import _ReadOnlyCursor
        with patch.object(training_replay,'request_scoped_connection',return_value=nullcontext()), \
             patch.object(training_replay,'get_conn',return_value=_ReadOnlyConnection()), \
             patch.object(_ReadOnlyCursor,'execute',side_effect=RuntimeError('read-only rejected')), \
             patch.object(training_replay,'_replay_day') as engine:
            with self.assertRaises(RuntimeError):replay_day(ReplayContext.morning(date(2026,7,15)))
            engine.assert_not_called()
