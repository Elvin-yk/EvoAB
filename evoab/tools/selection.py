"""Named tool groups shared by recipes and launchers."""

TOOL_GROUPS = {
    "@skills": ("list_skills", "read_skill"),
    "@scene": ("view_env_state", "capture_scene", "locate"),
    "@motion": ("move", "grasp", "release", "hold"),
    "@observation": ("capture_observation", "get_latest_observation", "get_observation",
                     "list_observations", "publish_insight", "get_latest_insight", "read_insight_updates"),
    "@jobs": ("get_tool_result", "wait_for_tool", "wait_for_tools", "list_tool_jobs",
              "cancel_tool", "queue_status", "flush_queue"),
}
