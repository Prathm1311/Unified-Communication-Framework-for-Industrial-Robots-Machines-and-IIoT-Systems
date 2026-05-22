"""
bridge_manager.py
-----------------
The central routing engine for RobotBridge.

Responsibilities:
  1. Load and validate bridge_config.yaml.
  2. Instantiate all three protocol adapters.
  3. Wire them together by injecting a routing callback.
  4. Apply direction rules to decide which adapter(s) receive each message.
  5. Prevent self-echo (a message is not re-delivered to the adapter that
     created it).
  6. Provide start() and stop() lifecycle methods.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

import yaml

from robot_bridge.adapters.mqtt_adapter import MQTTAdapter
from robot_bridge.adapters.rest_adapter import RESTAdapter
from robot_bridge.adapters.ros2_adapter import ROS2Adapter
from robot_bridge.models.message import BridgeMessage, MessageDirection, MessageSource

logger = logging.getLogger(__name__)

# Default config path relative to the package root
_DEFAULT_CONFIG = os.path.join(
    os.path.dirname(__file__), "..", "config", "bridge_config.yaml"
)


class BridgeManager:
    """
    Loads configuration, creates all adapters, and routes messages between them.

    Parameters
    ----------
    config_path:
        Absolute or relative path to bridge_config.yaml.
        Defaults to ``config/bridge_config.yaml`` next to the package root.
    """

    def __init__(self, config_path: Optional[str] = None) -> None:
        self._config_path = os.path.abspath(config_path or _DEFAULT_CONFIG)
        self._config:    Dict[str, Any]      = {}
        self._mappings:  List[Dict[str, Any]] = []
        self._adapters:  Dict[str, Any]       = {}
        self._running:   bool                 = False

        # Pre-compute direction lookup: mapping_name → MessageDirection value str
        self._directions: Dict[str, str] = {}

    # ------------------------------------------------------------------
    # Public lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Load config, create and wire adapters, then start all of them."""
        self._load_config()
        self._adapters = self._create_adapters()
        self._wire_callbacks()
        self._start_adapters()
        self._running = True
        logger.info("RobotBridge is running")

    def stop(self) -> None:
        """Stop all adapters in reverse start order."""
        self._running = False
        for name, adapter in reversed(list(self._adapters.items())):
            try:
                adapter.stop()
                logger.info("  ✓  %-10s adapter stopped", name.upper())
            except Exception as exc:
                logger.error("Error stopping %s adapter: %s", name, exc)

    # ------------------------------------------------------------------
    # Configuration loading
    # ------------------------------------------------------------------

    def _load_config(self) -> None:
        """Parse bridge_config.yaml and populate internal state."""
        if not os.path.isfile(self._config_path):
            raise FileNotFoundError(
                f"Config file not found: {self._config_path}\n"
                "Pass --config <path> or place bridge_config.yaml at the default location."
            )

        with open(self._config_path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)

        if not isinstance(raw, dict):
            raise ValueError(f"Invalid config file (expected YAML mapping): {self._config_path}")

        self._config   = raw
        self._mappings = raw.get("mappings", [])

        # Validate that every mapping has the required keys
        required_keys = {"name", "ros2_topic", "ros2_msg_type", "mqtt_topic", "rest_endpoint", "direction"}
        for i, mapping in enumerate(self._mappings):
            missing = required_keys - set(mapping.keys())
            if missing:
                raise ValueError(
                    f"Mapping #{i} is missing required keys: {missing}\n"
                    f"Mapping contents: {mapping}"
                )
            self._directions[mapping["name"]] = mapping["direction"]

        logger.info(
            "[BridgeManager] Config loaded: %d mappings from %s",
            len(self._mappings), self._config_path,
        )

    # ------------------------------------------------------------------
    # Adapter creation
    # ------------------------------------------------------------------

    def _create_adapters(self) -> Dict[str, Any]:
        """Instantiate all three protocol adapters."""
        ros2_cfg = self._config.get("ros2", {})
        mqtt_cfg = self._config.get("mqtt", {})
        rest_cfg = self._config.get("rest", {})

        return {
            "ros2": ROS2Adapter(ros2_cfg, self._mappings),
            "mqtt": MQTTAdapter(mqtt_cfg, self._mappings),
            "rest": RESTAdapter(rest_cfg, self._mappings),
        }

    def _wire_callbacks(self) -> None:
        """Inject the routing callback into every adapter."""
        for adapter in self._adapters.values():
            adapter.set_message_callback(self._route_message)

    def _start_adapters(self) -> None:
        """Start each adapter and log success/failure."""
        print("RobotBridge starting up …")
        for name, adapter in self._adapters.items():
            try:
                adapter.start()
                print(f"  \u2713  {name.upper():<10} adapter started")
            except Exception as exc:
                logger.error("  ✗  Failed to start %s adapter: %s", name.upper(), exc)

    # ------------------------------------------------------------------
    # Routing engine
    # ------------------------------------------------------------------

    def _route_message(self, message: BridgeMessage) -> None:
        """
        Decide which adapter(s) should receive *message* and deliver it.

        Rules
        -----
        1. The message must match a known mapping name; unknown topics are
           dropped with a warning.
        2. The direction setting of the mapping determines which adapters
           receive the message.
        3. Self-echo prevention: the source adapter never receives its own
           message back.
        """
        mapping_name = message.topic
        direction    = self._directions.get(mapping_name)

        if direction is None:
            logger.warning(
                "[BridgeManager] Received message for unknown mapping '%s' from %s — dropped.",
                mapping_name, message.source.value,
            )
            return

        source = message.source

        logger.debug(
            "[BridgeManager] Routing message: topic=%s source=%s direction=%s",
            mapping_name, source.value, direction,
        )

        if direction == MessageDirection.ROS2_TO_EXTERNAL.value:
            # ROS2 → MQTT + REST
            if source != MessageSource.ROS2:
                logger.debug(
                    "[BridgeManager] Direction mismatch: %s is ros2_to_external but source is %s — dropped.",
                    mapping_name, source.value,
                )
                return
            self._deliver(message, "mqtt")
            self._deliver(message, "rest")

        elif direction == MessageDirection.EXTERNAL_TO_ROS2.value:
            # MQTT / REST → ROS2
            if source == MessageSource.ROS2:
                logger.debug(
                    "[BridgeManager] Direction mismatch: %s is external_to_ros2 but source is ros2 — dropped.",
                    mapping_name,
                )
                return
            self._deliver(message, "ros2")

        elif direction == MessageDirection.BIDIRECTIONAL.value:
            # Deliver to all adapters except the source
            for adapter_name, adapter in self._adapters.items():
                source_adapter_name = source.value  # "ros2", "mqtt", "rest"
                if adapter_name != source_adapter_name:
                    self._deliver(message, adapter_name)
        else:
            logger.warning(
                "[BridgeManager] Unknown direction '%s' for mapping '%s' — dropped.",
                direction, mapping_name,
            )

    def _deliver(self, message: BridgeMessage, adapter_name: str) -> None:
        """Send *message* to the named adapter, catching any exceptions."""
        adapter = self._adapters.get(adapter_name)
        if adapter is None:
            logger.warning("[BridgeManager] No adapter named '%s' — skipping.", adapter_name)
            return
        try:
            adapter.send(message)
        except Exception as exc:
            logger.exception(
                "[BridgeManager] Error delivering to %s adapter: %s", adapter_name, exc
            )
