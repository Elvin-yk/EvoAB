"""Typed PhysicalRSI skills over EvoAB sessions and official evaluation.

Only the environment adapter executes robot tools. The System 1 skill receives
public RGB/state feedback; the separate verifier reads the official outcome.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
import time
from uuid import uuid4

from physicalrsi import CallableSkill, SkillDescriptor, compose_sequence
from physicalrsi.core import Contract
from PhysicalRSI_core.contracts import InfrastructureFault
from PhysicalRSI_core.embodiment import Embodiment, System1
from PhysicalRSI_core.experiments import TrialAdapters
from PhysicalRSI_core.infra.storage import digest, file_digest, read_json, atomic_json
from PhysicalRSI_core.self_harness.artifacts import verify_harness

from evoab import Session, SessionConfig, DeploymentConfig, deploy
from evoab.provider.catalog import ModelClient
from evoab.workbench.launcher import free_port

OBS = Contract('evoab-public-scene/v1')
ACT = Contract('evoab-registered-tool-call/v1')
ACTIVE_TRIALS = []


class RobotEpisode:
    def __init__(self, config, root: Path, runs_root: Path, controls: dict):
        self.config, self.root, self.runs_root, self.controls = config, root, runs_root, controls
        self.session = self.deployment = self.result = None
        self.usage = {'input_tokens':0, 'output_tokens':0, 'total_tokens':0}
        self.tools = []
        self.steps = 0
        self.ended = False
        self.started = time.monotonic()

    def identity(self):
        return {'kind':'evoab-tool-episode/v1', 'integration':self.config.integration,
                'task':self.config.task, 'implementation':file_digest(Path(__file__))}

    def describe(self):
        return Embodiment('evoab-simulator', digest(self.identity()), 'simulation', OBS, ACT)

    def _observation(self, output, context):
        images = [{'name': im.name, 'blob':context.blobs.put_bytes(im.data, media_type=im.mime_type)}
                  for im in output.images]
        return {'instruction':self.deployment.info.get('metadata', {}).get('instruction', self.deployment.info.get('task', self.config.task)), 'feedback':output.to_dict(for_model=True),
                'images':images, 'tools':self.tools}

    def reset(self, case, context):
        context.check()
        print(f'Trial reset: task={self.config.task} seed={case["seed"]}',flush=True)
        ACTIVE_TRIALS.append(self)
        try:
            self.started = time.monotonic()
            from evoab.workbench.benchmarks import launch_target
            profile, native_task = launch_target(self.config.integration, 'default', self.config.task)
            self.deployment = deploy(DeploymentConfig(integration=self.config.integration,
                task=native_task, runtime_profile=profile, seed=case['seed'], port=free_port(),
                output_dir=self.root/'services', startup_timeout_s=self.config.episode_seconds))
            self.deployment.__enter__()
            self.session = Session(SessionConfig(integration=self.config.integration,
                env_url=self.deployment.url, no_sam3=True, tool_profile='clean',
                output_dir=self.runs_root, tool_return_mode='immediate',
                episode_timeout_s=self.config.episode_seconds))
            self.session.start()
            atomic_json(self.session.session_dir/'research_trial.json',
                        {'research_id':self.root.name, 'case':case, 'controls':self.controls,
                         'physicalrsi_episode':context.episode, 'model':self.config.model})
            self.tools = self.session.tools.list()
            output = self.session.tools.call('capture_scene', {})
            if not output.success: raise RuntimeError(output.error)
            return {'ready':True, 'observation':self._observation(output,context)}
        except BaseException:
            self.close()
            raise

    def step(self, action, context):
        context.check()
        name, args = action['name'], action['arguments']
        if name not in {t['name'] for t in self.tools}: raise ValueError('Tool is not registered')
        args = dict(args)
        output = self.session.tools.call(name,args,timeout=max(1,self.config.episode_seconds))
        self.steps += 1
        if output.action and output.action.executed == 'unknown':
            raise InfrastructureFault('Uncertain robot execution; inspect original receipts')
        state = output.extra.get('state') or output.obs or {}
        self.ended = name == 'finish' or bool(state.get('ended'))
        return {'observation':self._observation(output,context),'terminated':self.ended}

    def close(self):
        try:
            if self.session and self.result is None:
                self.result = self.session.finish()
        finally:
            if self.deployment:
                self.deployment.close()
                self.deployment = None


class RobotSkill:
    def __init__(self, config, candidate, episode):
        self.config, self.episode = config, episode
        self.revision = verify_harness(candidate)
        self.history = []
        self.client = None
        self.controls = read_json(Path(candidate['root'])/'control.json')
        prepare = CallableSkill(SkillDescriptor('public-scene-context','1',
            {'name':OBS.name},{'name':'evoab-model-context/v1'}),self.prepare,
            executable_identity={'implementation':file_digest(Path(__file__)), 'harness':self.revision})
        decide = CallableSkill(SkillDescriptor('vision-tool-decision','1',
            {'name':'evoab-model-context/v1'},{'name':ACT.name}),self.decide,
            executable_identity={'implementation':file_digest(Path(__file__)), 'harness':self.revision})
        self.skill = compose_sequence('physicalrsi-evoab-system1',prepare,decide)

    def identity(self):
        return {'kind':'physicalrsi-evoab-system1/v1', 'revision':self.revision,
                'model':self.config.model, 'skills':self.skill.registry_record(),
                'implementation':file_digest(Path(__file__))}

    def describe(self):
        return System1('physicalrsi-evoab-system1',self.revision,OBS,ACT)

    def begin_episode(self,task,case,observation,context):
        context.check()
        self.client = ModelClient(self.config.model,reasoning_effort=self.config.reasoning_effort,max_tokens=2048)

    def prepare(self,observation,context):
        context.check()
        body = {k:v for k,v in observation.items() if k not in ('images','tools')}
        body['control'] = self.controls
        content=[{'type':'text','text':json.dumps(body,ensure_ascii=False)}]
        for item in observation['images']:
            ref=item['blob'];data=context.blobs.get_bytes(ref)
            content.append({'type':'image_url','image_url':{'url':
                'data:'+ref['media_type']+';base64,'+base64.b64encode(data).decode()}})
        return {'content':content,'tools':observation['tools']}

    def decide(self,value,context):
        context.check()
        system = ('Control the robot using only its registered tools and public observations. '
                  'Return exactly one tool call. Verify effects from RGB and measured state. '
                  'Tool completion does not prove task success. Coordinates use world metres and XYZ radians. '
                  f'Approach clearance: {self.controls["clearance_m"]} m. '
                  f'Maximum movement control steps per tool: {int(self.controls["motion_steps"])}. '
                  'Use move with gripper opening to grasp/release; finish when visual evidence supports completion.')
        self.history.append({'role':'user','content':value['content']})
        # Preserve complete recent tool feedback and images without any verifier state.
        history=self.history[-2*int(self.controls['history_turns']):]
        tools=[{'type':'function','function':{'name':t['name'],'description':t.get('description',''),
                'parameters':t.get('input_schema',t.get('parameters',{}))}} for t in value['tools']]
        reply=self.client.complete([{'role':'system','content':system},*history],tools=tools,tool_choice='required')
        for key in self.episode.usage:
            self.episode.usage[key] += reply.usage.get(key,0) or 0
        if reply.error: raise InfrastructureFault('System 1 model request failed: '+reply.error)
        if len(reply.tool_calls)!=1: raise ValueError('System 1 requires exactly one registered tool call')
        call=reply.tool_calls[0]
        args=call['arguments'];args=json.loads(args) if isinstance(args,str) else args
        # Keep a compact conversational record; the next turn includes measured feedback.
        self.history.append({'role':'assistant','content':json.dumps({'tool':call['name'],'arguments':args})})
        # Candidate parameters belong to System 1, never the fixed environment protocol.
        if call['name'] == 'move': args['max_steps'] = min(args.get('max_steps',12),int(self.controls['motion_steps']))
        if call['name'] == 'hold': args['steps'] = min(args.get('steps',1),int(self.controls['motion_steps']))
        return {'name':call['name'],'arguments':args}

    def act(self,observation,context):
        return self.skill.execute(observation,context)

    def end_episode(self,context):
        try:
            if self.client:self.client.close()
        finally:self.episode.close()
        return {'stopped':True}


class OfficialVerifier:
    def __init__(self,episode):self.episode=episode
    def identity(self):return {'kind':'evoab-official-verdict/v1','implementation':file_digest(Path(__file__))}
    def verify(self,case,trace,context=None):
        result=self.episode.result
        if result is None:raise RuntimeError('Episode did not finalize')
        if not result.accepted or result.task_success is None:
            raise InfrastructureFault('Environment has no accepted official outcome')
        print(f'Trial finished: task_success={result.task_success} tool_calls={self.episode.steps}',flush=True)
        return {'outcome':'success' if result.task_success else 'failure',
                'reason':'official_environment_verdict',
                'measurements':{'native_steps':read_json(result.session_dir/'native_outcome.json').get('native_steps',0),
                                'tool_calls':self.episode.steps,
                                **self.episode.usage,
                                'wall_time_s':time.monotonic()-self.episode.started}}


class RobotSuite:
    def __init__(self,config,root,runs_root):self.config,self.root,self.runs_root=config,root,runs_root
    def identity(self):return {'kind':'evoab-simulator-suite/v1','config':self.config.model_dump(),
                              'implementation':file_digest(Path(__file__))}
    def admit(self,candidate):
        verify_harness(candidate)
        control=read_json(Path(candidate['root'])/'control.json')
        if not 1<=control['motion_steps']<=12 or not .01<=control['clearance_m']<=.15 or not 1<=control['history_turns']<=8:
            return {'accepted':False,'reason':'controller_bounds'}
        return {'accepted':True,'evidence':{'harness_sha256':verify_harness(candidate)}}
    def trial(self,candidate,task):
        episode=RobotEpisode(self.config,self.root,self.runs_root,read_json(Path(candidate['root'])/'control.json'))
        return TrialAdapters(episode,RobotSkill(self.config,candidate,episode),OfficialVerifier(episode))
