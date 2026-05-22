"""
adapters/mqtt_adapter.py
------------------------
Manages a paho-mqtt client connection for publishing and subscribing.

Behaviour:
  • Publishes outbound messages as JSON envelopes: {timestamp, source, data}.
  • Only subscribes to topics whose direction is external_to_ros2 or
    bidirectional, avoiding re-ingestion of its own outbound messages.
  • Runs the paho network loop in a background thread (loop_start).
  • Reconnects automatically via paho's built-in reconnect logic.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Dict, List

from robot_bridge.adapters.base_adapter import BaseAdapter
from robot_bridge.models.message import BridgeMessage, MessageDirection, MessageSource

logger = logging.getLogger(__name__)

try:
    import paho.mqtt.client as mqtt
    _PAHO_AVAILABLE = True
except ImportError:
    _PAHO_AVAILABLE = False
    logger.warning(
        "[MQTTAdapter] paho-mqtt is not installed. "
        "Run: pip install paho-mqtt"
    )


class MQTTAdapter(BaseAdapter):
    """
    Protocol adapter for MQTT via paho-mqtt.

    Parameters
    ----------
    config:
        The ``mqtt`` section of bridge_config.yaml (broker_host, broker_port,
        client_id, keepalive, tls settings …).
    mappings:
        Full list of topic mapping dicts.
    """

    def __init__(self, config: Dict[str, Any], mappings: List[Dict[str, Any]]) -> None:
        super().__init__(config, mappings)
        self._client:           Any = None
        self._connected:        bool = False
        self._connect_lock:     threading.Lock = threading.Lock()

        # Build lookup tables for fast O(1) routing
        # mqtt_topic  → mapping_name  (inbound)
        self._mqtt_topic_to_name: Dict[str, str] = {}
        # mapping_name → mqtt_topic   (outbound)
        self._name_to_mqtt_topic: Dict[str, str] = {}

        for m in self._mappings:
            name = m.get("name", "")
            mqtt_topic = m.get("mqtt_topic", "")
            if name and mqtt_topic:
                self._name_to_mqtt_topic[name] = mqtt_topic
                direction = m.get("direction", "")
                if direction in (
                    MessageDirection.EXTERNAL_TO_ROS2.value,
                    MessageDirection.BIDIRECTIONAL.value,
                ):
                    self._mqtt_topic_to_name[mqtt_topic] = name

    # ------------------------------------------------------------------
    # BaseAdapter interface
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Connect to the MQTT broker and start the background loop."""
        if not _PAHO_AVAILABLE:
            logger.error("[MQTTAdapter] Cannot start — paho-mqtt not available.")
            return

        broker_host = self._config.get("broker_host", "localhost")
        broker_port = int(self._config.get("broker_port", 1883))
        keepalive   = int(self._config.get("keepalive", 60))
        client_id   = self._config.get("client_id", "robot_bridge")

        self._client = mqtt.Client(client_id=client_id)
        self._client.on_connect    = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message    = self._on_message

        # Optional TLS configuration
        tls_cfg = self._config.get("tls", {})
        if tls_cfg.get("enabled", False):
            ca_certs   = tls_cfg.get("ca_certs")
            certfile   = tls_cfg.get("certfile")
            keyfile    = tls_cfg.get("keyfile")
            self._client.tls_set(ca_certs=ca_certs, certfile=certfile, keyfile=keyfile)
            logger.info("[MQTTAdapter] TLS configured.")

        # Optional authentication
        username = self._config.get("username")
        password = self._config.get("password")
        if username:
            self._client.username_pw_set(username, password)

        logger.info("[MQTTAdapter] Connecting to %s:%s …", broker_host, broker_port)
        try:
            self._client.connect(broker_host, broker_port, keepalive)
        except Exception as exc:
            logger.error(
                "[MQTTAdapter] Initial connection failed: %s — will retry automatically.", exc
            )

        # Start the paho background network thread
        self._client.loop_start()

    def stop(self) -> None:
        """Stop the background loop and disconnect cleanly."""
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
        self._connected = False
        logger.info("[MQTTAdapter] Stopped.")

    def send(self, message: BridgeMessage) -> None:
        """
        Publish *message* to the MQTT broker.

        The payload is wrapped in a standard envelope:
            {timestamp: <float>, source: <str>, data: <dict>}
        """
        if not self._connected or self._client is None:
            logger.warning(
                "[MQTTAdapter] Cannot publish — not connected. Message dropped: %s", message
            )
            return

        mqtt_topic = self._name_to_mqtt_topic.get(message.topic)
        if mqtt_topic is None:
            logger.debug(
                "[MQTTAdapter] No MQTT topic for mapping '%s' — dropping.", message.topic
            )
            return

        envelope = {
            "timestamp": message.timestamp,
            "source":    message.source.value,
            "data":      message.payload,
        }
        try:
            payload_str = json.dumps(envelope, default=str)
            qos = int(self._config.get("qos", 0))
            self._client.publish(mqtt_topic, payload_str, qos=qos)
            logger.debug("[MQTTAdapter] Published to %s", mqtt_topic)
        except Exception as exc:
            logger.exception("[MQTTAdapter] Publish error on %s: %s", mqtt_topic, exc)

    # ------------------------------------------------------------------
    # paho callbacks
    # ------------------------------------------------------------------

    def _on_connect(
        self,
        client: Any,
        userdata: Any,
        flags: Dict[str, Any],
        rc: int,
    ) -> None:
        """Called by paho when the connection is established."""
        if rc == 0:
            with self._connect_lock:
                self._connected = True
            logger.info("[MQTTAdapter] Connected to broker (rc=%d).", rc)
            self._subscribe_to_inbound_topics()
        else:
            logger.warning("[MQTTAdapter] Connection refused (rc=%d).", rc)

    def _on_disconnect(self, client: Any, userdata: Any, rc: int) -> None:
        """Called by paho when the connection is lost."""
        with self._connect_lock:
            self._connected = False
        if rc != 0:
            logger.warning(
                "[MQTTAdapter] Unexpected disconnect (rc=%d). paho will reconnect …", rc
            )
        else:
            logger.info("[MQTTAdapter] Disconnected cleanly.")

    def _on_message(self, client: Any, userdata: Any, msg: Any) -> None:
        """Called by paho for every received MQTT message."""
        mqtt_topic = msg.topic
        mapping_name = self._mqtt_topic_to_name.get(mqtt_topic)
        if mapping_name is None:
            # This can happen if paho delivers a retained message on a topic
            # we no longer subscribe to after a reconnect.
            logger.debug("[MQTTAdapter] Received message on unregistered topic %s", mqtt_topic)
            return

        try:
            raw = json.loads(msg.payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.warning(
                "[MQTTAdapter] Could not parse payload on %s: %s", mqtt_topic, exc
            )
            return

        # Accept both raw dicts and the {timestamp, source, data} envelope
        if isinstance(raw, dict) and "data" in raw:
            payload = raw["data"]
        else:
            payload = raw

        bridge_msg = BridgeMessage(
            topic=mapping_name,
            payload=payload if isinstance(payload, dict) else {"value": payload},
            source=MessageSource.MQTT,
            metadata={"mqtt_topic": mqtt_topic, "qos": msg.qos, "retain": msg.retain},
        )
        self._emit(bridge_msg)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _subscribe_to_inbound_topics(self) -> None:
        """Subscribe to all MQTT topics that feed into ROS2."""
        for mqtt_topic in self._mqtt_topic_to_name:
            qos = int(self._config.get("qos", 0))
            self._client.subscribe(mqtt_topic, qos)
            logger.debug("[MQTTAdapter] Subscribed to MQTT topic '%s'", mqtt_topic)
