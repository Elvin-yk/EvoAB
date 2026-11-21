"""Measured whole-body context; tools own their schemas."""
from evoab.context.prompt import environment_for_model, state_json_for_model, Prompt, register_prompt_builder


@register_prompt_builder("isaaclab_arena")
def build(context):
    episode = context.episode
    prompt = Prompt(user=context.task or (episode.instruction if episode else ""), system=(
        "Control the described humanoid through actor_act and inspect fresh camera observations. "
        "The robot coordinates arms, hands, walking and pelvis height. Native whole-body control owns "
        "balance, legs and waist; they are not independent direct controls. "
        "Previews describe requested controller references, not predicted trajectories or contact. "
        "A completed action does not establish task success. The native evaluator owns the outcome. "
        "Do not inspect simulator implementation, privileged object poses or task predicates."
    ))
    if episode:
        prompt = prompt.append(system="Environment description:\n" + environment_for_model(episode.description.environment),
                               user="Initial measured state:\n" + state_json_for_model(episode.state))
    return prompt
