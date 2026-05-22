"""
main.py
-------
Entry point for RobotBridge.

Responsibilities:
  • Parse CLI arguments (--config, --log-level).
  • Configure the root logger.
  • Create and start the BridgeManager.
  • Install signal handlers so Ctrl+C shuts everything down cleanly.
  • Keep the process alive while the adapters work in background threads.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time

from robot_bridge.bridge_manager import BridgeManager


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="robot_bridge",
        description="RobotBridge — middleware between ROS2 and MQTT/REST.",
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        default=None,
        help=(
            "Path to bridge_config.yaml. "
            "Defaults to config/bridge_config.yaml next to the package root."
        ),
    )
    parser.add_argument(
        "--log-level",
        metavar="LEVEL",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging verbosity (default: INFO).",
    )
    return parser.parse_args(argv)


def _configure_logging(level_str: str) -> None:
    level = getattr(logging, level_str.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def main(argv: list[str] | None = None) -> int:
    """
    Main entry function — called by the console_scripts entry point and by
    ``python -m robot_bridge.main``.

    Returns
    -------
    int
        Exit code (0 = clean exit, 1 = error).
    """
    args = _parse_args(argv)
    _configure_logging(args.log_level)

    logger = logging.getLogger(__name__)
    manager = BridgeManager(config_path=args.config)

    # ------------------------------------------------------------------ #
    # Signal handlers — ensure clean shutdown on Ctrl+C or SIGTERM        #
    # ------------------------------------------------------------------ #
    _shutdown_requested = False

    def _handle_signal(signum: int, _frame: object) -> None:
        nonlocal _shutdown_requested
        if not _shutdown_requested:
            _shutdown_requested = True
            print("\nShutdown signal received — stopping RobotBridge …")
            try:
                manager.stop()
            except Exception as exc:
                logger.error("Error during shutdown: %s", exc)

    signal.signal(signal.SIGINT,  _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    # ------------------------------------------------------------------ #
    # Start                                                                #
    # ------------------------------------------------------------------ #
    try:
        manager.start()
    except FileNotFoundError as exc:
        logger.critical("Config file error: %s", exc)
        return 1
    except Exception as exc:
        logger.critical("Failed to start RobotBridge: %s", exc, exc_info=True)
        return 1

    # ------------------------------------------------------------------ #
    # Keep alive                                                           #
    # ------------------------------------------------------------------ #
    try:
        while not _shutdown_requested:
            time.sleep(0.5)
    except KeyboardInterrupt:
        # Fallback if signal handler did not fire (e.g. Windows)
        _handle_signal(signal.SIGINT, None)

    logger.info("RobotBridge exited.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
