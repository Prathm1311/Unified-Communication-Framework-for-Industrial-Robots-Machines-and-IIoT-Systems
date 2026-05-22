"""
adapters/base_adapter.py
------------------------
Abstract base class that every protocol adapter must inherit from.

Design goals:
  • Enforce a consistent interface (start / stop / send).
  • Provide _emit() so any adapter can deliver an inbound BridgeMessage up to
    the BridgeManager without knowing anything about it.
  • Keep adapters fully decoupled from each other — they only speak the
    BridgeMessage language.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List

from robot_bridge.models.message import BridgeMessage

logger = logging.getLogger(__name__)


class BaseAdapter(ABC):
    """
    Abstract base for all RobotBridge protocol adapters.

    Sub-classes must implement:
        start()  – connect / bind / subscribe
        stop()   – disconnect cleanly
        send()   – forward a BridgeMessage through the protocol

    Sub-classes should call ``self._emit(bridge_msg)`` whenever they receive
    an inbound message from their protocol.

    Parameters
    ----------
    config:
        The adapter-specific section of bridge_config.yaml (a plain dict).
    mappings:
        The full list of topic mapping dicts from bridge_config.yaml.
    """

    def __init__(self, config: Dict[str, Any], mappings: List[Dict[str, Any]]) -> None:
        self._config   = config   or {}
        self._mappings = mappings or []
        self._name     = self.__class__.__name__

        # Injected by BridgeManager after construction
        self._on_message_callback: Callable[[BridgeMessage], None] | None = None

    # ------------------------------------------------------------------
    # Abstract interface — all sub-classes must implement these
    # ------------------------------------------------------------------

    @abstractmethod
    def start(self) -> None:
        """Connect to the underlying protocol and begin listening."""

    @abstractmethod
    def stop(self) -> None:
        """Disconnect cleanly; release all resources."""

    @abstractmethod
    def send(self, message: BridgeMessage) -> None:
        """
        Forward *message* through this adapter's protocol.

        Parameters
        ----------
        message:
            The BridgeMessage to deliver.
        """

    # ------------------------------------------------------------------
    # Callback registration
    # ------------------------------------------------------------------

    def set_message_callback(self, callback: Callable[[BridgeMessage], None]) -> None:
        """
        Register the function the BridgeManager uses to receive inbound messages.

        Called once during setup by BridgeManager.
        """
        self._on_message_callback = callback

    # ------------------------------------------------------------------
    # Protected helper
    # ------------------------------------------------------------------

    def _emit(self, message: BridgeMessage) -> None:
        """
        Deliver an inbound BridgeMessage to the BridgeManager.

        Sub-classes call this whenever they receive a message from their
        protocol.  If no callback has been registered yet (e.g. during tests
        where the adapter is used standalone), the message is logged and
        dropped.
        """
        if self._on_message_callback is not None:
            try:
                self._on_message_callback(message)
            except Exception as exc:
                logger.exception("[%s] Error in message callback: %s", self._name, exc)
        else:
            logger.warning(
                "[%s] _emit called but no callback registered — dropping message: %s",
                self._name, message,
            )

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def get_mapping_by_name(self, name: str) -> Dict[str, Any] | None:
        """Return the mapping dict for *name*, or None if not found."""
        for m in self._mappings:
            if m.get("name") == name:
                return m
        return None

    def __repr__(self) -> str:
        return f"{self._name}(config={self._config})"
