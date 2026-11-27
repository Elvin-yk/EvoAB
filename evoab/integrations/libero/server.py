"""Serve a task and initial state from the original LIBERO benchmark."""
from evoab.integrations.libero_plus.server import BUDGETS, parser, prepare
from evoab.integrations.simulation import serve_episode

REVISION = "8f1084e3132a39270c3a13ebe37270a43ece2a01"
SUITE_BUDGETS = {**BUDGETS, "libero_90": 400}
PORT = 18801


def main():
    args = parser(suites=tuple(SUITE_BUDGETS), description=__doc__, port=PORT).parse_args()
    from .adapter import LiberoPolicyAdapter

    serve_episode(lambda: prepare(args, benchmark_name="libero", revision=REVISION, budgets=SUITE_BUDGETS,
                                  adapter_factory=LiberoPolicyAdapter),
                  args, "libero", capabilities=("pose", "native", "published_observations"))


if __name__ == "__main__":
    main()
