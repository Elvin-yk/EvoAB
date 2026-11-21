"""Supply task identity and measured environment context."""
from evoab.context.prompt import environment_for_model, state_json_for_model, Prompt, register_prompt_builder


@register_prompt_builder("behavior")
def build(context):
    episode = context.episode
    result = Prompt(user=context.task or (episode.instruction if episode else ""), system=(
        "Control the described mobile dual-arm robot using the registered tools. "
        "Inspect fresh RGB and robot measurements to judge action effects. "
        "Do not inspect simulator source, task predicates, hidden object poses or evaluator state. "
        "Only the native task evaluator determines the outcome after finish."
    ))
    if episode:
        result = result.append(system="Environment description:\n" + environment_for_model(episode.description.environment),
                               user="Initial measured state:\n" + state_json_for_model(episode.state))
    return result
