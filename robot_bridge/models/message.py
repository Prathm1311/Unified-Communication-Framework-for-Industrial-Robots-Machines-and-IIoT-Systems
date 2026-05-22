"""
models/message.py
-----------------
Defines the canonical internal message type that crosses all adapter boundaries
inside RobotBridge, plus the enums for message source and direction.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict


class MessageSource(str, Enum):
    """Which protocol adapter produced this message."""
    ROS2 = "ros2"
    MQTT = "mqtt"
    REST  = "rest"


class MessageDirection(str, Enum):
    """
    Direction rule declared for each topic mapping in bridge_config.yaml.

    ros2_to_external  – robot publishes → MQTT & REST cache receive it
    external_to_ros2  – MQTT / REST POST → robot receives it
    bidirectional     – both directions are active simultaneously
    """
    ROS2_TO_EXTERNAL = "ros2_to_external"
    EXTERNAL_TO_ROS2 = "external_to_ros2"
    BIDIRECTIONAL    = "bidirectional"


@dataclass
class BridgeMessage:
    """
    The single internal data type that travels between adapters.

    Every adapter converts its native format *into* a BridgeMessage before
    handing it to the BridgeManager, and converts a BridgeMessage *back* to
    its native format when the BridgeManager delivers one to it.

    Attributes
    ----------
    topic:     The logical mapping name from bridge_config.yaml (e.g. "robot_pose").
    payload:   The actual data as a JSON-serialisable dict.
    source:    Which adapter created this message.
    timestamp: Unix time (seconds) when the message was created.
    metadata:  Optional adapter-specific extras (e.g. MQTT QoS, ROS2 frame_id).
    """

    topic:     str
    payload:   Dict[str, Any]
    source:    MessageSource
    timestamp: float            = field(default_factory=time.time)
    metadata:  Dict[str, Any]   = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"BridgeMessage("
            f"topic={self.topic!r}, "
            f"source={self.source.value}, "
            f"ts={self.timestamp:.3f}, "
            f"payload_keys={list(self.payload.keys())})"
        )

    def to_dict(self) -> Dict[str, Any]:
        """Return a plain dict suitable for JSON serialisation."""
        return {
            "topic":     self.topic,
            "payload":   self.payload,
            "source":    self.source.value,
            "timestamp": self.timestamp,
            "metadata":  self.metadata,
        }
