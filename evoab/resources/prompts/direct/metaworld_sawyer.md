## Embodiment and control frame

- Robot: Rethink Robotics Sawyer single-arm tabletop manipulator in MuJoCo (MetaWorld MT50).
- Control site: parallel-jaw gripper TCP in world metres (`+x` right across table, `+y` away from robot base, `+z` up).
- Degrees of freedom: 4-DoF XYZ translation plus scalar gripper opening (`0` closed, `1` open). Wrist orientation is fixed by the benchmark; keep the measured quaternion unchanged.
- Cameras: `corner2` (fixed tabletop view) and `behindGripper` (rear-gripper view).
