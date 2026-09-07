"""Matter Server client: discover contact sensors and subscribe to changes."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

import aiohttp
from matter_server.client.client import MatterClient
from matter_server.client.exceptions import CannotConnect, ConnectionClosed
from matter_server.client.models.node import MatterNode
from matter_server.common.models import EventType

from matter_blind_controller.config import AppConfig
from matter_blind_controller.sensors import (
    SensorRegistry,
    find_contact_endpoints,
    read_state_value,
)

LOGGER = logging.getLogger(__name__)


class MatterController:
    """Connects to python-matter-server and watches configured contact sensors."""

    def __init__(self, config: AppConfig, registry: SensorRegistry) -> None:
        self._config = config
        self._registry = registry
        self._stop = asyncio.Event()
        self._unsubscribers: list[Callable[[], None]] = []

    def request_stop(self) -> None:
        """Signal the reconnect loop to exit."""
        self._stop.set()

    async def run_forever(self) -> None:
        """Connect with exponential backoff until stop is requested."""
        delay = self._config.matter.reconnect_initial_seconds
        max_delay = self._config.matter.reconnect_max_seconds

        while not self._stop.is_set():
            try:
                await self._run_session()
                if self._stop.is_set():
                    break
                LOGGER.warning("Matter Server connection closed; reconnecting")
                delay = self._config.matter.reconnect_initial_seconds
            except asyncio.CancelledError:
                raise
            except (CannotConnect, ConnectionClosed, aiohttp.ClientError) as exc:
                LOGGER.warning("Matter Server unreachable (%s); will reconnect", exc)
            except Exception:
                LOGGER.exception("Matter session failed; will reconnect")

            if self._stop.is_set():
                break

            LOGGER.info("Reconnecting in %.1fs", delay)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
                break
            except TimeoutError:
                pass
            delay = min(delay * 2, max_delay)

        LOGGER.info("Matter controller stopped")

    async def _run_session(self) -> None:
        url = self._config.matter.url
        self._registry.reset_cached_states()
        self._clear_subscriptions()

        timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=None)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            client = MatterClient(url, session)
            LOGGER.info("Connecting to Matter Server at %s", url)

            init_ready = asyncio.Event()
            listen_task = asyncio.create_task(
                client.start_listening(init_ready=init_ready),
                name="matter-listen",
            )
            init_task = asyncio.create_task(init_ready.wait(), name="matter-init")
            stop_task = asyncio.create_task(self._stop.wait(), name="matter-stop")

            try:
                done, _ = await asyncio.wait(
                    {listen_task, init_task, stop_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if self._stop.is_set() or stop_task in done:
                    LOGGER.info("Stop requested before Matter init completed")
                    return

                if listen_task in done:
                    # Connection failed or closed before init finished.
                    _raise_task_result(listen_task)
                    raise RuntimeError("Matter listen ended before initialization")

                # Initial node dump is ready.
                LOGGER.info("Connected to Matter Server")
                self._on_connected(client)

                done, _ = await asyncio.wait(
                    {listen_task, stop_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if self._stop.is_set() or stop_task in done:
                    LOGGER.info("Stop requested; disconnecting from Matter Server")
                    return

                _raise_task_result(listen_task)
            finally:
                init_task.cancel()
                stop_task.cancel()
                await self._shutdown_client(client, listen_task)
                self._clear_subscriptions()

    async def _shutdown_client(
        self, client: MatterClient, listen_task: asyncio.Task[Any]
    ) -> None:
        try:
            await client.disconnect()
        except Exception:  # noqa: BLE001
            LOGGER.debug("Error during Matter disconnect", exc_info=True)

        if listen_task.done():
            return

        listen_task.cancel()
        try:
            await listen_task
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001
            LOGGER.debug("Listen task ended with error after cancel", exc_info=True)

    def _clear_subscriptions(self) -> None:
        while self._unsubscribers:
            unsub = self._unsubscribers.pop()
            try:
                unsub()
            except Exception:  # noqa: BLE001
                LOGGER.debug("Unsubscribe failed", exc_info=True)

    def _on_connected(self, client: MatterClient) -> None:
        nodes = client.get_nodes()
        LOGGER.info("Discovered %d Matter node(s)", len(nodes))
        for node in sorted(nodes, key=lambda n: n.node_id):
            self._log_node_summary(node)

        configured = self._registry.configured_node_ids
        found_ids = {node.node_id for node in nodes}
        for node_id in sorted(configured - found_ids):
            name = self._registry.name_for(node_id)
            LOGGER.warning(
                "Configured sensor %s (node %s) not found on Matter Server",
                name,
                node_id,
            )

        for node in nodes:
            if node.node_id not in configured:
                continue
            name = self._registry.name_for(node.node_id)
            assert name is not None
            contacts = find_contact_endpoints(node)
            if not contacts:
                LOGGER.warning(
                    "Configured sensor %s (node %s) has no contact sensor endpoint",
                    name,
                    node.node_id,
                )
                continue
            if not node.available:
                LOGGER.warning(
                    "Configured sensor %s (node %s) is currently unavailable",
                    name,
                    node.node_id,
                )

            info = contacts[0]
            if len(contacts) > 1:
                LOGGER.info(
                    "Node %s (%s) has %d contact endpoints; using endpoint %s",
                    node.node_id,
                    name,
                    len(contacts),
                    info.endpoint_id,
                )

            self._subscribe_contact(client, name, info.node_id, info.attribute_path)
            value = read_state_value(node, info.endpoint_id)
            if value is None:
                LOGGER.warning(
                    "Could not read initial state for %s (node %s)",
                    name,
                    node.node_id,
                )
            else:
                self._registry.log_initial_state(node.node_id, value)

    def _subscribe_contact(
        self,
        client: MatterClient,
        name: str,
        node_id: int,
        attribute_path: str,
    ) -> None:
        def _on_update(_event: EventType, data: Any) -> None:
            self._registry.handle_state_value(node_id, data)

        unsub = client.subscribe_events(
            callback=_on_update,
            event_filter=EventType.ATTRIBUTE_UPDATED,
            node_filter=node_id,
            attr_path_filter=attribute_path,
        )
        self._unsubscribers.append(unsub)
        LOGGER.info(
            "Subscribed to %s (node %s, attribute %s)",
            name,
            node_id,
            attribute_path,
        )

    def _log_node_summary(self, node: MatterNode) -> None:
        contacts = find_contact_endpoints(node)
        configured_name = self._registry.name_for(node.node_id)
        contact_eps = ", ".join(str(c.endpoint_id) for c in contacts) or "-"
        LOGGER.info(
            "node_id=%s name=%r available=%s configured_as=%r contact_endpoints=%s",
            node.node_id,
            node.name,
            node.available,
            configured_name,
            contact_eps,
        )


def _raise_task_result(task: asyncio.Task[Any]) -> None:
    """Re-raise an exception from a finished task, if any."""
    if task.cancelled():
        raise asyncio.CancelledError()
    exc = task.exception()
    if exc is not None:
        raise exc
