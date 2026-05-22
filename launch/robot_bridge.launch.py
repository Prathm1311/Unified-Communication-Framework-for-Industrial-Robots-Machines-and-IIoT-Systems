"""
launch/robot_bridge.launch.py
------------------------------
ROS2 launch file for RobotBridge.

Usage
-----
# Default launch:
ros2 launch robot_bridge robot_bridge.launch.py

# Custom config file:
ros2 launch robot_bridge robot_bridge.launch.py config:=/path/to/my_config.yaml

# Debug logging:
ros2 launch robot_bridge robot_bridge.launch.py log_level:=DEBUG
"""

from __future__ import annotations

import os

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, LogInfo
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description() -> LaunchDescription:
    # ------------------------------------------------------------------ #
    # Launch arguments                                                     #
    # ------------------------------------------------------------------ #
    config_arg = DeclareLaunchArgument(
        "config",
        default_value=os.path.join(
            os.path.dirname(__file__),
            "..",
            "config",
            "bridge_config.yaml",
        ),
        description="Path to the bridge_config.yaml file.",
    )

    log_level_arg = DeclareLaunchArgument(
        "log_level",
        default_value="INFO",
        description="Logging verbosity: DEBUG | INFO | WARNING | ERROR | CRITICAL",
    )

    config_path = LaunchConfiguration("config")
    log_level   = LaunchConfiguration("log_level")

    # ------------------------------------------------------------------ #
    # RobotBridge node                                                     #
    # ------------------------------------------------------------------ #
    bridge_node = Node(
        package="robot_bridge",
        executable="robot_bridge",
        name="robot_bridge",
        output="screen",
        emulate_tty=True,
        arguments=[
            "--config", config_path,
            "--log-level", log_level,
        ],
        parameters=[],
    )

    return LaunchDescription([
        config_arg,
        log_level_arg,
        LogInfo(msg=["Launching RobotBridge with config: ", config_path]),
        bridge_node,
    ])
