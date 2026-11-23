"""Launch and inspect multi-episode PhysicalRSI jobs from Studio and CLI."""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import re
import sys
import time
from uuid import uuid4

from evoab.research.physicalrsi.config import ResearchConfig
from evoab.workbench import jobs, benchmarks
from evoab.workbench.trash import hidden_research_ids


def directory(runs_root: Path,ref: str) -> Path:
    if not re.fullmatch(r'research-[a-f0-9]{12}',ref):raise ValueError('Unknown research campaign')
    if ref in hidden_research_ids(runs_root):raise ValueError('Research campaign is in the recycle bin; restore it first')
    path=runs_root/'.studio'/'research'/ref
    if not path.is_dir() or path.is_symlink() or path.parent.is_symlink():raise ValueError('Unknown research campaign')
    return path


def validate(config: ResearchConfig):
    if importlib.util.find_spec('physicalrsi') is None:raise ValueError('Install evoab[physicalrsi] first')
    from evoab.provider.catalog import presets
    if config.model not in {p['name'] for p in presets()}:raise ValueError('Select an existing model preset')
    if config.integration not in {'metaworld','robodojo','libero_pro','libero_plus','calvin','robotwin'}:
        raise ValueError('This adapter currently supports prepared manipulation simulators only')
    runtime=benchmarks.runtime_for_launch(config.integration,'default')
    if runtime is None:raise ValueError('Prepare the benchmark first')
    if not benchmarks.task_info(config.integration,'default',config.task):raise ValueError('Select a prepared benchmark task')
    needed=config.rounds*(config.development_cases+(config.candidates+1)*config.validation_cases)+config.test_cases
    if config.max_trials<needed:raise ValueError(f'Trial budget must allow at least {needed} episodes')


def start(runs_root: Path,payload: dict):
    config=ResearchConfig.model_validate(payload);validate(config)
    name='research-'+uuid4().hex[:12]
    root=runs_root/'.studio'/'research'/name;root.mkdir(parents=True)
    (root/'request.json').write_text(config.model_dump_json(indent=2))
    spawn(root,runs_root)
    return {'research_id':name}


def spawn(root: Path,runs_root: Path):
    return jobs.start([sys.executable,'-m','evoab.research.physicalrsi.campaign',
        '--config',str(root/'request.json'),'--root',str(root),'--runs-root',str(runs_root)],
        root/'job.json',research_id=root.name)


def summary(root: Path):
    config=jobs.read_record(root/'request.json') or (jobs.read_record(root/'research.json') or {}).get('config', {})
    job=jobs.read_record(root/'job.json') or {}
    status=jobs.read_record(root/'status.json') or {}
    alive=jobs.is_running(job)
    state='running' if alive else status.get('state','error' if job.get('exit_code') or not job else 'starting')
    if not alive and state=='running':state='interrupted'
    cursor_ref=jobs.read_record(root/'campaign/current.json') or {}
    cursor=jobs.read_record(root/'campaign/history'/f'{cursor_ref.get("revision","none")}.json') or {}
    rounds=[]
    for receipt in sorted((root/'campaign/rounds').glob('round-*/receipt.json')):
        value=jobs.read_record(receipt)
        if value:rounds.append(value)
    test=jobs.read_record(root/'test-report.json')
    memory=jobs.read_record(root/'memory'/f'{cursor.get("memory_revision","none")}.json') or {}
    current=jobs.read_record(root/'campaign-result.json') or {}
    label=jobs.read_record(root/'label.json') or {}
    proposal_details=[]
    for path in sorted((root/'campaign/rounds').glob('round-*/execution/**/language-proposal/proposal.json')):
        proposal=jobs.read_record(path) or {}
        proposal_details.extend({k:c.get(k) for k in ('id','changes','hypothesis','expected_effect','used_lessons')}
                                for c in proposal.get('candidates',[]))
    evaluations=[]
    for path in sorted((root/'campaign/rounds').glob('round-*/execution/evaluate_*.json')):
        record=jobs.read_record(path) or {}
        output=record.get('output') or {}
        episodes=output.get('episodes') or []
        evaluations.append({'candidate':path.stem.removeprefix('evaluate_'),
                            'successes':sum(bool(e.get('success')) for e in episodes),
                            'episodes':len(episodes), 'state':record.get('state'),
                            'measurements':[e.get('measurements',{}) for e in episodes]})
    trial_receipts=sorted((root/'campaign').glob('**/trials/*/receipt.json'), key=lambda p:p.stat().st_mtime)
    trial_records=[jobs.read_record(path) or {} for path in trial_receipts]
    active=next((r for r in reversed(trial_records) if r.get('state')=='started'),{})
    return {'research_id':root.name,'state':state,'config':config,'alive':alive, 'label':label.get('label'),
            'started_at':job.get('started_at'),'error':status.get('error'),
            'completed_rounds':cursor.get('index',0),'rounds':rounds,
            'harness_revision':cursor.get('harness_revision'),
            'memory_revision':cursor.get('memory_revision'),'lessons':memory.get('lessons',{}),
            'test_report':test,'current':current.get('current'),
            'proposal_details':proposal_details, 'evaluations':evaluations,
            'completed_episodes':sum(r.get('state')=='completed' for r in trial_records),
            'phase':active.get('execution_phase') or ('system2' if alive else state),
            'log':jobs.log_tail(root/'job.json',35),
            'proposals':len(list((root/'campaign/rounds').glob('round-*/execution/**/language-proposal/proposal.json')))}


def list_campaigns(runs_root: Path):
    base=runs_root/'.studio'/'research'
    hidden=hidden_research_ids(runs_root)
    return sorted([summary(p) for p in base.glob('research-*') if p.is_dir() and not p.is_symlink()
                   and p.name not in hidden
                   and ((p/'request.json').is_file() or (p/'research.json').is_file())],
                  key=lambda r:r.get('started_at') or 0,reverse=True)


def stop(runs_root: Path,ref: str):
    root=directory(runs_root,ref);record=jobs.read_record(root/'job.json') or {}
    jobs.interrupt(record)
    return {'stopping':ref}


def resume(runs_root: Path,ref: str):
    root=directory(runs_root,ref);record=jobs.read_record(root/'job.json') or {}
    if jobs.is_running(record):raise ValueError('Campaign is already running')
    if (jobs.read_record(root/'status.json') or {}).get('state')=='completed':
        raise ValueError('Campaign is complete; start a new campaign for a different budget')
    config=ResearchConfig.model_validate_json((root/'request.json').read_text());validate(config)
    spawn(root,runs_root)
    return {'resuming':ref}
