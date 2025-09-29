#!/usr/bin/env python3
"""Ad-hoc MANET neighbor discovery helper for bladeRF-wiphy nodes.

This daemon periodically emits a small JSON "hello" message via link-layer
broadcast and listens for responses from peers joined to the same IBSS cell.
It provides a lightweight control-plane primitive that mirrors the NHDP style
neighbor table discussed in the MANET planning notes while avoiding a hard
dependency on a full routing suite for bring-up tests.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import selectors
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional


DEFAULT_PORT = 6696


@dataclass
class Neighbor:
    node_id: str
    address: str
    last_seen: float


class NeighborTable:
    """Tracks live neighbors and persists summaries to disk."""

    def __init__(self, expiry: float, state_file: Optional[Path]) -> None:
        self._expiry = expiry
        self._state_file = state_file
        self._neighbors: Dict[str, Neighbor] = {}
        self._dirty = False

    def update(self, neighbor: Neighbor) -> None:
        previous = self._neighbors.get(neighbor.node_id)
        if previous is None or previous.address != neighbor.address:
            self._dirty = True
        else:
            # Mark dirty on first sighting after expiry.
            if time.monotonic() - previous.last_seen > self._expiry:
                self._dirty = True
        self._neighbors[neighbor.node_id] = neighbor

    def prune(self) -> None:
        now = time.monotonic()
        removed = [node_id for node_id, entry in self._neighbors.items() if now - entry.last_seen > self._expiry]
        if removed:
            self._dirty = True
        for node_id in removed:
            del self._neighbors[node_id]

    def maybe_persist(self) -> None:
        if not self._dirty or self._state_file is None:
            return
        payload = {
            "generated_at": time.time(),
            "neighbors": [
                {
                    "node_id": entry.node_id,
                    "address": entry.address,
                    "last_seen": entry.last_seen,
                }
                for entry in sorted(self._neighbors.values(), key=lambda item: item.node_id)
            ],
        }
        self._state_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        self._dirty = False

    def format_summary(self) -> str:
        if not self._neighbors:
            return "No neighbors discovered"
        lines = ["Discovered neighbors:"]
        for entry in sorted(self._neighbors.values(), key=lambda item: item.node_id):
            age = time.monotonic() - entry.last_seen
            lines.append(f"  - {entry.node_id} @ {entry.address} (age {age:.1f}s)")
        return "\n".join(lines)


@dataclass
class HelloConfig:
    source: ipaddress.IPv4Interface
    node_id: str
    broadcast: ipaddress.IPv4Address
    port: int
    interval: float
    expiry: float
    interface: Optional[str]
    verbose: bool


@dataclass
class HelloMessage:
    node_id: str
    address: str
    timestamp: float

    def encode(self) -> bytes:
        return json.dumps({"node_id": self.node_id, "address": self.address, "timestamp": self.timestamp}).encode("utf-8")

    @staticmethod
    def decode(payload: bytes) -> "HelloMessage":
        decoded = json.loads(payload.decode("utf-8"))
        return HelloMessage(
            node_id=str(decoded["node_id"]),
            address=str(decoded["address"]),
            timestamp=float(decoded["timestamp"]),
        )


def create_socket(config: HelloConfig) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    if config.interface:
        # Linux-specific bind to device to avoid leaking frames onto other NICs.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, config.interface.encode("utf-8"))
    sock.bind(("", config.port))
    return sock


def send_hello(sock: socket.socket, config: HelloConfig) -> None:
    message = HelloMessage(
        node_id=config.node_id,
        address=str(config.source.ip),
        timestamp=time.time(),
    )
    sock.sendto(message.encode(), (str(config.broadcast), config.port))
    if config.verbose:
        print(f"tx hello -> {config.broadcast} ({len(message.encode())} bytes)")


def receive_hello(sock: socket.socket, config: HelloConfig) -> Optional[HelloMessage]:
    payload, addr = sock.recvfrom(4096)
    try:
        message = HelloMessage.decode(payload)
    except (ValueError, json.JSONDecodeError) as exc:
        if config.verbose:
            print(f"Ignoring malformed hello from {addr}: {exc}")
        return None
    if message.address == str(config.source.ip) and message.node_id == config.node_id:
        # Ignore our own broadcast.
        return None
    if config.verbose:
        print(f"rx hello <- {addr[0]}: {message}")
    return message


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ip", required=True, help="Local IP with prefix length (e.g. 10.23.0.1/24)")
    parser.add_argument("--node-id", default=os.uname().nodename, help="Identifier advertised to peers")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="UDP port for hello exchanges")
    parser.add_argument("--interval", type=float, default=1.0, help="Seconds between hello transmissions")
    parser.add_argument("--expiry", type=float, default=5.0, help="Neighbor expiry interval in seconds")
    parser.add_argument("--interface", help="Restrict socket binding to a specific interface")
    parser.add_argument("--state-file", type=Path, help="Optional path to persist neighbor table JSON")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    return parser.parse_args(argv)


def build_config(args: argparse.Namespace) -> HelloConfig:
    try:
        source = ipaddress.ip_interface(args.ip)
    except ValueError as exc:
        print(f"error: invalid IP address '{args.ip}': {exc}", file=sys.stderr)
        sys.exit(2)

    if not isinstance(source, ipaddress.IPv4Interface):
        print("error: only IPv4 is currently supported", file=sys.stderr)
        sys.exit(2)

    broadcast = source.network.broadcast_address
    if broadcast == source.ip:
        print("error: broadcast address overlaps with host address", file=sys.stderr)
        sys.exit(2)

    return HelloConfig(
        source=source,
        node_id=str(args.node_id),
        broadcast=broadcast,
        port=int(args.port),
        interval=float(args.interval),
        expiry=float(args.expiry),
        interface=args.interface,
        verbose=bool(args.verbose),
    )


def run_daemon(config: HelloConfig, state_file: Optional[Path]) -> None:
    selector = selectors.DefaultSelector()
    table = NeighborTable(config.expiry, state_file)
    sock = create_socket(config)
    sock.setblocking(False)
    selector.register(sock, selectors.EVENT_READ)

    next_tx = time.monotonic()

    try:
        while True:
            timeout = max(0.0, next_tx - time.monotonic())
            events = selector.select(timeout)
            for key, _ in events:
                if key.fileobj is sock:
                    message = receive_hello(sock, config)
                    if message is None:
                        continue
                    table.update(
                        Neighbor(
                            node_id=message.node_id,
                            address=message.address,
                            last_seen=time.monotonic(),
                        )
                    )
                    print(table.format_summary())
            now = time.monotonic()
            if now >= next_tx:
                send_hello(sock, config)
                next_tx = now + config.interval
            table.prune()
            table.maybe_persist()
    except KeyboardInterrupt:
        print("Stopping hello daemon")
    finally:
        selector.unregister(sock)
        sock.close()


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = parse_args(argv)
    config = build_config(args)
    run_daemon(config, args.state_file)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
