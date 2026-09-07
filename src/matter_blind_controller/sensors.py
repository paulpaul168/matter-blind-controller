"""Contact sensor helpers and state logging."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from chip.clusters import Objects as Clusters
from matter_server.client.models.device_types import ContactSensor
from matter_server.client.models.node import MatterEndpoint, MatterNode
from matter_server.common.helpers.util import create_attribute_path

LOGGER = logging.getLogger(__name__)

BOOLEAN_STATE = Clusters.BooleanState
STATE_VALUE = Clusters.BooleanState.Attributes.StateValue


@dataclass(frozen=True, slots=True)
class ContactSensorInfo:
    """Resolved contact sensor endpoint on a Matter node."""

    node_id: int
    endpoint_id: int
    attribute_path: str
    device_name: str | None


def state_value_to_label(value: bool) -> str:
    """Map Matter BooleanState.StateValue to OPEN/CLOSED.

    Matter reports True when contact is present (closed).
    """
    return "CLOSED" if value else "OPEN"


def find_contact_endpoints(node: MatterNode) -> list[ContactSensorInfo]:
    """Return contact-sensor endpoints on a node."""
    results: list[ContactSensorInfo] = []
    for endpoint in node.endpoints.values():
        if not _is_contact_endpoint(endpoint):
            continue
        path = create_attribute_path(
            endpoint.endpoint_id,
            BOOLEAN_STATE.id,
            STATE_VALUE.attribute_id,
        )
        results.append(
            ContactSensorInfo(
                node_id=node.node_id,
                endpoint_id=endpoint.endpoint_id,
                attribute_path=path,
                device_name=node.name,
            )
        )
    return results


def _is_contact_endpoint(endpoint: MatterEndpoint) -> bool:
    if ContactSensor in endpoint.device_types:
        return endpoint.has_attribute(BOOLEAN_STATE, STATE_VALUE)
    return endpoint.has_cluster(BOOLEAN_STATE) and endpoint.has_attribute(
        BOOLEAN_STATE, STATE_VALUE
    )


def read_state_value(node: MatterNode, endpoint_id: int) -> bool | None:
    """Read current BooleanState.StateValue, or None if unavailable."""
    try:
        value = node.get_attribute_value(endpoint_id, BOOLEAN_STATE, STATE_VALUE)
    except Exception:  # noqa: BLE001 — defensive against incomplete node data
        LOGGER.debug(
            "Failed reading StateValue node=%s endpoint=%s",
            node.node_id,
            endpoint_id,
            exc_info=True,
        )
        return None
    if isinstance(value, bool):
        return value
    return None


class SensorRegistry:
    """Maps configured Matter node IDs to names and logs state changes."""

    def __init__(self, sensor_map: dict[int, str]) -> None:
        self._names = dict(sensor_map)
        self._last_states: dict[int, str] = {}

    @property
    def configured_node_ids(self) -> frozenset[int]:
        return frozenset(self._names)

    def name_for(self, node_id: int) -> str | None:
        return self._names.get(node_id)

    def handle_state_value(self, node_id: int, value: Any) -> None:
        """Log OPEN/CLOSED when a configured sensor's StateValue changes."""
        name = self._names.get(node_id)
        if name is None:
            return
        if not isinstance(value, bool):
            LOGGER.warning(
                "Ignoring non-bool StateValue for %s (node %s): %r",
                name,
                node_id,
                value,
            )
            return

        label = state_value_to_label(value)
        previous = self._last_states.get(node_id)
        if previous == label:
            return
        self._last_states[node_id] = label
        LOGGER.info("%s: %s", name, label)

    def log_initial_state(self, node_id: int, value: bool) -> None:
        """Record and log the initial sensor state after connect."""
        self.handle_state_value(node_id, value)

    def reset_cached_states(self) -> None:
        """Clear last-known states (e.g. before a fresh reconnect)."""
        self._last_states.clear()
