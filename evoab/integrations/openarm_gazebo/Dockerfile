ARG ROS_BASE=ros:humble-ros-base-jammy
FROM ${ROS_BASE}
SHELL ["/bin/bash", "-eo", "pipefail", "-c"]
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-pip python3-colcon-common-extensions ros-humble-ament-cmake-python \
    ros-humble-gazebo-dev ros-humble-gazebo-ros-pkgs ros-humble-gazebo-ros2-control \
    ros-humble-joint-state-broadcaster ros-humble-joint-trajectory-controller \
    ros-humble-robot-state-publisher ros-humble-xacro xvfb xauth libgl1-mesa-dri \
    && rm -rf /var/lib/apt/lists/*
# Humble's native Python is 3.10. Keep the EvoAB service in its own interpreter.
ENV UV_PYTHON_INSTALL_DIR=/opt/openarm/python
RUN python3 -m pip install --no-cache-dir uv==0.9.18 && \
    uv python install 3.11.14 && "$(uv python find 3.11.14)" -m venv /opt/openarm/service
WORKDIR /opt/openarm
COPY openarm_description ws/src/openarm_description
COPY rosbot_ros/rosbot_description ws/src/rosbot_description
COPY RuboClaw/robot_runtime/simulation/openarm2_minimal_gazebo ws/src/openarm2_minimal_gazebo
RUN source /opt/ros/humble/setup.bash && cd ws && colcon build --merge-install \
    --packages-select openarm_description rosbot_description openarm2_minimal_gazebo --cmake-args -DBUILD_TESTING=OFF
COPY RuboClaw/robot_runtime/__init__.py ruboclaw/robot_runtime/
COPY RuboClaw/robot_runtime/openarm_ik ruboclaw/robot_runtime/openarm_ik
COPY RuboClaw/robot_runtime/openarm_control ruboclaw/robot_runtime/openarm_control
