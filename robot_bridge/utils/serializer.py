"""
utils/serializer.py
-------------------
Converts ROS2 message objects to plain Python dicts and vice-versa.

All conversion is done reflectively so that arbitrary ROS2 message types work
without any per-type code.  The only limitation is that deeply custom binary
fields (e.g. raw image buffers stored as `bytes`) are stringified rather than
preserved verbatim — sufficient for bridging JSON-friendly telemetry.
"""

from __future__ import annotations

import importlib
import logging
from typing import Any, Dict, Type

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def ros2_msg_to_dict(msg: Any) -> Dict[str, Any]:
    """
    Recursively convert a ROS2 message object into a JSON-serialisable dict.

    Parameters
    ----------
    msg:
        Any rclpy / ROS2 message instance (e.g. ``geometry_msgs.msg.PoseStamped``).

    Returns
    -------
    dict
        A plain Python dictionary mirroring the message fields.
    """
    result: Dict[str, Any] = {}

    # rclpy messages expose their field names via __slots__ or get_fields_and_field_types
    field_names = _get_field_names(msg)

    for field_name in field_names:
        value = getattr(msg, field_name, None)
        result[field_name] = _convert_value(value)

    return result


def dict_to_ros2_msg(data: Dict[str, Any], msg_instance: Any) -> Any:
    """
    Populate a pre-constructed ROS2 message instance from a plain dict.

    Only fields present in *data* are written; missing fields keep their
    default values.  Nested message objects are populated recursively.

    Parameters
    ----------
    data:
        Plain Python dict (from JSON payload).
    msg_instance:
        A freshly constructed ROS2 message object to populate.

    Returns
    -------
    The populated message instance (same object as *msg_instance*).
    """
    if not isinstance(data, dict):
        logger.warning("dict_to_ros2_msg: expected dict, got %s — skipping", type(data))
        return msg_instance

    field_names = _get_field_names(msg_instance)

    for key, value in data.items():
        if key not in field_names:
            logger.debug("dict_to_ros2_msg: unknown field %r — skipping", key)
            continue

        current_attr = getattr(msg_instance, key, None)

        if current_attr is not None and hasattr(current_attr, "__slots__"):
            # Nested ROS2 message — recurse
            if isinstance(value, dict):
                dict_to_ros2_msg(value, current_attr)
            else:
                logger.warning(
                    "dict_to_ros2_msg: expected dict for nested field %r, got %s",
                    key, type(value),
                )
        elif isinstance(value, list) and isinstance(current_attr, (list, tuple)):
            # Sequences: set directly; ROS2 accepts Python lists for array fields
            setattr(msg_instance, key, value)
        else:
            try:
                setattr(msg_instance, key, value)
            except (AttributeError, TypeError) as exc:
                logger.warning("dict_to_ros2_msg: could not set %r = %r: %s", key, value, exc)

    return msg_instance


def get_ros2_msg_class(type_string: str) -> Type[Any]:
    """
    Dynamically import and return a ROS2 message class from a type string.

    Parameters
    ----------
    type_string:
        A string of the form ``"package/msg/TypeName"``
        (e.g. ``"geometry_msgs/msg/PoseStamped"``).

    Returns
    -------
    The message class.

    Raises
    ------
    ValueError
        If the string is not in the expected ``package/msg/TypeName`` format.
    ImportError
        If the package or message type cannot be found.
    """
    parts = type_string.strip().split("/")
    if len(parts) != 3:
        raise ValueError(
            f"ROS2 message type string must be 'package/msg/TypeName', got {type_string!r}"
        )

    package, _msg_subdir, class_name = parts
    module_path = f"{package}.{_msg_subdir}.{class_name}"

    try:
        module = importlib.import_module(f"{package}.{_msg_subdir}")
        return getattr(module, class_name)
    except (ImportError, AttributeError):
        # Fall back to direct module import (some ROS2 packages use flat layout)
        try:
            module = importlib.import_module(module_path)
            return module  # type: ignore[return-value]
        except ImportError as exc:
            raise ImportError(
                f"Could not import ROS2 message type {type_string!r}. "
                f"Make sure the package is installed and the workspace is sourced. "
                f"Original error: {exc}"
            ) from exc


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _get_field_names(msg: Any) -> list[str]:
    """Return the list of field names for a ROS2 message object."""
    # Modern rclpy messages expose get_fields_and_field_types() class method
    if hasattr(msg, "get_fields_and_field_types"):
        try:
            return list(msg.get_fields_and_field_types().keys())
        except Exception:
            pass

    # Fallback: use __slots__ (strips leading underscore if present)
    if hasattr(msg, "__slots__"):
        return [s.lstrip("_") for s in msg.__slots__]

    # Last resort: public attributes only
    return [attr for attr in dir(msg) if not attr.startswith("_")]


def _convert_value(value: Any) -> Any:
    """Recursively convert a single field value to a JSON-serialisable type."""
    if value is None:
        return None

    # Primitive JSON-safe types
    if isinstance(value, (bool, int, float, str)):
        return value

    # bytes / bytearray — encode as hex string rather than losing data
    if isinstance(value, (bytes, bytearray)):
        return value.hex()

    # Lists and tuples (ROS2 array fields)
    if isinstance(value, (list, tuple)):
        return [_convert_value(item) for item in value]

    # Nested ROS2 message (has __slots__ or get_fields_and_field_types)
    if hasattr(value, "__slots__") or hasattr(value, "get_fields_and_field_types"):
        return ros2_msg_to_dict(value)

    # Fallback: stringify
    return str(value)
