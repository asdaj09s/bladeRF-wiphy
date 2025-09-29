# bladeRF-wiphy MANET helpers

The utilities in this directory translate the planning work captured in
[`docs/manet_plan.md`](../../docs/manet_plan.md) into concrete host-side tooling
that enables two bladeRF-wiphy nodes to form a basic Mobile Ad-hoc Network
(MANET).  They focus on the initial milestone of bringing up an IEEE 802.11 IBSS
link and exchanging neighbor discovery beacons before integrating a
full-featured routing suite.

## `setup_ibss.py`

`setup_ibss.py` automates the Linux mac80211 steps required to join an ad-hoc
(IBSS) cell.  It accepts the SSID, operating frequency, and IPv4 addressing
information, configures deterministic BSSID values (unless one is provided), and
optionally starts an OLSR/OLSRv2 routing daemon once the wireless link is up.

Channel width and rate controls are exposed via `--channel-mode`,
`--basic-rate`, and `--mcast-rate`, enabling experiments in spectrum-constrained
bands such as 902–928 MHz.  The helper can also request `iw` to lock the
frequency (`--fixed-frequency`) and apply a regulatory country code before
making any other changes (`--country`).


Typical usage on each bladeRF host is::

    sudo ./setup_ibss.py --interface wlan0 --ssid bladerf-mesh \
        --frequency 5180 --ip 10.23.0.1/24 --peer 10.23.0.2

The script can also be run in `--dry-run` mode to preview the `iw`/`ip`
commands it will execute.  When `--olsr` is requested the helper auto-detects an
available `olsrd2` or `olsrd` binary, generating a throw-away configuration file
if necessary so that two nodes can immediately exchange routing hellos.  For a
902 MHz ISM trial with 5 MHz channels, a single node might be configured with::

    sudo ./setup_ibss.py --interface wlan0 --ssid bladerf-mesh-900 \
        --frequency 904 --channel-mode 5MHz --country US \
        --ip 10.23.0.1/24 --peer 10.23.0.2

if necessary so that two nodes can immediately exchange routing hellos.


## `hello_daemon.py`

`hello_daemon.py` provides a lightweight UDP-based neighbor discovery loop that
emits JSON "hello" frames to the IBSS broadcast address and maintains a table of
recently seen peers.  The state can optionally be persisted to disk for
integration with external monitoring tools.  This mimics the NeighborHood
Discovery Protocol (NHDP) stage described in the MANET design notes and offers a
self-contained validation tool when a full routing daemon is unavailable.

Run it after the IBSS link is up::

    ./hello_daemon.py --ip 10.23.0.1/24 --interface wlan0 --state-file /tmp/neighbors.json

The script prints neighbor summaries whenever it sees an update and writes the
latest table to the specified state file.
