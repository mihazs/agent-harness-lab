"""Conditional finite-bank WoR inference; declared assumptions are not authentication."""
from decimal import Context, Decimal, localcontext, ROUND_CEILING
from fractions import Fraction
from collections import Counter
import hashlib
import math
import secrets

from agent_harness_lab.private_journal import encoded, record_bytes
from .regressions import fields, sha256

PROFILE = 'conditional-finite-bank-sequential-v1'
METHOD = 'wor-hoeffding-predictable-rational-v1'
ESTIMAND = 'finite_bank_realized_potential_root_block_mean'
ASSUMPTIONS = ['root_potential_outcomes_independent_of_controller_permutation',
    'no_cross_root_interference_or_execution_order_effects', 'frozen_root_local_protocol']
MAX_ROOTS = 1000
MAX_TRIALS = 10000


def rational(value):
    if type(value) not in (int,float) or not math.isfinite(value): raise ValueError('finite decimal constant required')
    return Fraction(str(value))


def packed(value): return [value.numerator,value.denominator]


def unpack(value):
    if (not isinstance(value,list) or len(value)!=2 or any(type(v) is not int or v.bit_length()>16384 for v in value)
            or value[1]<=0): raise ValueError('bounded exact rational required')
    result=Fraction(*value)
    if packed(result)!=value: raise ValueError('noncanonical exact rational')
    return result


def hashed(value): return hashlib.sha256(encoded(value)).hexdigest()


def validate(spec):
    value=spec.sequential
    fields(value,'schema_version method estimand assumptions max_roots root_seconds arm_seconds')
    roots={r for _,r,_ in spec.tasks}
    if (type(value['schema_version']) is not int or value['schema_version']!=1 or value['method']!=METHOD
            or value['estimand']!=ESTIMAND or value['assumptions']!=ASSUMPTIONS
            or spec.profile!='descriptive_smoke' or spec.partition!='development'
            or not 0<len(spec.tasks)<=MAX_ROOTS or spec.precision_half_width is not None):
        raise ValueError('unsupported conditional sequential design or fixed precision target')
    maximum=value['max_roots']
    if type(maximum) is not int or not spec.min_roots<=maximum<=len(roots): raise ValueError('invalid sequential root ceiling')
    for key in ('root_seconds','arm_seconds'):
        item=value[key]
        if type(item) not in (float,int) or not 0<item<=300 or not math.isfinite(item):
            raise ValueError('finite frozen sequential role/root quotas required')
    largest=max(Counter(r for _,r,_ in spec.tasks).values())
    if planned_trials(spec)>MAX_TRIALS:raise ValueError('sequential trial metadata ceiling exceeded')
    if value['root_seconds']<=4*largest*spec.repeats*value['arm_seconds']:
        raise ValueError('root quota must reserve both arms, graders and setup')


def validate_selection(source):
    tasks=source.get('tasks')
    if (source.get('profile','descriptive_smoke')!='descriptive_smoke' or not isinstance(tasks,list)
            or not 0<len(tasks)<=MAX_ROOTS or any(not isinstance(t,dict) or t.get('split') not in ('development','regression') for t in tasks)):
        raise ValueError('conditional sequential execution requires exposed development/regression tasks')


def planned_trials(spec):
    counts=Counter(root for _,root,_ in spec.tasks)
    return 2*spec.repeats*sum(sorted(counts.values(),reverse=True)[:spec.sequential['max_roots']])


def config(max_roots,root_seconds=10,arm_seconds=1):
    return dict(schema_version=1,method=METHOD,estimand=ESTIMAND,assumptions=list(ASSUMPTIONS),
        max_roots=max_roots,root_seconds=root_seconds,arm_seconds=arm_seconds)


def membership(spec, raw=None):
    if spec.cohort_manifest_sha256 is None:
        if raw is not None: raise ValueError('unexpected sequential cohort bytes')
        return []
    from .cohorts import MAX_BYTES, _manifest
    value,pin=record_bytes(raw,MAX_BYTES)
    if pin!=spec.cohort_manifest_sha256: raise ValueError('sequential cohort bytes differ from plan')
    result=_manifest(value,spec.tasks)['cohorts']
    if any(c['qualification']['plan_sha256']==spec.digest for c in result): raise ValueError('same-plan cohort qualification')
    return result


def channels(spec, cohorts):
    result={}
    for family,_ in spec.family_weights:
        result['family:'+family]=dict(required=True,margin=spec.family_margin,
            tasks={t for t,_,f in spec.tasks if f==family})
    for c in cohorts:
        if c['required']:
            result['cohort:'+c['cohort_id']]=dict(required=True,margin=c['margin'],tasks={m['task_id'] for m in c['members']})
    return result


def input_pin(manifest_sha256,spec,fixtures):
    return hashed(dict(manifest_sha256=manifest_sha256,fixtures=fixtures,baseline_sha256=spec.baseline_hash,
        candidate_sha256=spec.candidate_hash,oracle_sha256=spec.grader_hash,runtime_sha256=spec.runtime_hash))


def engine_pin():
    from .decision_receipts import engine
    return hashed(engine())


def create_binding(spec, cohorts, *, inputs_sha256='0'*64):
    spec.validate();sha256(inputs_sha256)
    roots=sorted({r for _,r,_ in spec.tasks})
    # Direct unbiased draws, not a user-chosen PRNG seed pretending to be uniform over N!.
    for index in range(len(roots)-1,0,-1):
        other=secrets.randbelow(index+1); roots[index],roots[other]=roots[other],roots[index]
    alpha=rational(spec.alpha)/len(channels(spec,cohorts))
    lambdas=[]
    for i in range(1,len(roots)+1):
        # The actual positive predictable binary value IS the tuning parameter.
        # The theorem does not require exact approximation to the paper's suggested tuning.
        lam=min(.5,math.sqrt(2*(math.log(2)-math.log(alpha.numerator)+math.log(alpha.denominator))/(i*math.log(i+1))))
        lambdas.append(packed(Fraction(lam)))
    return dict(schema_version=1,profile=PROFILE,plan_sha256=spec.digest,nonce=secrets.token_hex(32),
        root_order=roots,lambdas=lambdas,engine_sha256=engine_pin(),
        inputs_sha256=inputs_sha256,sampling='controller_unbiased_fisher_yates_v1',numeric_policy='exact_rational_outward_log_60_v1')


def validate_binding(spec,binding,*,current_engine=True):
    fields(binding,'schema_version profile plan_sha256 nonce root_order lambdas engine_sha256 inputs_sha256 sampling numeric_policy')
    roots={r for _,r,_ in spec.tasks}
    if (type(binding['schema_version']) is not int or binding['schema_version']!=1 or binding['profile']!=PROFILE
            or binding['plan_sha256']!=spec.digest or binding['sampling']!='controller_unbiased_fisher_yates_v1'
            or binding['numeric_policy']!='exact_rational_outward_log_60_v1'
            or not isinstance(binding['root_order'],list) or len(binding['root_order'])!=len(roots)
            or any(not isinstance(r,str) for r in binding['root_order']) or set(binding['root_order'])!=roots
            or not isinstance(binding['lambdas'],list) or len(binding['lambdas'])!=len(roots)):
        raise ValueError('sequential sampling binding changed')
    for key in ('nonce','engine_sha256','inputs_sha256'):sha256(binding[key])
    for pair in binding['lambdas']:
        lam=unpack(pair)
        if not 0<lam<=Fraction(1,2) or lam.numerator.bit_length()>53 or lam.denominator.bit_length()>64 or lam.denominator & (lam.denominator-1):
            raise ValueError('invalid bounded binary64 predictable tuning table')
    if current_engine and binding['engine_sha256']!=engine_pin(): raise ValueError('sequential engine changed; cold continuation unsupported')


def schedule(spec,binding=None):
    roots=(sorted({r for _,r,_ in spec.tasks}) if binding is None else binding['root_order'])[:spec.sequential['max_roots']]
    salt='unexecuted-declaration' if binding is None else hashed(binding)
    result=[];plan_pin=spec.digest
    for root in roots:
        for repetition in range(spec.repeats):
            for task,_,family in (t for t in spec.tasks if t[1]==root):
                bit=int(hashlib.sha256(f'{spec.seed}:{root}:{task}'.encode()).hexdigest(),16)
                arms=('baseline','candidate') if (bit+repetition)%2==0 else ('candidate','baseline')
                for arm in arms:
                    ident=hashlib.sha256(f'{plan_pin}:{salt}:{task}:{repetition}:{arm}'.encode()).hexdigest()
                    result.append(dict(trial_id=ident,task=task,root=root,family=family,repetition=repetition,
                        version=arm,plan_digest=plan_pin))
    return result


def log_upper(alpha):
    with localcontext(Context(prec=60,rounding=ROUND_CEILING)):
        ratio=Decimal(2*alpha.denominator)/Decimal(alpha.numerator)
        # ln is correctly rounded HALF_EVEN; monotonicity + outward neighbor encloses
        # the logarithm of the exact rational, including the input division rounding.
        return Fraction(ratio.ln().next_plus())


def outward(value,upper):
    result=float(value)
    if (Fraction(result)<value if upper else Fraction(result)>value):
        result=math.nextafter(result,math.inf if upper else -math.inf)
    return result


def endpoint(values,N,alpha,lambdas):
    s=a=b=q=Fraction(0);lo,hi=Fraction(-1),Fraction(1);width=None
    logarithm=log_upper(alpha)
    for i,x in enumerate(values,1):
        lam=unpack(lambdas[i-1]);a+=lam*(x+s/(N-i+1));b+=lam*N/(N-i+1);q+=lam*lam/2;s+=x
        width=(q+logarithm)/b
        lo=max(lo,(a-q-logarithm)/b,(s-(N-i))/N)
        hi=min(hi,(a+q+logarithm)/b,(s+(N-i))/N)
        if lo>hi:return None
    return lo,hi,dict(low=outward(lo,False),high=outward(hi,True),exact_low=packed(lo),exact_high=packed(hi),
        roots=len(values),population_roots=N,cs_center=float(a/b) if b else None,
        sample_mean=float(s/len(values)) if values else None,half_width=float(width) if width is not None else None)


def _missing_identification(spec, records, binding, cohorts):
    """Descriptive finite-bank completion range, never a confidence sequence or authority."""
    expected=schedule(spec,binding); by_id={row['trial_id']:row for row in expected}
    if ([row.trial_id for row in records] != [row['trial_id'] for row in expected[:len(records)]]
            or any((row.baseline_hash,row.candidate_hash,row.grader_hash,row.runtime_hash) !=
                   (spec.baseline_hash,spec.candidate_hash,spec.grader_hash,spec.runtime_hash) for row in records)):
        raise ValueError('ordered, matching sequential prefix required')
    data={}
    for row in records:
        trial=by_id[row.trial_id]
        valid=(row.status=='graded' and row.provenance=='executable_grader' and type(row.outcome) is bool
               and type(row.critical_regression) is bool)
        data[(trial['task'],trial['repetition'],trial['version'])]=int(row.outcome) if valid else None
    def endpoint(task_ids):
        roots={}
        for task,root,_ in spec.tasks:
            if task not in task_ids:continue
            for repeat in range(spec.repeats):
                baseline=data.get((task,repeat,'baseline'));candidate=data.get((task,repeat,'candidate'))
                lo=(candidate if candidate is not None else 0)-(baseline if baseline is not None else 1)
                hi=(candidate if candidate is not None else 1)-(baseline if baseline is not None else 0)
                roots.setdefault(root,[]).append((lo,hi))
        if not roots:raise ValueError('nonempty endpoint required')
        low=sum((Fraction(sum(pair[0] for pair in rows),len(rows)) for rows in roots.values()),Fraction())/len(roots)
        high=sum((Fraction(sum(pair[1] for pair in rows),len(rows)) for rows in roots.values()),Fraction())/len(roots)
        return low,high
    channel_defs=channels(spec,cohorts);ranges={name:endpoint(item['tasks']) for name,item in channel_defs.items()}
    weights={f:rational(w) for f,w in spec.family_weights};total=sum(weights.values())
    low=sum((w*ranges['family:'+f][0] for f,w in weights.items()),Fraction())/total
    high=sum((w*ranges['family:'+f][1] for f,w in weights.items()),Fraction())/total
    def view(pair):return dict(low=outward(pair[0],False),high=outward(pair[1],True),
                               exact_low=packed(pair[0]),exact_high=packed(pair[1]))
    return dict(estimand=ESTIMAND,primary=view((low,high)),endpoints={name:view(pair) for name,pair in ranges.items()},
        interpretation='finite_bank_completion_range_not_a_confidence_sequence',
        input_authority='caller_declared_unverified',automatic_promotion=False,publication_allowed=False)


def analyze(spec,records,binding,*,cohort_json=None,include_missing_bounds=False):
    spec.validate();validate_binding(spec,binding,current_engine=False)
    cohorts=membership(spec,cohort_json);groups=channels(spec,cohorts)
    expected=schedule(spec,binding);byid={r['trial_id']:r for r in expected}
    base=dict(profile=PROFILE,analysis_mode='anytime_finite_bank',estimand=ESTIMAND,assumptions_status='declared_unverified',
        sampling_sha256=hashed(binding),sampling_authority='unverified_numeric_declaration',power='not_estimated',future_expected_quality='unknown',
        root_independence='unverified',automatic_promotion=False,publication_allowed=False)
    def finish(status,reason,**extra):return dict(base,status=status,reason=reason,**extra)
    if len({r.trial_id for r in records})!=len(records) or any(r.trial_id not in byid for r in records):
        return finish('incomplete','duplicate_or_foreign_trial')
    for r in records:
        if ((r.baseline_hash,r.candidate_hash,r.grader_hash,r.runtime_hash)!=(spec.baseline_hash,spec.candidate_hash,spec.grader_hash,spec.runtime_hash)
                or type(r.critical_regression) is not bool):return finish('blocked','provenance_mismatch')
    if any(r.critical_regression for r in records):
        if {r.trial_id for r in records}!={r['trial_id'] for r in expected[:len(records)]}:return finish('incomplete','nonprefix_critical_record')
        return finish('rejected','critical_regression')
    roots={byid[r.trial_id]['root'] for r in records};order=binding['root_order'][:len(roots)]
    if roots!=set(order) or {r.trial_id for r in records}!={r['trial_id'] for r in expected if r['root'] in roots}:
        if include_missing_bounds is True and [r.trial_id for r in records]==[r['trial_id'] for r in expected[:len(records)]]:
            return finish('incomplete','partial_or_nonprefix_root',missing_outcome_bounds=_missing_identification(spec,records,binding,cohorts))
        return finish('incomplete','partial_or_nonprefix_root')
    if any(type(r.outcome) is not bool or r.status!='graded' or r.provenance!='executable_grader' for r in records):
        if include_missing_bounds is True and [r.trial_id for r in records]==[r['trial_id'] for r in expected[:len(records)]]:
            return finish('incomplete','unknown_root_outcome',missing_outcome_bounds=_missing_identification(spec,records,binding,cohorts))
        return finish('incomplete','unknown_root_outcome')
    data={(byid[r.trial_id]['task'],byid[r.trial_id]['repetition'],byid[r.trial_id]['version']):r.outcome for r in records}
    alpha=rational(spec.alpha)/len(groups);observed={};exact={}
    for name,group in groups.items():
        population={root for task,root,_ in spec.tasks if task in group['tasks']};values=[]
        for root in order:
            tasks=[t for t,r,_ in spec.tasks if r==root and t in group['tasks']]
            if tasks:values.append(Fraction(sum(int(data[t,k,'candidate'])-int(data[t,k,'baseline']) for t in tasks for k in range(spec.repeats)),len(tasks)*spec.repeats))
        bounds=endpoint(values,len(population),alpha,binding['lambdas'])
        if bounds is None:return finish('inconclusive','empty_running_intersection')
        lo,hi,item=bounds;exact[name]=(lo,hi);observed[name]=dict(item,margin=group['margin'],required=True,
            disposition='harm' if hi< -rational(group['margin']) else 'insufficient_roots' if len(values)<spec.min_roots else 'supported' if lo>=-rational(group['margin']) else 'inconclusive')
    weights={f:rational(w) for f,w in spec.family_weights};total=sum(weights.values());weights={f:w/total for f,w in weights.items()}
    low=sum(w*exact['family:'+f][0] for f,w in weights.items());high=sum(w*exact['family:'+f][1] for f,w in weights.items())
    primary=dict(low=outward(low,False),high=outward(high,True),exact_low=packed(low),exact_high=packed(high))
    enough=len(order)>=spec.min_roots and all(v['roots']>=spec.min_roots for v in observed.values())
    harm=any(v['disposition']=='harm' for v in observed.values()) or high< -rational(spec.family_margin)
    good=enough and low>rational(spec.effect_margin) and all(v['disposition']=='supported' for v in observed.values())
    status='rejected' if harm else 'supported' if good else 'inconclusive'
    reason='preservation_or_family_harm' if harm else 'sequential_benefit' if good else 'insufficient_family_evidence' if not enough else 'boundary_not_crossed'
    return finish(status,reason,primary=primary,endpoints=observed,roots=len(order),trials=len(records),
        family_weights={f:packed(w) for f,w in weights.items()},multiplicity=dict(method='Bonferroni_families_and_required_cohorts',
            endpoints=len(groups),alpha=packed(alpha),primary='derived_family_interval_sum'),unobserved_roots=len(binding['root_order'])-len(order))
