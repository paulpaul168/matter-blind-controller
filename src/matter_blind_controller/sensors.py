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
POWER_SOURCE = Clusters.PowerSource
BAT_PERCENT = Clusters.PowerSource.Attributes.BatPercentRemaining
BAT_CHARGE_LEVEL = Clusters.PowerSource.Attributes.BatChargeLevel

# Matter BatPercentRemaining is in 0.5% units (0–200). ChargeLevel: 0=Ok, 1=Warning, 2=Critical.
BATTERY_WARN_PERCENT = 20


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


def battery_percent_from_raw(value: Any) -> int | None:
    """Convert Matter BatPercentRemaining (half-percent units) to 0–100."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        raw = int(value)
    except (TypeError, ValueError):
        return None
    if raw < 0:
        return None
    return max(0, min(100, raw // 2))


def find_battery_paths(node: MatterNode) -> list[tuple[int, str, str]]:
    """Return (endpoint_id, percent_path, charge_level_path) for PowerSource batteries."""
    found: list[tuple[int, str, str]] = []
    for endpoint in node.endpoints.values():
        if not endpoint.has_attribute(POWER_SOURCE, BAT_PERCENT):
            continue
        percent_path = create_attribute_path(
            endpoint.endpoint_id,
            POWER_SOURCE.id,
            BAT_PERCENT.attribute_id,
        )
        level_path = create_attribute_path(
            endpoint.endpoint_id,
            POWER_SOURCE.id,
            BAT_CHARGE_LEVEL.attribute_id,
        )
        found.append((endpoint.endpoint_id, percent_path, level_path))
    return found


def read_battery_percent(node: MatterNode, endpoint_id: int) -> int | None:
    """Read battery percent 0–100, or None if unavailable."""
    try:
        value = node.get_attribute_value(endpoint_id, POWER_SOURCE, BAT_PERCENT)
    except Exception:  # noqa: BLE001
        LOGGER.debug(
            "Failed reading BatPercentRemaining node=%s endpoint=%s",
            node.node_id,
            endpoint_id,
            exc_info=True,
        )
        return None
    return battery_percent_from_raw(value)


def read_battery_charge_level(node: MatterNode, endpoint_id: int) -> int | None:
    """Read BatChargeLevel enum, or None."""
    try:
        value = node.get_attribute_value(endpoint_id, POWER_SOURCE, BAT_CHARGE_LEVEL)
    except Exception:  # noqa: BLE001
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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
    """Maps configured Matter node IDs to names and tracks OPEN/CLOSED state."""

    def __init__(self, sensor_map: dict[int, str]) -> None:
        self._names = dict(sensor_map)
        self._node_by_name = {name: node_id for node_id, name in sensor_map.items()}
        # name -> "OPEN" | "CLOSED" (None until first reading)
        self._states: dict[str, str | None] = {name: None for name in sensor_map.values()}
        self._battery_percent: dict[str, int | None] = {
            name: None for name in sensor_map.values()
        }
        self._battery_charge_level: dict[str, int | None] = {
            name: None for name in sensor_map.values()
        }
        self._battery_warn: dict[str, bool] = {name: False for name in sensor_map.values()}

    @property
    def configured_node_ids(self) -> frozenset[int]:
        return frozenset(self._names)

    @property
    def states(self) -> dict[str, str | None]:
        """Latest known states keyed by sensor name (copy)."""
        return dict(self._states)

    def name_for(self, node_id: int) -> str | None:
        return self._names.get(node_id)

    @property
    def batteries(self) -> dict[str, int | None]:
        """Latest battery percent 0–100 keyed by sensor name (copy)."""
        return dict(self._battery_percent)

    @property
    def battery_warnings(self) -> dict[str, bool]:
        """True when that sensor battery is low."""
        return dict(self._battery_warn)

    def get(self, name: str) -> str | None:
        """Return ``OPEN``, ``CLOSED``, or ``None`` if not yet known."""
        if name not in self._states:
            raise KeyError(f"Unknown sensor name: {name}")
        return self._states[name]

    def handle_battery(
        self,
        node_id: int,
        percent: int | None = None,
        charge_level: int | None = None,
    ) -> None:
        """Update battery percent / warning for a configured node."""
        name = self._names.get(node_id)
        if name is None:
            return
        if percent is not None:
            self._battery_percent[name] = percent
        if charge_level is not None:
            self._battery_charge_level[name] = charge_level
        warn = False
        stored = self._battery_percent.get(name)
        if stored is not None and stored <= BATTERY_WARN_PERCENT:
            warn = True
        stored_lvl = self._battery_charge_level.get(name)
        if stored_lvl is not None and stored_lvl >= 1:
            warn = True
        if warn != self._battery_warn.get(name, False):
            LOGGER.warning(
                "battery %s: %s%% warn=%s",
                name,
                stored,
                warn,
            )
        self._battery_warn[name] = warn

    def handle_state_value(self, node_id: int, value: Any) -> None:
        """Update and log OPEN/CLOSED when a configured sensor's StateValue changes."""
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
        previous = self._states.get(name)
        if previous == label:
            return
        self._states[name] = label
        LOGGER.info("%s: %s", name, label)

    def log_initial_state(self, node_id: int, value: bool) -> None:
        """Record and log the initial sensor state after connect."""
        self.handle_state_value(node_id, value)

    def reset_cached_states(self) -> None:
        """Clear last-known states (e.g. before a fresh reconnect)."""
        for name in self._states:
            self._states[name] = None
            self._battery_percent[name] = None
            self._battery_charge_level[name] = None
            self._battery_warn[name] = False
