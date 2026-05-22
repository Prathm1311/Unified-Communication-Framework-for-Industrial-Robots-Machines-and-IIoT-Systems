"""
adapters/rest_adapter.py
------------------------
Runs a FastAPI web server in a background thread.

Auto-generates endpoints for every topic mapping:
  • GET  /<rest_endpoint>     → return latest cached telemetry
  • POST /<command_endpoint>  → accept a command and forward as BridgeMessage
  • GET  /telemetry           → return ALL cached telemetry in one response
  • GET  /health              → simple health-check

The server runs via uvicorn in a daemon thread so it doesn't block the main
process.  The telemetry cache is a plain dict (mapping_name → latest payload)
protected by a threading.Lock.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional

from robot_bridge.adapters.base_adapter import BaseAdapter
from robot_bridge.models.message import BridgeMessage, MessageDirection, MessageSource

logger = logging.getLogger(__name__)

try:
    import uvicorn
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel

    class CommandPayload(BaseModel):
        """Request body schema for all POST /command/* endpoints."""
        data: Dict[str, Any]

    _FASTAPI_AVAILABLE = True
except ImportError:
    _FASTAPI_AVAILABLE = False
    CommandPayload = None  # type: ignore[assignment,misc]
    logger.warning(
        "[RESTAdapter] FastAPI/uvicorn not installed. "
        "Run: pip install 'fastapi>=0.100' 'uvicorn[standard]>=0.23' 'pydantic>=2'"
    )


class RESTAdapter(BaseAdapter):
    """
    Protocol adapter for HTTP REST via FastAPI.

    Parameters
    ----------
    config:
        The ``rest`` section of bridge_config.yaml (host, port).
    mappings:
        Full list of topic mapping dicts.
    """

    def __init__(self, config: Dict[str, Any], mappings: List[Dict[str, Any]]) -> None:
        super().__init__(config, mappings)
        self._app:             Optional[Any] = None   # FastAPI instance
        self._server:          Optional[Any] = None   # uvicorn.Server instance
        self._server_thread:   Optional[threading.Thread] = None
        self._cache:           Dict[str, Dict[str, Any]] = {}  # mapping_name → latest data
        self._cache_lock:      threading.Lock = threading.Lock()
        self._running:         bool = False

    # ------------------------------------------------------------------
    # BaseAdapter interface
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Build the FastAPI app, register all endpoints, start uvicorn."""
        if not _FASTAPI_AVAILABLE:
            logger.error("[RESTAdapter] Cannot start — FastAPI/uvicorn not available.")
            return

        self._app = FastAPI(
            title="RobotBridge REST API",
            description=(
                "Auto-generated REST interface for RobotBridge. "
                "GET endpoints stream cached telemetry from the robot. "
                "POST endpoints send commands to the robot."
            ),
            version="1.0.0",
        )

        self._register_global_endpoints()
        self._register_mapping_endpoints()

        host = self._config.get("host", "0.0.0.0")
        port = int(self._config.get("port", 8000))
        log_level = self._config.get("log_level", "warning")

        uvicorn_config = uvicorn.Config(
            app=self._app,
            host=host,
            port=port,
            log_level=log_level,
        )
        self._server = uvicorn.Server(config=uvicorn_config)

        self._running = True
        self._server_thread = threading.Thread(
            target=self._server.run,
            name="rest_server",
            daemon=True,
        )
        self._server_thread.start()

        # Give the server a moment to bind the port before continuing
        time.sleep(0.5)
        logger.info("[RESTAdapter] Server started on http://%s:%d  (docs: /docs)", host, port)

    def stop(self) -> None:
        """Signal uvicorn to shut down and wait for the thread to exit."""
        self._running = False
        if self._server:
            self._server.should_exit = True
        if self._server_thread and self._server_thread.is_alive():
            self._server_thread.join(timeout=5.0)
        logger.info("[RESTAdapter] Stopped.")

    def send(self, message: BridgeMessage) -> None:
        """
        Cache the latest telemetry payload so GET endpoints can serve it.

        POST-originated commands come back through send() after routing, but
        we don't want to cache commands as telemetry, so we only cache
        messages whose source is ROS2.
        """
        # Always update cache for all sources (allows REST echo for bidirectional)
        with self._cache_lock:
            self._cache[message.topic] = {
                "mapping":   message.topic,
                "timestamp": message.timestamp,
                "source":    message.source.value,
                "data":      message.payload,
            }
        logger.debug("[RESTAdapter] Cache updated for mapping '%s'", message.topic)

    # ------------------------------------------------------------------
    # Endpoint registration
    # ------------------------------------------------------------------

    def _register_global_endpoints(self) -> None:
        """Register /health and /telemetry (all cache) endpoints."""
        adapter = self  # Capture reference for closures

        @adapter._app.get("/health", tags=["System"])
        async def health_check() -> JSONResponse:
            """Return a simple health-check response."""
            return JSONResponse(
                content={
                    "status": "ok",
                    "timestamp": time.time(),
                    "cached_topics": list(adapter._cache.keys()),
                }
            )

        @adapter._app.get("/telemetry", tags=["Telemetry"])
        async def all_telemetry() -> JSONResponse:
            """Return the latest cached data for every telemetry mapping."""
            with adapter._cache_lock:
                snapshot = dict(adapter._cache)
            return JSONResponse(content={"telemetry": snapshot})

    def _register_mapping_endpoints(self) -> None:
        """
        For every mapping, dynamically register a GET and/or POST endpoint.

        GET  endpoints are registered for ros2_to_external and bidirectional.
        POST endpoints are registered for external_to_ros2 and bidirectional.
        """
        adapter = self  # Capture reference for closures

        for mapping in self._mappings:
            name         = mapping.get("name", "")
            rest_endpoint = mapping.get("rest_endpoint", "")
            direction     = mapping.get("direction", "")

            if not name or not rest_endpoint:
                continue

            # Ensure endpoint starts with /
            if not rest_endpoint.startswith("/"):
                rest_endpoint = "/" + rest_endpoint

            # ---- GET (telemetry) ----------------------------------------
            if direction in (
                MessageDirection.ROS2_TO_EXTERNAL.value,
                MessageDirection.BIDIRECTIONAL.value,
            ):
                # Create a closure that captures the current ``name``
                self._add_get_endpoint(adapter, rest_endpoint, name)

            # ---- POST (command) -----------------------------------------
            if direction in (
                MessageDirection.EXTERNAL_TO_ROS2.value,
                MessageDirection.BIDIRECTIONAL.value,
            ):
                # Derive command path: replace leading /telemetry with /command
                command_endpoint = rest_endpoint.replace("/telemetry/", "/command/", 1)
                if command_endpoint == rest_endpoint:
                    # No /telemetry/ prefix — just prepend /command
                    path_part = rest_endpoint.lstrip("/")
                    command_endpoint = f"/command/{path_part}"
                self._add_post_endpoint(adapter, command_endpoint, name)

    @staticmethod
    def _add_get_endpoint(adapter: "RESTAdapter", path: str, mapping_name: str) -> None:
        """Register a single GET endpoint for *mapping_name* at *path*."""

        @adapter._app.get(path, tags=["Telemetry"], name=f"get_{mapping_name}")
        async def get_telemetry() -> JSONResponse:
            with adapter._cache_lock:
                data = adapter._cache.get(mapping_name)
            if data is None:
                raise HTTPException(
                    status_code=404,
                    detail=(
                        f"No data received yet for mapping '{mapping_name}'. "
                        "Wait for the robot to publish on the corresponding ROS2 topic."
                    ),
                )
            return JSONResponse(content=data)

    @staticmethod
    def _add_post_endpoint(adapter: "RESTAdapter", path: str, mapping_name: str) -> None:
        """Register a single POST endpoint for *mapping_name* at *path*."""

        @adapter._app.post(path, tags=["Commands"], name=f"post_{mapping_name}")
        async def post_command(body: CommandPayload) -> JSONResponse:
            bridge_msg = BridgeMessage(
                topic=mapping_name,
                payload=body.data,
                source=MessageSource.REST,
            )
            adapter._emit(bridge_msg)
            return JSONResponse(
                content={
                    "status":  "accepted",
                    "mapping": mapping_name,
                    "timestamp": bridge_msg.timestamp,
                },
                status_code=202,
            )
