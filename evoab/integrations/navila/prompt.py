"""Public navigation context; native goals and metrics are never prompts."""
from pathlib import Path

from evoab.context.prompt import environment_for_model, state_json_for_model, Prompt, register_prompt_builder


@register_prompt_builder("navila")
def build(context):
    episode = context.episode
    text = (Path(__file__).resolve().parents[2] / "resources/prompts/navila/hybrid.md").read_text()
    prompt = Prompt(user=context.task or (episode.instruction if episode else ""), system=text)
    if episode:
        prompt = prompt.append(system="Environment description:\n" + environment_for_model(episode.description.environment),
                               user="Initial measured state:\n" + state_json_for_model(episode.state))
    return prompt
