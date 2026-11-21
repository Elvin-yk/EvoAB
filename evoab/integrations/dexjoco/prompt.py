"""Measured robot context for DexJoCo; tools own their operation schemas."""
from evoab.context.prompt import environment_for_model, state_json_for_model, Prompt, register_prompt_builder


@register_prompt_builder("dexjoco")
def build(context):
    episode = context.episode
    prompt = Prompt(user=context.task or (episode.instruction if episode else ""), system=(
        "Control the described Panda arm and Allegro hand through the registered tools. "
        "Use actor_act for bounded local phases and inspect fresh RGB observations to verify effects. "
        "TCP poses are measured in the world frame, in metres and wxyz quaternion order. "
        "Finger proprioception is expressed in radians for the named joints. "
        "A requested pose, finger target, completed command or reviewer decision does not establish a grasp "
        "or task success. A preview shows requested targets, not predicted physical or object motion. "
        "The official native task evaluator determines the outcome after finish. "
        "Do not inspect simulator state, task predicates, privileged object poses or service implementation."
    ))
    if episode:
        prompt = prompt.append(system="Environment description:\n" + environment_for_model(episode.description.environment),
                               user="Initial measured state:\n" + state_json_for_model(episode.state))
    return prompt
