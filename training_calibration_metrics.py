"""Observable selection diagnostics, distinct from observational outcomes."""
from collections import Counter
from itertools import groupby
from statistics import mean, median, pstdev
from integrations.tonal.program_balance import VIABLE_NEED_MARGIN, ELEVATED_COVERAGE
from integrations.tonal.muscle_readiness import PROGRAMMING_MUSCLES


def _mean(values): return round(mean(values), 3) if values else None


def recovering_audit(day):
    recommendation=day['recommendation']; selected=recommendation.get('selected_muscles') or []
    inputs=day.get('inputs',{}).get('training') or {}
    muscles={r['muscle']:r for r in inputs.get('ranked_muscles') or []}
    recovering=[m for m in selected if muscles.get(m,{}).get('readiness_state')=='RECOVERING']
    scores=inputs.get('template_scores') or []
    winner=next((t for t in scores if t['session_type']==recommendation.get('session_type')),None)
    feasible=[t for t in scores if t.get('eligible') and t.get('movement_eligible')]
    safer=[t for t in feasible if t is not winner
           and any(s in ('READY','FRESH') for s in t.get('candidate_readiness',{}).values())
           and sum(s=='RECOVERING' for s in t.get('candidate_readiness',{}).values())<len(recovering)]
    need=winner.get('program_need_score',0.) if winner else 0.
    viable=[t for t in safer if t.get('program_need_score',0.)>=need-VIABLE_NEED_MARGIN]
    alternative=max(viable,key=lambda t:t['score']) if viable else None
    # Older stored replays carried score values but not muscle identities.
    # Retain a compatibility fallback without using it for new diagnostics.
    unscored_recovering = []
    if winner:
        for muscle in recovering:
            if 'aggregated_muscles' in winner:
                scored = muscle in winner['aggregated_muscles']
            else:
                scored = muscles.get(muscle, {}).get('priority_score') in winner.get('aggregated_components', [])
            if not scored:
                unscored_recovering.append(muscle)
    category=None
    if recovering:
        if not safer: category='no_viable_ready_fresh_alternative'
        elif not viable: category='materially_stronger_program_need'
        elif unscored_recovering:
            category='template_aggregation_inversion'
        elif winner and winner.get('session_type')=='Lower Body' and sum(muscles.get(m,{}).get('history_stimulus_score',0)>=100 for m in selected)>=2:
            category='correlated_regional_urgency_inversion'
        elif winner and alternative and alternative['score']+alternative.get('rotation_penalty',0)>winner['score']+winner.get('rotation_penalty',0):
            category='rotation_inversion'
        else: category='other_competitive_alternative'
    return {'recovering_muscles':recovering,'viable_alternative':alternative['session_type'] if alternative else None,
            'viable_alternative_score':alternative['score'] if alternative else None,
            'category':category,'viable_alternative_count':len(viable),
            'viability_rule':'movement-eligible, fewer recovering muscles, ready/fresh present, program need within '+str(VIABLE_NEED_MARGIN)}


def calibration_metrics(days):
    gaps={};boundary={}
    for muscle in PROGRAMMING_MUSCLES:
        indexes=[i for i,d in enumerate(days) if muscle in (d['recommendation'].get('selected_muscles') or [])]
        limits=[-1]+indexes+[len(days)]
        spans=[(b-a-1,a,b) for a,b in zip(limits,limits[1:])]
        length,left,right=max(spans)
        gaps[muscle]={'longest':length,'median':median([s[0] for s in spans]),'selected_days':len(indexes)}
        boundary[muscle]={'gap_days':length,'last_selected':days[left]['replay_date'] if left>=0 else None,
                          'next_selected':days[right]['replay_date'] if right<len(days) else None,
                          'first_gap_day':days[left+1]['replay_date'] if length else None,
                          'last_gap_day':days[right-1]['replay_date'] if length else None}
    names=[d['recommendation'].get('session_type') for d in days]
    runs=[];offset=0
    for name,group in groupby(names):
        n=len(list(group));runs.append({'template':name,'length':n,'start':days[offset]['replay_date'],'end':days[offset+n-1]['replay_date']});offset+=n
    overlap=[];counts=[];actual_resolution=[];coverage={m:[] for m in PROGRAMMING_MUSCLES};audits=[]
    modalities=Counter();conditioning=[];durations=[];volume_ratios=[];categories=Counter()
    for i,day in enumerate(days):
        r=day['recommendation'];current=set(r.get('selected_muscles') or []);counts.append(len(current))
        if i:
            prev=set(days[i-1]['recommendation'].get('selected_muscles') or [])
            if prev and current:overlap.append(len(prev&current)/min(len(prev),len(current)))
        audit=recovering_audit(day);audits.append({'date':day['replay_date'],**audit})
        if audit['category']:categories[audit['category']]+=len(audit['recovering_muscles'])
        for row in (day.get('inputs',{}).get('training') or {}).get('ranked_muscles') or []:
            muscle=row['muscle'];coverage[muscle].append(row.get('coverage_score',row.get('program_coverage_score',0.)))
            c=row.get('coverage_diagnostics') or {}
            if c.get('actual_primary_gap_days',99)<=1:
                actual_resolution.append({'date':day['replay_date'],'muscle':muscle,'coverage_score':coverage[muscle][-1],
                                          'days_since_actual_primary':c.get('actual_primary_gap_days')})
        session=((day.get('diagnostics') or {}).get('training') or {}).get('session') or {}
        dose=session.get('dose_diagnostics') or {};hist=dose.get('historical') or {}
        if hist.get('median_volume') and r.get('target_volume_lb') is not None:
            volume_ratios.append(r['target_volume_lb']/hist['median_volume'])
        if r.get('estimated_duration_min') is not None:durations.append(r['estimated_duration_min'])
        activity=r.get('activity_plan') or {}
        sessions=activity.get('recommended_sessions') or activity.get('sessions') or []
        modalities.update(s.get('modality') for s in sessions)
        conditioning.append(sum(s.get('duration_minutes',0) for s in sessions))
    selected_counts=[g['selected_days'] for g in gaps.values()]
    def region(name):
        if name=='Lower Body':return 'lower'
        if name in ('Upper Pull','Upper Push','Upper Mixed','Chest + Biceps'):return 'upper'
        return name
    regions=[region(n) for n in names]
    sets=[d['recommendation']['target_sets'] for d in days if d['recommendation'].get('target_sets') is not None]
    return {'gap_days':gaps,'gap_boundaries':boundary,'longest_identical_template_streak':max((r['length'] for r in runs),default=0),
            'template_runs':runs,'overlap':{'mean':_mean(overlap),'median':median(overlap) if overlap else None,
                                           'full_overlap_pairs':sum(x==1 for x in overlap),'pairs':len(overlap)},
            'recovering_audit':{'selections_with_viable_alternative':sum(len(a['recovering_muscles']) for a in audits if a['viable_alternative']),
                                'days_with_viable_alternative':sum(bool(a['viable_alternative']) for a in audits),
                                'categories':dict(categories),'days':audits},
            'coverage_need':{m:{'mean':_mean(v),'peak':max(v,default=0),'elevated_days':sum(x>=ELEVATED_COVERAGE for x in v)} for m,v in coverage.items()},
            'coverage_after_actual_primary':actual_resolution,
            'dose':{'median_sets':median(sets) if sets else None,'mean_volume_ratio':_mean(volume_ratios),'mean_duration_minutes':_mean(durations)},
            'activity':{'modality_distribution':dict(modalities),'mean_conditioning_minutes':_mean(conditioning)},
            'pathology':{'muscle_count_cv':round(pstdev(selected_counts)/mean(selected_counts),3) if mean(selected_counts) else None,
                         'abab_template_windows':sum(names[i]==names[i-2] and names[i-1]==names[i-3] and names[i]!=names[i-1] for i in range(3,len(names))),
                         'upper_lower_ping_pong_triples':sum(regions[i]==regions[i-2] and {regions[i],regions[i-1]}=={'upper','lower'} for i in range(2,len(regions))),
                         'full_body_days':names.count('Full Body'),'accessory_days':names.count('Core + Accessories'),
                         'strength_days':sum(n not in ('Rest','Active Recovery',None) for n in names),'mean_selected_muscles':_mean(counts)}}
