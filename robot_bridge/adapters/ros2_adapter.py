"""
adapters/ros2_adapter.py
------------------------
Handles ROS2 subscriptions and publishers via rclpy.

The adapter:
  • Spins a dedicated rclpy node in a background thread so the event loop
    doesn't block the rest of RobotBridge.
  • Builds subscriptions for every mapping whose direction is ros2_to_external
    or bidirectional.
  • Builds publishers for every mapping whose direction is external_to_ros2 or
    bidirectional.
  • Converts ROS2 messages to/from plain dicts using utils.serializer so that
    the BridgeManager never has to import rclpy.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, List

from robot_bridge.adapters.base_adapter import BaseAdapter
from robot_bridge.models.message import BridgeMessage, MessageDirection, MessageSource
from robot_bridge.utils.serializer import dict_to_ros2_msg, get_ros2_msg_class, ros2_msg_to_dict

logger = logging.getLogger(__name__)

# Lazy import guard — rclpy is only available inside a sourced ROS2 environment.
try:
    import rclpy
    from rclpy.node import Node
    from rclpy.executors import SingleThreadedExecutor
    _ROS2_AVAILABLE = True
except ImportError:
    _ROS2_AVAILABLE = False
    logger.warning(
        "[ROS2Adapter] rclpy is not available. "
        "Source your ROS2 workspace before running RobotBridge."
    )


class ROS2Adapter(BaseAdapter):
    """
    Protocol adapter for ROS2 (rclpy).

    Parameters
    ----------
    config:
        The ``ros2`` section of bridge_config.yaml.
    mappings:
        Full list of topic mapping dicts.
    """

    def __init__(self, config: Dict[str, Any], mappings: List[Dict[str, Any]]) -> None:
        super().__init__(config, mappings)
        self._node:         Any = None   # rclpy.Node
        self._executor:     Any = None   # SingleThreadedExecutor
        self._spin_thread:  threading.Thread | None = None
        self._publishers:   Dict[str, Any] = {}    # mapping_name → Publisher
        self._subscribers:  Dict[str, Any] = {}    # mapping_name → Subscription
        self._running:      bool = False

    # ------------------------------------------------------------------
    # BaseAdapter interface
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Initialise rclpy, create the node, build pub/sub, start spin thread."""
        if not _ROS2_AVAILABLE:
            logger.error("[ROS2Adapter] Cannot start — rclpy not available.")
            return

        if not rclpy.ok():
            rclpy.init()

        node_name = self._config.get("node_name", "robot_bridge_node")
        self._node = rclpy.create_node(node_name)
        self._executor = SingleThreadedExecutor()
        self._executor.add_node(self._node)

        self._build_subscriptions()
        self._build_publishers()

        self._running = True
        self._spin_thread = threading.Thread(
            target=self._spin_loop,
            name="ros2_spin",
            daemon=True,
        )
        self._spin_thread.start()
        logger.info("[ROS2Adapter] Started node '%s'", node_name)

    def stop(self) -> None:
        """Shut down the ROS2 node and spin thread."""
        self._running = False

        if self._executor:
            self._executor.shutdown()

        if self._spin_thread and self._spin_thread.is_alive():
            self._spin_thread.join(timeout=5.0)

        if self._node:
            self._node.destroy_node()

        if _ROS2_AVAILABLE and rclpy.ok():
            rclpy.shutdown()

        logger.info("[ROS2Adapter] Stopped.")

    def send(self, message: BridgeMessage) -> None:
        """
        Publish *message* on the appropriate ROS2 topic.

        The mapping is looked up by ``message.topic``; if no publisher exists
        (because the direction doesn't allow it), the message is silently
        dropped.
        """
        publisher = self._publishers.get(message.topic)
        if publisher is None:
            logger.debug(
                "[ROS2Adapter] No publisher for mapping '%s' — dropping.", message.topic
            )
            return

        mapping = self.get_mapping_by_name(message.topic)
        if mapping is None:
            logger.warning("[ROS2Adapter] No mapping found for topic '%s'.", message.topic)
            return

        try:
            msg_class = get_ros2_msg_class(mapping["ros2_msg_type"])
            ros_msg = msg_class()
            dict_to_ros2_msg(message.payload, ros_msg)
            publisher.publish(ros_msg)
            logger.debug("[ROS2Adapter] Published to %s", mapping["ros2_topic"])
        except Exception as exc:
            logger.exception(
                "[ROS2Adapter] Failed to publish to %s: %s",
                mapping.get("ros2_topic"), exc,
            )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _spin_loop(self) -> None:
        """Keep the rclpy executor spinning until stop() is called."""
        logger.debug("[ROS2Adapter] Spin thread started.")
        try:
            while self._running and rclpy.ok():
                self._executor.spin_once(timeout_sec=0.1)
        except Exception as exc:
            logger.exception("[ROS2Adapter] Spin loop error: %s", exc)
        logger.debug("[ROS2Adapter] Spin thread exited.")

    def _build_subscriptions(self) -> None:
        """Create a ROS2 subscription for each applicable mapping."""
        for mapping in self._mappings:
            direction = mapping.get("direction", "")
            if direction not in (
                MessageDirection.ROS2_TO_EXTERNAL.value,
                MessageDirection.BIDIRECTIONAL.value,
            ):
                continue

            ros2_topic   = mapping["ros2_topic"]
            msg_type_str = mapping["ros2_msg_type"]
            name         = mapping["name"]

            try:
                msg_class = get_ros2_msg_class(msg_type_str)
            except (ImportError, ValueError) as exc:
                logger.error(
                    "[ROS2Adapter] Cannot subscribe to %s (%s): %s",
                    ros2_topic, msg_type_str, exc,
                )
                continue

            # Build a per-mapping closure that captures ``name``
            callback = self._make_subscription_callback(name)
            sub = self._node.create_subscription(
                msg_class,
                ros2_topic,
                callback,
                qos_profile=10,
            )
            self._subscribers[name] = sub
            logger.debug("[ROS2Adapter] Subscribed to ROS2 topic %s", ros2_topic)

    def _build_publishers(self) -> None:
        """Create a ROS2 publisher for each applicable mapping."""
        for mapping in self._mappings:
            direction = mapping.get("direction", "")
            if direction not in (
                MessageDirection.EXTERNAL_TO_ROS2.value,
                MessageDirection.BIDIRECTIONAL.value,
            ):
                continue

            ros2_topic   = mapping["ros2_topic"]
            msg_type_str = mapping["ros2_msg_type"]
            name         = mapping["name"]

            try:
                msg_class = get_ros2_msg_class(msg_type_str)
            except (ImportError, ValueError) as exc:
                logger.error(
                    "[ROS2Adapter] Cannot create publisher for %s (%s): %s",
                    ros2_topic, msg_type_str, exc,
                )
                continue

            pub = self._node.create_publisher(msg_class, ros2_topic, qos_profile=10)
            self._publishers[name] = pub
            logger.debug("[ROS2Adapter] Publisher created for ROS2 topic %s", ros2_topic)

    def _make_subscription_callback(self, mapping_name: str) -> Callable[[Any], None]:
        """Return a ROS2 subscription callback that wraps messages in BridgeMessage."""

        def callback(ros_msg: Any) -> None:
            try:
                payload = ros2_msg_to_dict(ros_msg)
                bridge_msg = BridgeMessage(
                    topic=mapping_name,
                    payload=payload,
                    source=MessageSource.ROS2,
                )
                self._emit(bridge_msg)
            except Exception as exc:
                logger.exception(
                    "[ROS2Adapter] Error in subscription callback for '%s': %s",
                    mapping_name, exc,
                )

        return callback
