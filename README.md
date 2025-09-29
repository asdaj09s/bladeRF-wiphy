# bladeRF-wiphy

bladeRF-wiphy is an open-source IEEE 802.11 compatible software defined radio VHDL modem

<img align="right" src="https://nuand.com/bladeRF-wiphy-birb.png">

## What is the bladeRF-wiphy project?
The bladeRF-wiphy project is an open-source IEEE 802.11 compatible software defined radio VHDL modem. The modem is able to modulate and demodulate 802.11 packets (the protocol WiFi is based on), and run directly on the bladeRF 2.0 micro xA9’s FPGA.

The bladeRF-wiphy coupled with Linux mac80211 allows the [bladeRF 2.0 micro xA9](https://www.nuand.com/product/bladerf-xA9/) to become a software defined radio 802.11 access point! 802.11 packets (PDUs) are modulated and demodulated directly on the FPGA, so only 802.11 packets are transferred between the FPGA and libbladeRF.

## Documentation
A technical deep dive of bladeRF-wiphy https://www.nuand.com/bladeRF-wiphy/

Instructions to compile, install, and run bladeRF-wiphy and tools https://www.nuand.com/bladeRF-wiphy-instructions/

Instructions to simulate bladeRF-wiphy: https://www.nuand.com/bladeRF-wiphy-simulation/

### MANET bring-up helpers

This repository now includes host-side tooling that follows the roadmap
described in [`docs/manet_plan.md`](docs/manet_plan.md) to establish an ad-hoc
link between two bladeRF-wiphy nodes.  The utilities live under
[`host/manet`](host/manet) and provide:

- `setup_ibss.py`: Automates joining an IEEE 802.11 IBSS cell, assigns IP
  addressing, and can optionally launch an OLSR/OLSRv2 routing daemon to begin
  exchanging MANET control traffic.
- `hello_daemon.py`: A lightweight UDP neighbor discovery loop that mirrors the
  NHDP step of the MANET plan, allowing quick validation that both radios see
  each other once the IBSS link is established.

See [`host/manet/README.md`](host/manet/README.md) for detailed usage examples.

<p align="center">
<img width="50%" height="50%" src="https://www.nuand.com/wp-content/uploads/2021/01/bw-block-diagram.png">
</p>

## Features
 - IEEE 802.11 compatible FPGA based PHY receiver and transmitter
 - Compatible with [bladeRF 2.0 micro xA9](https://www.nuand.com/product/bladerf-xA9/)
 - Linux mac80211 MAC integration
 - RX and TX monitor mode support
 - Hardware Distributed Coordination Function (DCF) allows quick turn-around time ACKs
 - High-performance equalizer – implements Zero Forcing (ZF) and optionally Decision Feedback Equalizer (DFE)

## Modulation schemes
 - DSSS - CCK
 - OFDM - 20MHz (6Mbps, 9Mbps, 12Mbps, 18Mbps, 24Mbps, 36Mbps, 48Mbps, 54Mbps)

## Modulation constellations
 - DSSS-CCK DBPSK
 - OFDM-BPSK
 - OFDM-QPSK
 - OFDM-16-QAM
 - OFDM-64-QAM

## Contact information
Email: bladeRF@nuand.com

Slack: See Slack section on this [page](https://www.nuand.com/support/)
