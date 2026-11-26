"""Run one built-in planner; provider credentials use the standard environment variables."""
import argparse
from evoab import AgentConfig, SessionConfig, run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--integration', required=True)
    parser.add_argument('--env-url', required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--recipe', default='agentic', help='Method id (evoab methods list) or recipe file')
    parser.add_argument('--agent', default='codex', help="Entry role's agent runtime")
    parser.add_argument('--set', action='append', default=[], metavar='NAME=VALUE')
    args = parser.parse_args()
    overrides = dict(item.split('=', 1) for item in args.set)
    result = run(SessionConfig(integration=args.integration, env_url=args.env_url,
                              recipe=args.recipe, recipe_overrides=overrides,
                              agent=AgentConfig(model=args.model, agent_backend=args.agent)))
    print(result.task_success, result.session_dir)


if __name__ == '__main__':
    main()
