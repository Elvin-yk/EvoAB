"""Run upstream ResearchCampaign, SelfHarness, selection and memory unchanged."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import signal
import time

from .config import ResearchConfig


def implementation_identity():
    import importlib.metadata
    import PhysicalRSI_core.self_harness as core
    from PhysicalRSI_core.infra.storage import file_digest
    return {'version':importlib.metadata.version('physicalrsi'),
            'core_sha256':file_digest(Path(core.__file__)),
            'bridge_sha256':file_digest(Path(__file__)),
            'adapters_sha256':file_digest(Path(__file__).with_name('adapters.py'))}


class CatalogLanguageClient:
    def __init__(self,config):self.config=config
    def identity(self):
        from evoab.provider.catalog import ModelClient
        c=ModelClient(self.config.model,reasoning_effort=self.config.reasoning_effort)
        try:return {'kind':'evoab-catalog-system2/v1',**c.describe()}
        finally:c.close()
    def complete(self,messages,*,max_output_tokens):
        from evoab.provider.catalog import ModelClient
        c=ModelClient(self.config.model,reasoning_effort=self.config.reasoning_effort,max_tokens=max_output_tokens)
        try:r=c.complete(messages)
        finally:c.close()
        if r.error:raise RuntimeError('System 2 model call failed: '+r.error)
        return {'text':r.content,'usage':r.usage}


class LanguageReflector:
    """Bind a model's hypothesis to verified development evidence only."""
    def __init__(self,client):self.client=client
    def identity(self):return {'kind':'evoab-development-reflector/v1','client':self.client.identity()}
    def reflect(self,research_context,development,output):
        from PhysicalRSI_core.infra.storage import atomic_json
        from PhysicalRSI_core.self_harness.language_proposer import _json_text
        output.mkdir(parents=True,exist_ok=True)
        rows=development.get('evaluations',[])
        # Project only the evidence approved by upstream ResearchCampaign.
        summary=[{k:r.get(k) for k in ('outcome','verdict','trial')} for r in rows]
        messages=[{'role':'user','content':json.dumps({
            'instruction':'Reflect on DEVELOPMENT trials only. Return JSON {"hypothesis":"a concrete, testable improvement hypothesis"}. Do not claim an improvement was proven.',
            'scope':research_context['scope'],'development':summary,
            'prior_hypotheses':research_context['lessons']},ensure_ascii=False)}]
        atomic_json(output/'prompt.json',messages)
        reply=self.client.complete(messages,max_output_tokens=1024)
        atomic_json(output/'response.json',reply)
        value=json.loads(_json_text(reply['text']))
        hypothesis=value.get('hypothesis')
        if not isinstance(hypothesis,str) or not hypothesis.strip():raise ValueError('Reflection requires a hypothesis')
        lesson={'conditions':research_context['scope'],'hypothesis':hypothesis,
                'required_checks':['fresh-paired-validation'], 'counterexample_checks':[],
                'evidence':development['evidence']}
        return {'schema':'physicalrsi.memory-delta/v1','operations':[{
            'kind':'add','lesson_id':f'round-{research_context["index"]+1}-hypothesis','lesson':lesson}]}


class OriginalSelector:
    def identity(self):
        from PhysicalRSI_core.self_harness import selection
        from PhysicalRSI_core.infra.storage import file_digest
        return {'kind':'physicalrsi-original-selector','implementation':file_digest(Path(selection.__file__))}
    def select(self,*args,**kwargs):
        from PhysicalRSI_core.self_harness.selection import select_survivor
        return select_survivor(*args,**kwargs)


def run(config: ResearchConfig,root: Path,runs_root: Path):
    from PhysicalRSI_core.infra.storage import atomic_json, file_digest, read_json
    from PhysicalRSI_core.self_harness.artifacts import CLOSURE, verify_harness
    from PhysicalRSI_core.lineage import HarnessState
    from PhysicalRSI_core.infra.trial_quota import TrialQuota
    from PhysicalRSI_core.self_harness.research_memory import ResearchMemory
    from PhysicalRSI_core.self_harness.research_campaign import ResearchCampaign
    from PhysicalRSI_core.self_harness import SelfHarness
    from PhysicalRSI_core.self_harness.evaluation import ExperimentEvaluator
    from PhysicalRSI_core.self_harness.protocol import CaseProtocol, PreregisteredSuite
    from PhysicalRSI_core.self_harness.search_space import ParameterSpace, SearchField
    from PhysicalRSI_core.self_harness.language_proposer import LanguageMutationEvolver
    from PhysicalRSI_core.self_harness.rollout import RolloutProposer
    from PhysicalRSI_core.embodiment import Revision
    from PhysicalRSI_core.experiments import Budget
    from .adapters import RobotSuite, ACTIVE_TRIALS

    root=root.resolve();root.mkdir(parents=True,exist_ok=True)
    expected={'config':config.model_dump(),'implementation':implementation_identity()}
    spec=root/'research.json'
    if spec.exists() and read_json(spec)!=expected:raise ValueError('Research configuration or implementation changed; create a new campaign')
    atomic_json(spec,expected)
    initial=root/'initial';initial.mkdir(exist_ok=True)
    for kind in CLOSURE:
        path=initial/(kind+'.json')
        if not path.exists():atomic_json(path,{'motion_steps':6,'clearance_m':.05,'history_turns':3} if kind=='control'
            else {'kind':kind,'implementation':expected['implementation']})
    parent={'id':'initial','root':str(initial),
            'components':{k:{k+'.json':file_digest(initial/(k+'.json'))} for k in CLOSURE}}
    verify_harness(parent)
    state=HarnessState(root/'lineage');memory=ResearchMemory(root/'memory')
    scope={'integration':config.integration,'task':config.task,'observation':'rgb-proprioception-tools'}
    bootstrap=root/'bootstrap.json'
    if not bootstrap.exists():atomic_json(bootstrap,{'hypothesis':'Verify measured motion and visual effects before continuing a manipulation plan.'})
    memory_revision=memory.write({'bootstrap':{'conditions':scope,'hypothesis':read_json(bootstrap)['hypothesis'],
        'required_checks':['fresh-paired-validation'],'counterexample_checks':[],
        'evidence':{str(bootstrap):file_digest(bootstrap)}}})
    quota=TrialQuota(root/'quota',max_trials=config.max_trials)
    space=ParameterSpace([SearchField('control','control.json',('motion_steps',),1,12,0),
        SearchField('control','control.json',('clearance_m',),.01,.15,3),
        SearchField('control','control.json',('history_turns',),1,8,0)])
    client=CatalogLanguageClient(config)
    class CheckedProposer(RolloutProposer):
        def _propose(self, *args, **kwargs):
            children=super()._propose(*args, **kwargs)
            for child in children:
                child['required_checks']=['fresh-paired-validation']
            return children
    def build_loop(directory,context):
        index=context['index'];base=config.seed+index*10000
        splits={
            'development':{config.task:[{'seed':base+i} for i in range(config.development_cases)]},
            'validation':{config.task:[{'seed':base+1000+i} for i in range(config.validation_cases)]},
            'test':{config.task:[{'seed':config.seed+9000000+i} for i in range(config.test_cases)]}}
        protocol=CaseProtocol(root/'protocols'/f'round-{index+1:04d}',splits=splits)
        suite=PreregisteredSuite(RobotSuite(config,root,runs_root),protocol)
        evaluator=ExperimentEvaluator(suite=suite,budgets={config.task:Budget(config.max_turns,config.episode_seconds)},
            scope='simulation',system2=Revision('evoab-physicalrsi',expected['implementation']['bridge_sha256']),
            quota=quota,infrastructure_retries=0)
        evolver=LanguageMutationEvolver(client=client,space=space,max_candidates=config.candidates,
            memory=memory,max_attempts=2,instructions='Improve the declared robot skill control parameters using observed development failures. Preserve the exact observation and model interfaces.')
        return SelfHarness(directory,state=state,proposer=CheckedProposer(evaluator,evolver),
            evaluator=evaluator,selector=OriginalSelector(),scope='simulation',max_candidates=config.candidates,
            round_context=context,
            profile={'tasks':{config.task:{'weight':1,'episodes':config.validation_cases,'score_range':[0,1],'maximum_regression':0}},
                     'minimum_gain':0,'tie_tolerance':0,'infrastructure_retries':0,'statistics':{'method':'paired-permutation','alpha':.05,
                     'max_comparisons':config.rounds*config.candidates,'resamples':2000}},
            protocol={'identity':evaluator.revision,'evaluation_kind':'simulation_official_environment'})
    campaign=ResearchCampaign(root/'campaign',state=state,build_loop=build_loop,memory=memory,
        memory_revision=memory_revision,context=scope,reflector=LanguageReflector(client),max_rounds=config.rounds)
    atomic_json(root/'status.json',{'state':'running','started_at':time.time()})
    try:
        result=campaign.run(parent)
        atomic_json(root/'campaign-result.json',result)
        test=campaign.report_test(parent,require_completed=True) if result['status']=='completed' else None
        if test is not None:atomic_json(root/'test-report.json',test)
        atomic_json(root/'status.json',{'state':result['status'],'finished_at':time.time(),
                    'completed_rounds':result['completed_rounds']})
        return result
    except BaseException as error:
        atomic_json(root/'status.json',{'state':'interrupted' if isinstance(error,KeyboardInterrupt) else 'error',
                    'error':str(error),'finished_at':time.time()})
        raise
    finally:
        for trial in ACTIVE_TRIALS:
            try:trial.close()
            except Exception:pass


def main(argv=None):
    parser=argparse.ArgumentParser(description='Run a full PhysicalRSI research campaign over EvoAB environments')
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--runs-root',type=Path,required=True)
    args=parser.parse_args(argv)
    from evoab.services.deployment.process import cancellation_signals,enable_child_reaping
    enable_child_reaping()
    with cancellation_signals():
        result=run(ResearchConfig.model_validate_json(args.config.read_text()),args.root,args.runs_root)
    print(json.dumps({'status':result['status'],'rounds':result['completed_rounds']},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
