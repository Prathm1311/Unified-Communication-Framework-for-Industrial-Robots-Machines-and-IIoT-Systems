"""
tests/test_bridge_manager.py
-----------------------------
Unit tests for BridgeManager routing logic, BridgeMessage, and the serializer.

All three adapters are mocked so these tests run without a live ROS2
environment, MQTT broker, or FastAPI server.
"""

from __future__ import annotations

import time
import types
import unittest
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

from robot_bridge.models.message import BridgeMessage, MessageDirection, MessageSource
from robot_bridge.utils.serializer import get_ros2_msg_class, ros2_msg_to_dict


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_manager_with_mocks(mappings: List[Dict[str, Any]]):
    """
    Return a (BridgeManager, ros2_mock, mqtt_mock, rest_mock) tuple.

    The manager's adapters are replaced by MagicMock instances so no real
    connections are made.
    """
    from robot_bridge.bridge_manager import BridgeManager

    config_data = {
        "ros2": {},
        "mqtt": {"broker_host": "localhost", "broker_port": 1883},
        "rest": {"host": "0.0.0.0", "port": 8000},
        "mappings": mappings,
    }

    manager = BridgeManager.__new__(BridgeManager)
    manager._config    = config_data
    manager._mappings  = mappings
    manager._running   = False
    manager._directions = {m["name"]: m["direction"] for m in mappings}

    ros2_mock = MagicMock()
    mqtt_mock = MagicMock()
    rest_mock = MagicMock()

    manager._adapters = {
        "ros2": ros2_mock,
        "mqtt": mqtt_mock,
        "rest": rest_mock,
    }

    return manager, ros2_mock, mqtt_mock, rest_mock


# ---------------------------------------------------------------------------
# Routing tests
# ---------------------------------------------------------------------------

class TestBridgeManagerRouting(unittest.TestCase):
    """Tests for the _route_message() routing logic."""

    def setUp(self) -> None:
        self.mappings: List[Dict[str, Any]] = [
            {
                "name": "robot_pose",
                "ros2_topic": "/robot/pose",
                "ros2_msg_type": "geometry_msgs/msg/PoseStamped",
                "mqtt_topic": "robot/telemetry/pose",
                "rest_endpoint": "/telemetry/pose",
                "direction": "ros2_to_external",
            },
            {
                "name": "cmd_vel",
                "ros2_topic": "/cmd_vel",
                "ros2_msg_type": "geometry_msgs/msg/Twist",
                "mqtt_topic": "robot/command/cmd_vel",
                "rest_endpoint": "/command/cmd_vel",
                "direction": "external_to_ros2",
            },
            {
                "name": "robot_status",
                "ros2_topic": "/robot/status",
                "ros2_msg_type": "std_msgs/msg/String",
                "mqtt_topic": "robot/status",
                "rest_endpoint": "/telemetry/status",
                "direction": "bidirectional",
            },
        ]
        self.manager, self.ros2, self.mqtt, self.rest = _make_manager_with_mocks(
            self.mappings
        )

    # ------------------------------------------------------------------ #

    def test_ros2_to_external_routing(self) -> None:
        """ROS2 source + ros2_to_external → MQTT and REST receive it, ROS2 does not."""
        msg = BridgeMessage(
            topic="robot_pose",
            payload={"pose": {"position": {"x": 1.0}}},
            source=MessageSource.ROS2,
        )
        self.manager._route_message(msg)

        self.mqtt.send.assert_called_once_with(msg)
        self.rest.send.assert_called_once_with(msg)
        self.ros2.send.assert_not_called()

    def test_external_to_ros2_routing_from_mqtt(self) -> None:
        """MQTT source + external_to_ros2 → ROS2 receives it, MQTT and REST do not."""
        msg = BridgeMessage(
            topic="cmd_vel",
            payload={"linear": {"x": 0.5}, "angular": {"z": 0.2}},
            source=MessageSource.MQTT,
        )
        self.manager._route_message(msg)

        self.ros2.send.assert_called_once_with(msg)
        self.mqtt.send.assert_not_called()
        self.rest.send.assert_not_called()

    def test_bidirectional_from_ros2(self) -> None:
        """ROS2 source + bidirectional → MQTT and REST get it, ROS2 does not (no self-echo)."""
        msg = BridgeMessage(
            topic="robot_status",
            payload={"data": "IDLE"},
            source=MessageSource.ROS2,
        )
        self.manager._route_message(msg)

        self.mqtt.send.assert_called_once_with(msg)
        self.rest.send.assert_called_once_with(msg)
        self.ros2.send.assert_not_called()

    def test_unknown_mapping_dropped(self) -> None:
        """Messages with an unknown mapping name are silently dropped."""
        msg = BridgeMessage(
            topic="nonexistent_mapping",
            payload={},
            source=MessageSource.ROS2,
        )
        self.manager._route_message(msg)

        self.ros2.send.assert_not_called()
        self.mqtt.send.assert_not_called()
        self.rest.send.assert_not_called()

    def test_no_self_echo(self) -> None:
        """The REST adapter does not receive its own outbound command."""
        msg = BridgeMessage(
            topic="cmd_vel",
            payload={"linear": {"x": 0.1}},
            source=MessageSource.REST,
        )
        self.manager._route_message(msg)

        self.ros2.send.assert_called_once_with(msg)
        self.rest.send.assert_not_called()
        self.mqtt.send.assert_not_called()

    def test_ros2_source_rejected_for_external_to_ros2(self) -> None:
        """ROS2 source on an external_to_ros2 mapping is dropped."""
        msg = BridgeMessage(
            topic="cmd_vel",
            payload={},
            source=MessageSource.ROS2,
        )
        self.manager._route_message(msg)

        self.ros2.send.assert_not_called()
        self.mqtt.send.assert_not_called()
        self.rest.send.assert_not_called()

    def test_bidirectional_from_mqtt(self) -> None:
        """MQTT source + bidirectional → ROS2 and REST get it, MQTT does not."""
        msg = BridgeMessage(
            topic="robot_status",
            payload={"data": "BUSY"},
            source=MessageSource.MQTT,
        )
        self.manager._route_message(msg)

        self.ros2.send.assert_called_once_with(msg)
        self.rest.send.assert_called_once_with(msg)
        self.mqtt.send.assert_not_called()


# ---------------------------------------------------------------------------
# BridgeMessage tests
# ---------------------------------------------------------------------------

class TestBridgeMessage(unittest.TestCase):
    """Tests for the BridgeMessage dataclass."""

    def test_default_timestamp(self) -> None:
        """A BridgeMessage created without an explicit timestamp uses time.time()."""
        before = time.time()
        msg = BridgeMessage(topic="t", payload={}, source=MessageSource.ROS2)
        after  = time.time()
        self.assertGreaterEqual(msg.timestamp, before)
        self.assertLessEqual(msg.timestamp, after)

    def test_repr(self) -> None:
        """__repr__ should include topic and source."""
        msg = BridgeMessage(topic="robot_pose", payload={"x": 1}, source=MessageSource.MQTT)
        r = repr(msg)
        self.assertIn("robot_pose", r)
        self.assertIn("mqtt", r)

    def test_to_dict(self) -> None:
        """to_dict() should return a plain dict with all fields."""
        msg = BridgeMessage(
            topic="battery_status",
            payload={"voltage": 12.4},
            source=MessageSource.ROS2,
            timestamp=1234567890.0,
        )
        d = msg.to_dict()
        self.assertEqual(d["topic"], "battery_status")
        self.assertEqual(d["source"], "ros2")
        self.assertEqual(d["payload"]["voltage"], 12.4)
        self.assertEqual(d["timestamp"], 1234567890.0)

    def test_default_metadata_is_empty_dict(self) -> None:
        """Default metadata should be a fresh empty dict, not shared."""
        msg1 = BridgeMessage(topic="a", payload={}, source=MessageSource.ROS2)
        msg2 = BridgeMessage(topic="b", payload={}, source=MessageSource.ROS2)
        msg1.metadata["key"] = "value"
        self.assertNotIn("key", msg2.metadata)

    def test_message_source_enum_values(self) -> None:
        """MessageSource values must be the strings used in JSON output."""
        self.assertEqual(MessageSource.ROS2.value, "ros2")
        self.assertEqual(MessageSource.MQTT.value, "mqtt")
        self.assertEqual(MessageSource.REST.value,  "rest")

    def test_message_direction_enum_values(self) -> None:
        """MessageDirection values must match the config YAML strings."""
        self.assertEqual(MessageDirection.ROS2_TO_EXTERNAL.value, "ros2_to_external")
        self.assertEqual(MessageDirection.EXTERNAL_TO_ROS2.value, "external_to_ros2")
        self.assertEqual(MessageDirection.BIDIRECTIONAL.value,    "bidirectional")


# ---------------------------------------------------------------------------
# Serializer tests
# ---------------------------------------------------------------------------

class TestSerializer(unittest.TestCase):
    """Tests for utils/serializer.py — no live ROS2 required."""

    def _make_fake_ros2_msg(self, **fields) -> types.SimpleNamespace:
        """Build a duck-typed object that mimics a ROS2 message."""
        msg = types.SimpleNamespace(**fields)
        # Mimic rclpy __slots__
        msg.__slots__ = list(fields.keys())
        return msg

    def test_ros2_msg_to_dict_primitive(self) -> None:
        """Primitive fields (bool, int, float, str) are preserved as-is."""
        msg = self._make_fake_ros2_msg(x=1.0, y=2.0, active=True, label="robot")
        result = ros2_msg_to_dict(msg)
        self.assertEqual(result["x"], 1.0)
        self.assertEqual(result["y"], 2.0)
        self.assertEqual(result["active"], True)
        self.assertEqual(result["label"], "robot")

    def test_ros2_msg_to_dict_list(self) -> None:
        """List fields are serialised to Python lists."""
        msg = self._make_fake_ros2_msg(covariance=[0.1, 0.2, 0.3])
        result = ros2_msg_to_dict(msg)
        self.assertEqual(result["covariance"], [0.1, 0.2, 0.3])

    def test_ros2_msg_to_dict_nested_object(self) -> None:
        """Nested message-like objects are recursively converted to dicts."""
        position = self._make_fake_ros2_msg(x=1.0, y=2.0, z=0.0)
        msg      = self._make_fake_ros2_msg(position=position)
        result   = ros2_msg_to_dict(msg)
        self.assertIsInstance(result["position"], dict)
        self.assertEqual(result["position"]["x"], 1.0)

    def test_get_ros2_msg_class_bad_format(self) -> None:
        """get_ros2_msg_class raises ValueError for incorrectly formatted strings."""
        from robot_bridge.utils.serializer import get_ros2_msg_class

        with self.assertRaises(ValueError):
            get_ros2_msg_class("geometry_msgs/PoseStamped")   # missing /msg/

        with self.assertRaises(ValueError):
            get_ros2_msg_class("geometry_msgs")                # only one part

    def test_bytes_field_serialised_as_hex(self) -> None:
        """bytes fields are serialised as hex strings rather than lost."""
        msg = self._make_fake_ros2_msg(raw=b"\x00\x01\x02")
        result = ros2_msg_to_dict(msg)
        self.assertEqual(result["raw"], "000102")

    def test_none_field_preserved(self) -> None:
        """None fields are preserved as None (null in JSON)."""
        msg = self._make_fake_ros2_msg(optional_field=None)
        result = ros2_msg_to_dict(msg)
        self.assertIsNone(result["optional_field"])


# ---------------------------------------------------------------------------
# BridgeManager config loading tests
# ---------------------------------------------------------------------------

class TestBridgeManagerConfigLoading(unittest.TestCase):
    """Tests for BridgeManager._load_config() behaviour."""

    def test_missing_config_raises(self) -> None:
        """FileNotFoundError is raised if the config file does not exist."""
        from robot_bridge.bridge_manager import BridgeManager

        manager = BridgeManager(config_path="/nonexistent/path/config.yaml")
        with self.assertRaises(FileNotFoundError):
            manager._load_config()

    def test_invalid_yaml_raises(self) -> None:
        """ValueError is raised if the YAML file does not contain a dict."""
        import tempfile
        import os
        from robot_bridge.bridge_manager import BridgeManager

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as tmp:
            tmp.write("- just a list\n- not a mapping\n")
            tmp_path = tmp.name

        try:
            manager = BridgeManager(config_path=tmp_path)
            with self.assertRaises(ValueError):
                manager._load_config()
        finally:
            os.unlink(tmp_path)

    def test_mapping_missing_key_raises(self) -> None:
        """ValueError is raised if a mapping is missing a required key."""
        import tempfile
        import os
        from robot_bridge.bridge_manager import BridgeManager

        bad_yaml = """
mqtt:
  broker_host: localhost
  broker_port: 1883
rest:
  host: "0.0.0.0"
  port: 8000
mappings:
  - name: robot_pose
    ros2_topic: /robot/pose
    # Missing ros2_msg_type, mqtt_topic, rest_endpoint, direction
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as tmp:
            tmp.write(bad_yaml)
            tmp_path = tmp.name

        try:
            manager = BridgeManager(config_path=tmp_path)
            with self.assertRaises(ValueError):
                manager._load_config()
        finally:
            os.unlink(tmp_path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
