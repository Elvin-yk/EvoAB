"""Start a previously prepared environment container and own its cleanup."""
import argparse
from evoab import DeploymentConfig, Session, SessionConfig, deploy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--benchmark', required=True)
    parser.add_argument('--task', required=True)
    parser.add_argument('--gpu', type=int, default=0)
    args = parser.parse_args()
    with deploy(DeploymentConfig(benchmark=args.benchmark, task=args.task,
                                 deploy_gpu=args.gpu)) as environment:
        with Session(SessionConfig(benchmark=args.benchmark, env_url=environment.url)) as session:
            print(session.tools.call('view_env_state').to_dict())
        print(session.result.record)


if __name__ == '__main__':
    main()
