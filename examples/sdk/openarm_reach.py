"""Exercise the shared wrist, gripper and read-only status tools in Gazebo."""
import argparse
import json
import time
from pathlib import Path

from evoab import DeploymentConfig, Session, SessionConfig, deploy


def call(session, tool, **arguments):
    result = session.tools.call(tool, arguments)
    if not result.success:
        raise RuntimeError(f"{tool}: {result.error}")
    return result.extra


def exercise(session, side):
    start = call(session, "get_status")
    z0 = start["arms"][side]["wrist_pose"]["position"][2]
    assert set(start["arms"]) == {side} and start["controller"]["ready"]
    assert start["arms"][side]["gripper_opening"] >= 0.95
    target = [{"arm": side}]
    actions = [
        ("move", {"targets": [{"arm": side, "translation_m": [0, 0, 0.04]}], "profile": "precision"}),
        ("grasp", {"targets": target, "steps": 1}),
        ("hold", {"steps": 1}),
        ("release", {"targets": target, "steps": 1}),
    ]
    for tool, arguments in actions:
        action = call(session, tool, **arguments)
        status = call(session, "get_status")
        arm = status["arms"][side]
        assert status["native_steps"] == action["state"]["native_steps"]
        assert status["freshness"] == "fresh" and status["controller"]["ready"]
        assert abs(arm["wrist_pose"]["position"][2] - z0 - 0.04) <= 0.005
        expected_open = tool in ("move", "release")
        assert abs(arm["gripper_opening"] - int(expected_open)) <= 0.05
        assert arm["gripper_target"] == int(expected_open)
        print(json.dumps({"tool": tool, "steps": status["native_steps"],
                          "lift_m": arm["wrist_pose"]["position"][2] - z0,
                          "gripper_opening": arm["gripper_opening"]}), flush=True)
    time.sleep(5.1)
    stale = call(session, "get_status")
    assert stale["freshness"] == "stale" and stale["controller"]["fault"] is None
    assert (stale["state_version"], stale["native_steps"], stale["captured_at"]) == (
        status["state_version"], status["native_steps"], status["captured_at"])
    call(session, "finish", status="success", summary="Verified measured wrist motion and gripper targets.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("side", choices=("left", "right"))
    parser.add_argument("--output-dir", type=Path, default=Path("runs"))
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    side = args.side
    deployment = DeploymentConfig(integration="openarm_gazebo", task=f"reach_{side}",
                                  output_dir=args.output_dir, port=args.port)
    with deploy(deployment) as environment:
        config = SessionConfig(integration="openarm_gazebo", env_url=environment.url, output_dir=args.output_dir)
        with Session(config) as session:
            exercise(session, side)
        print(session.result.session_dir / "native_outcome.json")
        if not session.result.task_success:
            raise SystemExit("The measured wrist pose missed its target")


if __name__ == "__main__":
    main()
