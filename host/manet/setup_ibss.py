#!/usr/bin/env python3
"""Utility helpers to configure a bladeRF-wiphy interface for MANET use.

The script provisions an interface for IEEE 802.11 IBSS (ad-hoc) operation,
assigns an IP address, and optionally launches an OLSR routing daemon if one
is present on the system.  Channel width, multicast/basic rate hints, and a
regulatory country code can be supplied to make 902–928 MHz IBSS experiments
more convenient.  The helper codifies the high-level plan captured in
``docs/manet_plan.md`` by providing a repeatable automation entrypoint that can
be invoked on both bladeRF 2.0 micro xA9 nodes.

Example usage for a two-node testbed::

    sudo ./setup_ibss.py --interface wlan0 --ssid bladerf-mesh \
        --frequency 5180 --ip 10.23.0.1/24 --peer 10.23.0.2

    sudo ./setup_ibss.py --interface wlan0 --ssid bladerf-mesh \
        --frequency 5180 --ip 10.23.0.2/24 --peer 10.23.0.1

After the interface is configured, a minimal UDP hello daemon (see
``hello_daemon.py``) can be launched to verify bi-directional reachability and
export neighbor state.
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional


@dataclass
class CommandResult:
    """Container for executed command metadata."""

    command: List[str]
    returncode: int


class CommandRunner:
    """Thin wrapper around subprocess that logs and optionally dry-runs."""

    def __init__(self, dry_run: bool = False) -> None:
        self.dry_run = dry_run

    def run(self, argv: Iterable[str], *, check: bool = True) -> CommandResult:
        command = list(argv)
        print(f"$ {' '.join(shlex.quote(arg) for arg in command)}")
        if self.dry_run:
            return CommandResult(command, 0)
        completed = subprocess.run(command, check=check)
        return CommandResult(command, completed.returncode)


def ensure_tool(binary: str) -> str:
    """Return the absolute path to *binary* or exit with an error."""

    resolved = shutil.which(binary)
    if resolved is None:
        print(f"error: required tool '{binary}' not found in PATH", file=sys.stderr)
        sys.exit(2)
    return resolved


def compute_ibss_bssid(ssid: str, frequency_mhz: int) -> str:
    """Derive a locally-administered BSSID that remains stable per SSID.

    The procedure is deterministic and keeps the multicast bit clear while
    asserting the locally administered bit.  This mirrors how Linux mac80211
    synthesizes IBSS identifiers when a BSSID is not provided manually.
    """

    import hashlib

    digest = hashlib.sha256(f"{ssid}\0{frequency_mhz}".encode("utf-8")).digest()
    raw = bytearray(digest[:6])
    raw[0] |= 0x02  # locally administered
    raw[0] &= 0xFE  # unicast
    return ":".join(f"{b:02x}" for b in raw)


def configure_interface(
    runner: CommandRunner,
    *,
    interface: str,
    ssid: str,
    frequency_mhz: int,
    channel_mode: str,
    fixed_frequency: bool,
    basic_rates: Optional[List[float]],
    multicast_rate: Optional[float],
    address: ipaddress.IPv4Interface,
    beacon_interval: int,
    mtu: Optional[int],
    bssid: Optional[str],
    tx_power_dbm: Optional[float],
) -> None:
    """Bring the interface into IBSS mode and assign the provided address."""

    ensure_tool("iw")
    ensure_tool("ip")

    bssid_value = bssid or compute_ibss_bssid(ssid, frequency_mhz)

    # Bring the link down while reconfiguring to avoid mac80211 rejects.
    runner.run(["ip", "link", "set", interface, "down"])
    runner.run(["iw", "dev", interface, "set", "type", "ibss"])

    join_command = [
        "iw",
        "dev",
        interface,
        "ibss",
        "join",
        ssid,
        str(frequency_mhz),
        channel_mode,
        bssid_value,
        "beacon-interval",
        str(beacon_interval),
    ]
    if fixed_frequency:
        join_command.append("fixed-freq")
    if basic_rates:
        basic_rate_string = ",".join(f"{rate:g}" for rate in basic_rates)
        join_command.extend(["basic-rates", basic_rate_string])
    if multicast_rate is not None:
        join_command.extend(["mcast-rate", f"{multicast_rate:g}"])
    runner.run(join_command)

    runner.run(["ip", "addr", "flush", "dev", interface])
    runner.run(["ip", "addr", "add", str(address), "dev", interface])
    if mtu is not None:
        runner.run(["ip", "link", "set", interface, "mtu", str(mtu)])
    runner.run(["ip", "link", "set", interface, "up"])

    if tx_power_dbm is not None:
        # iw expects mBm (dBm * 100)
        tx_power_mbm = int(tx_power_dbm * 100)
        runner.run([
            "iw",
            "dev",
            interface,
            "set",
            "txpower",
            "fixed",
            str(tx_power_mbm),
        ])

    print(
        textwrap.dedent(
            f"""
            Interface {interface} joined IBSS '{ssid}' ({bssid_value}) on {frequency_mhz} MHz.
            Assigned address {address} with beacon interval {beacon_interval} TU.
            """
        ).strip()
    )


def add_peer_route(runner: CommandRunner, *, interface: str, peer: ipaddress.IPv4Address) -> None:
    """Install a host route towards *peer* via the MANET interface."""

    runner.run(["ip", "route", "replace", str(peer), "dev", interface])


def build_olsr_command(
    *,
    interface: str,
    local_ip: ipaddress.IPv4Address,
    preferred: Optional[str],
    provided_config: Optional[Path],
) -> Optional[List[str]]:
    """Return a launch command for OLSR/OLSRv2 if available."""

    binary_candidates = [preferred] if preferred else []
    binary_candidates += ["olsrd2", "olsrd"]

    binary_path: Optional[str] = None
    for candidate in binary_candidates:
        if candidate is None:
            continue
        resolved = shutil.which(candidate)
        if resolved:
            binary_path = resolved
            break

    if binary_path is None:
        return None

    binary_name = os.path.basename(binary_path)

    if provided_config is not None:
        return [binary_path, "-f", str(provided_config)]

    if binary_name.startswith("olsrd2"):
        # Generate a minimal OLSRv2 config that only enables the interface.
        cfg = textwrap.dedent(
            f"""
            [global]
            fork false

            [log]
            information true

            [interface]
            ifname "{interface}"
            """
        ).strip()
    else:
        # Legacy olsrd configuration
        cfg = textwrap.dedent(
            f"""
            DebugLevel 1
            IpVersion 4
            Hna4 {{}}
            UseHysteresis no
            LinkQualityLevel 2
            LinkQualityAlgorithm "etx_ff"
            AllowNoInt yes
            Pollrate 0.05
            TcRedundancy 2
            MprCoverage 7
            Willingness 3

            InterfaceDefaults {{
                HelloInterval 2.0
                HelloValidityTime 20.0
                TcInterval 5.0
                TcValidityTime 60.0
            }}

            Interface "{interface}" {{
                Mode "mesh"
                Ip4Broadcast 255.255.255.255
            }}
            """
        ).strip()

    temp_fd, temp_path = tempfile.mkstemp(prefix="manet-olsr-", suffix=".conf")
    with os.fdopen(temp_fd, "w", encoding="utf-8") as handle:
        handle.write(cfg)

    if binary_name.startswith("olsrd"):
        return [binary_path, "-i", interface, "-nofork", "-f", temp_path]

    # olsrd2
    return [binary_path, "-f", temp_path]


def launch_olsr(
    runner: CommandRunner,
    *,
    interface: str,
    ip_address: ipaddress.IPv4Interface,
    preferred_binary: Optional[str],
    config: Optional[Path],
) -> None:
    command = build_olsr_command(
        interface=interface,
        local_ip=ip_address.ip,
        preferred=preferred_binary,
        provided_config=config,
    )
    if command is None:
        print("warning: no OLSR binary found; skipping MANET routing daemon")
        return

    runner.run(command, check=False)
    print(
        textwrap.dedent(
            """
            OLSR daemon launched in the foreground. Use Ctrl-C to terminate when finished.
            For unattended operation consider supervising the command with systemd or tmux.
            """
        ).strip()
    )


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interface", required=True, help="wireless interface name")
    parser.add_argument("--ssid", required=True, help="IBSS network identifier")
    parser.add_argument(
        "--frequency",
        type=int,
        required=True,
        help="Operating frequency in MHz (e.g. 5180)",
    )
    parser.add_argument(
        "--channel-mode",
        default="HT20",
        choices=["HT20", "HT40+", "HT40-", "5MHz", "10MHz", "NOHT"],
        help="Channel mode / width hint passed to iw (default: HT20)",
    )
    parser.add_argument(
        "--fixed-frequency",
        action="store_true",
        help="Request fixed-frequency IBSS operation (disables freq drift)",
    )
    parser.add_argument(
        "--basic-rate",
        dest="basic_rates",
        type=float,
        action="append",
        help="Add a basic rate in Mbps (repeatable; disables defaults)",
    )
    parser.add_argument(
        "--mcast-rate",
        type=float,
        help="Override the multicast rate in Mbps",
    )
    parser.add_argument(
        "--ip",
        required=True,
        help="IPv4 address with prefix length, e.g. 10.23.0.1/24",
    )
    parser.add_argument(
        "--beacon-interval",
        type=int,
        default=100,
        help="Beacon interval in TU (default: 100)",
    )
    parser.add_argument(
        "--bssid",
        help="Optional fixed BSSID; defaults to deterministic hash of SSID/frequency",
    )
    parser.add_argument(
        "--peer",
        action="append",
        help="Optional peer IPv4 address to install as a host route (repeatable)",
    )
    parser.add_argument(
        "--mtu",
        type=int,
        help="Override MTU on the MANET interface",
    )
    parser.add_argument(
        "--tx-power",
        type=float,
        help="Transmit power in dBm (requires regulatory permissions)",
    )
    parser.add_argument(
        "--country",
        help="Optional two-letter country code for iw reg set",
    )
    parser.add_argument(
        "--olsr",
        action="store_true",
        help="Attempt to launch an OLSR/OLSRv2 daemon after joining the IBSS",
    )
    parser.add_argument(
        "--olsr-binary",
        help="Preferred OLSR binary name/path (overrides auto-detection)",
    )
    parser.add_argument(
        "--olsr-config",
        type=Path,
        help="Explicit OLSR configuration file to pass to the daemon",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print commands without executing",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = parse_args(argv)

    try:
        address = ipaddress.ip_interface(args.ip)
    except ValueError as exc:  # pragma: no cover - argparse guards typical usage
        print(f"error: invalid IP specification: {exc}", file=sys.stderr)
        return 2

    peers = []
    if args.peer:
        for peer in args.peer:
            try:
                peers.append(ipaddress.ip_address(peer))
            except ValueError as exc:
                print(f"error: invalid peer address {peer!r}: {exc}", file=sys.stderr)
                return 2

    runner = CommandRunner(dry_run=args.dry_run)

    if args.country:
        ensure_tool("iw")
        runner.run(["iw", "reg", "set", args.country.upper()])

    configure_interface(
        runner,
        interface=args.interface,
        ssid=args.ssid,
        frequency_mhz=args.frequency,
        channel_mode=args.channel_mode,
        fixed_frequency=args.fixed_frequency,
        basic_rates=args.basic_rates,
        multicast_rate=args.mcast_rate,
        address=address,
        beacon_interval=args.beacon_interval,
        mtu=args.mtu,
        bssid=args.bssid,
        tx_power_dbm=args.tx_power,
    )

    for peer in peers:
        add_peer_route(runner, interface=args.interface, peer=peer)

    if args.olsr:
        launch_olsr(
            runner,
            interface=args.interface,
            ip_address=address,
            preferred_binary=args.olsr_binary,
            config=args.olsr_config,
        )

    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())
