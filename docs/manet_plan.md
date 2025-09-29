# Mobile Ad-Hoc Networking (MANET) Notes for bladeRF-wiphy

## bladeRF-wiphy architecture recap

- **Top-level PHY/MAC pipeline.** `wlan_top` integrates the receive and transmit clock domains, FIFO interfaces to the host, automatic gain control handshakes, and the over-the-air IQ sample ports. Its internal TX and RX state machines cover header parsing, payload buffering, ACK handling, and retry timing, providing the core control path for packet exchange.【F:fpga/vhdl/wlan_top.vhd†L34-L143】
- **Distributed Coordination Function (DCF).** The `wlan_dcf` module implements the CSMA/CA backoff logic using contention window timers, random backoff masking, and SIFS/DIFS gating. It asserts `tx_sifs_ready`/`tx_difs_ready` when the medium is idle and enforces RX blocking while waiting, enabling hardware-assisted contention and ACK turnaround.【F:fpga/vhdl/wlan_dcf.vhd†L26-L140】
- **PHY framing.** `wlan_framer` computes OFDM signal fields, length/parity bits, and packet CRC bytes before feeding the encoder and modulator, ensuring transmitted frames match IEEE 802.11 formatting constraints.【F:fpga/vhdl/wlan_framer.vhd†L29-L200】
- **ACK generation.** `wlan_ack_generator` buffers peer MAC addresses, synthesizes acknowledgment payloads, and streams 10-byte ACK responses to the TX datapath once the previous transmission finishes.【F:fpga/vhdl/wlan_ack_generator.vhd†L31-L159】

Together, these VHDL blocks provide a standards-compliant PHY with enough MAC intelligence (DCF + ACK) to interoperate with Linux mac80211 on the host while offloading strict timing requirements to the FPGA fabric.

## Target MANET capability

A MANET allows two or more bladeRF 2.0 micro xA9 radios to form a decentralized network where each node can originate traffic and forward data without infrastructure.【F:docs/external_sources.md†L1-L7】 For the initial milestone—two nodes exchanging traffic—both radios need:

1. A peer-to-peer PHY/MAC link (e.g., 802.11 IBSS or 802.11s mesh) operating on the same channel.
2. A lightweight routing/control plane so each node discovers its neighbor and exchanges Layer 2/Layer 3 reachability information.
3. Tooling to configure and monitor the MANET from the host OS.

## Design options and references

### Option A: 802.11 IBSS with host-based MANET routing

1. **Enable IBSS/adhoc mode in mac80211.** bladeRF-wiphy already exposes standard 802.11 frame handling, so enabling the Linux driver to create an IBSS (`iw dev wlan0 ibss join ...`) leverages the existing DCF and ACK support with minimal FPGA changes. Ensure the RX/TX pipeline accepts beacons and peer management frames, and expose configuration bits for channel, BSSID, and beacon intervals via `config_reg` or host control paths.【F:fpga/vhdl/wlan_top.vhd†L44-L78】
2. **Add beacon support if missing.** IBSS peers periodically transmit beacons. Extend `wlan_framer` or a companion management framer to assemble beacon management frames and schedule them through the TX FSM (new states or timer). Incorporate timestamp updates and capability bits sourced from mac80211.
3. **Run a routing daemon.** Deploy a proactive protocol such as OLSR/OLSRv2 or BATMAN-adv so nodes automatically exchange topology information. OLSR disseminates link-state updates via hello/TC messages, while OLSRv2 formalizes MANET optimizations; BATMAN focuses on large community meshes.【F:docs/external_sources.md†L9-L26】 Linux packages exist for all three, allowing experimentation without FPGA modifications.
4. **Two-node validation.** Configure both radios to join the same IBSS SSID/BSSID, start the routing daemon (or rely on static routes for two nodes), and verify bidirectional throughput/latency. Add capture hooks in the RX path (`rx_packet_control`, `rx_fifo_data`) to dump frames for debugging.【F:fpga/vhdl/wlan_top.vhd†L48-L59】

### Option B: 802.11s mesh point implementation

1. **Mesh peering management.** IEEE 802.11s defines mesh peering, path selection, and synchronization. Augment the MAC control plane to handle mesh peering open/confirm frames and path selection frames. These can reuse the existing framer with additional management frame templates and state machine extensions.【F:fpga/vhdl/wlan_framer.vhd†L181-L200】
2. **Mesh metrics and routing.** For two nodes, the default Hybrid Wireless Mesh Protocol (HWMP) suffices, but integrating with host-based routing (e.g., BATMAN or OLSRv2) provides flexibility. Evaluate whether to keep mesh path selection in userspace (using Linux 802.11s stack) or offload parts to the FPGA.
3. **Timing considerations.** Mesh requires synchronized beacons (TBTT), so ensure the FPGA exposes accurate TSF updates and supports per-node beacon scheduling. `wlan_top`'s TX FSM could provide hooks for timed transmissions and quick ACK turnaround to meet mesh peer requirements.【F:fpga/vhdl/wlan_top.vhd†L90-L143】

### Routing/control plane choices

| Protocol | Characteristics | Notes |
| --- | --- | --- |
| OLSRv2 | Proactive link-state routing standardized for MANETs; relies on NHDP for neighbor discovery.【F:docs/external_sources.md†L9-L18】 | Good for small meshes; existing Linux daemons (olsrd2). |
| NHDP | 1-hop and 2-hop neighbor discovery used by OLSRv2.【F:docs/external_sources.md†L18-L26】 | Implement in userspace to feed MAC neighbor tables. |
| BATMAN | Community-driven mesh routing optimizing for large-scale deployments.【F:docs/external_sources.md†L27-L31】 | Linux `batman-adv` module integrates with 802.11s/IBSS. |

## Recommended implementation roadmap

1. **Gap analysis.** Audit existing FPGA/driver support for management frames (beacons, peer link frames) and identify configuration register hooks required for IBSS/mesh parameters.
2. **Driver enhancements.** Extend the bladeRF-wiphy mac80211 glue layer to expose IBSS/mesh modes, TSF readback, and beacon scheduling. Provide debugfs hooks for MANET metrics.
3. **FPGA updates.**
   - Add a management framer path that can craft beacons/mesh frames and enqueue them through `wlan_top`'s TX FSM alongside data traffic.
   - Provide timers/counters to trigger transmissions and maintain TSF, possibly leveraging `tx_ota_req` handshake for coordination.【F:fpga/vhdl/wlan_top.vhd†L73-L79】
   - Ensure `wlan_dcf` can honor contention requirements for periodic beacons by exposing contention window tuning parameters.【F:fpga/vhdl/wlan_dcf.vhd†L74-L112】
4. **Host routing integration.** Package scripts to install and configure OLSRv2 or BATMAN-adv, enabling automatic link bring-up when two nodes detect each other.
5. **Testing.** Develop a regression plan covering RF loopback, two-node over-the-air IBSS throughput, routing convergence time, and resilience to channel changes. Capture raw IQ and frame logs for analysis using existing FIFO interfaces.【F:fpga/vhdl/wlan_top.vhd†L55-L63】

## External references captured for future study

To keep this repository self-contained, store short excerpts of public-domain/CC-BY sources alongside URLs in `docs/external_sources.md` (added in this change). These cover MANET fundamentals, IEEE 802.11s mesh networking, and candidate routing protocols (OLSR/OLSRv2, NHDP, B.A.T.M.A.N.).
