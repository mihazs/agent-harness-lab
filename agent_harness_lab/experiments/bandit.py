"""Seeded Beta-Bernoulli simulation with exact delayed-reward attribution."""
from dataclasses import asdict
import math
import random
from .context_policies import PolicyArm


class ThompsonSampler:
    def __init__(self, arms, seed, max_selections):
        if not arms or len({a.arm_id for a in arms})!=len(arms) or type(max_selections) is not int or max_selections<=0:
            raise ValueError('invalid fixed arm set/budget')
        self.arms={a.arm_id:a for a in arms if a.eligible()}
        if not self.arms: raise ValueError('no approved read-only arm')
        self.posterior={a:[1,1] for a in self.arms}
        self.rng=random.Random(seed)
        self.max_selections=max_selections
        self.assignments={}; self.reward_events={}

    def select(self,task_id):
        if task_id in self.assignments: raise ValueError('task already assigned')
        if len(self.assignments)>=self.max_selections: raise ValueError('selection budget exhausted')
        state={k:list(v) for k,v in self.posterior.items()}
        arm=max(sorted(self.arms),key=lambda a:self.rng.betavariate(*self.posterior[a]))
        self.assignments[task_id]=dict(arm=arm,version=self.arms[arm].version,
            selection_state=state,reward=None,reward_provenance=None)
        return arm

    def reward(self,event_id,task_id,arm_version,outcome,provenance):
        if type(outcome) is not bool and outcome is not None: raise ValueError('reward must be verified binary or unknown')
        payload=(task_id,arm_version,outcome,provenance)
        if event_id in self.reward_events:
            if self.reward_events[event_id]!=payload: raise ValueError('conflicting reward event')
            return False
        if task_id not in self.assignments: raise ValueError('unknown delayed task')
        assignment=self.assignments[task_id]
        if assignment['version']!=arm_version: raise ValueError('policy version changed')
        if outcome is not None and provenance not in ('synthetic_oracle','executable_grader'):
            raise ValueError('reward provenance is unverified')
        if assignment['reward'] is not None:
            raise ValueError('task reward already applied')
        self.reward_events[event_id]=payload
        if outcome is None: return False
        assignment.update(reward=outcome,reward_provenance=provenance)
        self.posterior[assignment['arm']][0 if outcome else 1]+=1
        return True


def require_identifiable(design, *, propensities_verified=False, overlap=False):
    if design=='controlled_simulation': return True
    if design=='randomized_log' and propensities_verified and overlap: return True
    raise ValueError('counterfactual comparison is not identifiable from arbitrary observational logs')


def simulate(seed=7,tasks=200,scenario='stationary'):
    if type(tasks) is not int or not 1<=tasks<=10000 or scenario not in ('stationary','drift','sparse','delayed','harmful'):
        raise ValueError('invalid bounded simulation scenario')
    arms=[PolicyArm('none','v1',True),PolicyArm('lexical','v1',True),PolicyArm('reviewed_rule','v1',scenario!='harmful'),
          PolicyArm('unsafe','v1',False,False)]
    eligible=sorted(a.arm_id for a in arms if a.eligible())
    rng=random.Random(seed)
    # All potential outcomes are explicitly known only in this controlled simulator.
    outcomes=[]
    for i in range(tasks):
        probabilities={'none':.45,'lexical':.6,'reviewed_rule':.65}
        if scenario=='drift' and i>=tasks//2: probabilities={'none':.7,'lexical':.6,'reviewed_rule':.4}
        outcomes.append({a:rng.random()<probabilities[a] for a in eligible})
    dev=random.Random(seed+100000)
    development={a:sum(dev.random()<p for _ in range(100)) for a,p in [('none',.45),('lexical',.6),('reviewed_rule',.65)]}
    best=max(eligible,key=lambda a:development[a])
    reports={}
    for strategy in ('fixed_lexical','fixed_best_on_development','uniform','rule','thompson'):
        sampler=ThompsonSampler(arms,seed,tasks)
        choice_rng=random.Random(seed+3)
        success,verified,pending=0,0,[]
        for i in range(tasks):
            arm=(sampler.select(str(i)) if strategy=='thompson' else
                 'lexical' if strategy in ('fixed_lexical','rule') else
                 best if strategy=='fixed_best_on_development' else choice_rng.choice(eligible))
            outcome=outcomes[i][arm]
            visible=not (scenario=='sparse' and random.Random(seed+i+90000).random()<.8)
            success+=outcome; verified+=visible
            if strategy=='thompson':
                pending.append((i+3 if scenario=='delayed' else i,str(i),outcome if visible else None))
                due=[entry for entry in pending if entry[0]<=i]
                pending=[entry for entry in pending if entry[0]>i]
                for _,task,reward in due: sampler.reward('reward:'+task,task,'v1',reward,'synthetic_oracle')
        for _,task,reward in pending: sampler.reward('reward:'+task,task,'v1',reward,'synthetic_oracle')
        reports[strategy]=dict(tasks=tasks,simulated_successes=success,observed_rewards=verified,
                               unknown_rewards=tasks-verified,posterior=sampler.posterior if strategy=='thompson' else None)
    return dict(seed=seed,scenario=scenario,design='controlled_simulation',results=reports,
                real_task_effectiveness='not_run',rl_decision='insufficient_evidence',
                missing_evidence=['independently graded coding trajectories','held_out_retrieval_benefit',
                                  'privacy_license_training_access_and_explicit_budget'],
                automatic_deployment=False,training_started=False)
