"""Drive one upstream Q0-Preview policy from a prepared simulation episode."""

from contextlib import closing
import json
from pathlib import Path
import sys
import time

from evoab.artifacts.run import write_json_atomic, write_result_json
from evoab.integrations.robodojo.actor_profiles import X5_CAMERAS
from evoab.integrations.robodojo.x5_observation import x5_joint_command, x5_observation
from evoab.services.deployment.process import cancellation_signals
from evoab.services.environment.client import EnvironmentClient
from evoab.services.environment.contracts import ActionReply, CaptureRequest, FinishRequest, StepRequest

from . import REVISION
from .runtime import policy_session


def verify_episode(info, config):
    if (info.integration != "robodojo" or info.lifecycle != "active" or info.task != config["task"]
            or (config["run_id"] and info.episode_id != config["run_id"])
            or (config["identity"] and info.episode_id != config["identity"]["episode_id"])):
        raise ValueError("Expected an active RoboDojo simulation episode matching the requested task and run.")
    desc = info.environment
    if (desc.control_frequency_hz != 25 or [arm.name for arm in desc.arms] != ["left", "right"]
            or "seed" not in info.metadata
            or any(arm.control_frame != "x5_grasp_site" or arm.joint_names != [f"joint{i}" for i in range(1, 7)]
                   for arm in desc.arms)
            or not set(X5_CAMERAS) <= {camera.name for camera in desc.cameras if camera.has_calibration}):
        raise ValueError("Q0-Preview requires the 25 Hz dual X5 and three calibrated cameras.")


def perform(env, operation, request):
    """Resolve a lost response using its existing receipt, with no action replay."""
    try:
        return operation(request)
    except Exception:
        if env.uncertain:
            receipt = env.reconcile()
            if receipt and receipt.get("status") == "completed" and not env.uncertain:
                result = receipt["result"]
                return ActionReply.model_validate(result) if isinstance(request, StepRequest) else result
        raise


def run_episode(config, env, output, record, policy_factory):
    info = env.connect()
    verify_episode(info, config)
    capture = CaptureRequest(cameras=list(X5_CAMERAS), high_resolution=True)
    snapshot = perform(env, env.capture, capture)
    if snapshot["state"]["ended"] or snapshot["state"]["native_steps"] != 0:
        raise ValueError("Q0-Preview requires an unused active episode.")
    record.update(episode_identity=info.model_dump(mode="json"), seed=info.metadata["seed"],
                  instruction=snapshot["state"]["metadata"]["instruction"])
    expected = 0
    with (output / "trajectory.jsonl").open("x") as trace, policy_factory(config, info, output) as policy:
        while True:
            state = snapshot["state"]
            if (snapshot["episode_id"] != info.episode_id or state["native_steps"] != expected
                    or state["ended"] or snapshot["state_version"] != state["state_version"]):
                raise RuntimeError("Expected a fresh observation from the same active episode and control step.")
            observation = x5_observation(snapshot, env.artifact_bytes)
            policy.call("update_obs", obs=observation)
            command = x5_joint_command(policy.call("get_action"))
            request = StepRequest(command=command, expected_state_version=snapshot["state_version"])
            reply = perform(env, env.step, request)
            expected += 1
            trace.write(json.dumps(dict(step=expected, snapshot_id=snapshot["snapshot_id"],
                                        command=command.model_dump(), receipt=reply.identity.model_dump(),
                                        execution_status=reply.execution_status)) + "\n")
            trace.flush()
            if (reply.execution_status != "completed" or reply.feedback.get("accepted") is not True
                    or reply.feedback.get("requires_operator_recovery") or reply.feedback.get("execution_outcome") in {"partial", "unknown"}
                    or reply.state_before.native_steps != expected - 1 or reply.state_after.native_steps != expected
                    or reply.artifact_error):
                raise RuntimeError(f"Incomplete X5 step or artifact at receipt {reply.identity.request_id}")
            record["steps"] = expected
            if reply.state_after.ended:
                break
            snapshot = perform(env, env.capture, capture)

    final = perform(env, env.finish, FinishRequest(reason="native_terminal"))
    outcome = final["outcome"]
    write_json_atomic(output / "native_outcome.json", outcome)
    if (final.get("artifact_error") or outcome.get("complete") is not True
            or not isinstance(outcome.get("task_success"), bool) or outcome.get("steps") != expected):
        raise RuntimeError("The environment did not publish a complete native outcome and archive.")
    record.update(task_success=outcome["task_success"], score=outcome.get("native_score"),
                  accepted=True, stop_reason=outcome.get("stop_reason"),
                  failure_category=None if outcome["task_success"] else "task_unsuccessful")


def evaluate(config, *, env_factory=EnvironmentClient, policy_factory=policy_session):
    output = Path(config["output"])
    recipe = config["recipe"]
    write_json_atomic(output / "recipe.json", {"id": recipe["id"], "recipe": recipe})
    started = time.monotonic()
    record = dict(agent_backend="q0_preview", integration="robodojo", task=config["task"], model=config["model"],
                  task_id=config["task"], channel="tools", roles=[recipe["loop"]["entry"]], reasoning_effort="xhigh",
                  model_preset=config["preset"], recipe=recipe["id"], upstream_revision=REVISION,
                  protocol="upstream_reference", task_success=None, agent_finished=False, accepted=False,
                  failure_category="evaluator_error", retryable=False, steps=0)
    code = 1
    with closing(env_factory(config["env_url"])) as env:
        try:
            run_episode(config, env, output, record, policy_factory)
            code = 0 if record["task_success"] else 1
        except (Exception, KeyboardInterrupt) as error:
            record["error"] = f"{type(error).__name__}: {error}"
            if "episode_identity" in record and not env.uncertain:
                try:
                    # Preserve the native outcome on a policy-side failure, even when
                    # the environment had already terminated before upstream cleanup.
                    final = env.outcome() if (env.connect().lifecycle == "finished") else perform(
                        env, env.finish, FinishRequest(reason="evaluator_error"))
                    write_json_atomic(output / "native_outcome.json", final["outcome"])
                except Exception as cleanup_error:
                    record["cleanup_error"] = str(cleanup_error)
            if env.uncertain:
                code = 75
                record["retryable"] = True
                record["failure_category"] = "execution_uncertain"
                record["pending_receipt"] = env.pending_request_id
            if isinstance(error, KeyboardInterrupt):
                code = 130
        finally:
            record["elapsed_s"] = round(time.monotonic() - started, 3)
            write_result_json(output, record)
    return code


if __name__ == "__main__":
    with cancellation_signals():
        sys.exit(evaluate(json.load(sys.stdin)))
