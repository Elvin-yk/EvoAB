"""Serve a pinned LIBERO-PRO task without generating or rewriting its assets."""
from evoab.envs.libero.family import BUDGETS, parser as family_parser, prepare as prepare_family
from evoab.envs.simulation import serve_episode

REVISION = "eafdb809426b13153aa1e4c42d6601844217dfec"
SUITES = tuple(base + suffix for base in BUDGETS for suffix in ("", "_env", "_object", "_swap", "_task", "_lan"))


def parser():
    return family_parser(suites=SUITES, description=__doc__, port=18782)


def prepare(args):
    from .adapter import LiberoProPolicyAdapter

    return prepare_family(args, benchmark_name="libero_pro", revision=REVISION,
                          adapter_factory=LiberoProPolicyAdapter)


def main():
    args = parser().parse_args()
    serve_episode(lambda: prepare(args), args, "libero_pro",
                  capabilities=("pose", "native", "published_observations"))


if __name__ == "__main__":
    main()
