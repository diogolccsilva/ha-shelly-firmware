# Shelly Local Firmware — Home Assistant integration

[![hacs][hacs-badge]][hacs] [![validate][validate-badge]][validate-workflow] [![tests][tests-badge]][tests-workflow]

A local Home Assistant integration that puts Shelly firmware updates in
**Settings → System → Updates**, including for devices that are not allowed to
reach the internet.

The official Shelly integration asks the device to download firmware from
Shelly. If that download is blocked, the device reports that it is up to date
even when a newer version exists. This integration checks Shelly's catalog
from Home Assistant, and when you press **Update** it hosts the image under
`/config/www` and calls `Shelly.Update` with that local URL.

## Features

- One **Firmware** update entity per device, shown with the rest of your updates.
- Catalog check for any Gen2 or newer RPC device (`WallDisplay`, `BluGwG3`, and the rest).
- Stable channel by default. A stable release is offered only when it is newer than what is installed, so a device on a newer beta is not downgraded.
- Optional beta channel.
- Local hosting (default): Home Assistant downloads the zip, checks the SHA-256 Shelly publishes in the URL, and the device installs from your LAN.
- Direct mode: the device downloads from Shelly itself, for devices that do have internet.
- The update entity is attached to the device the official Shelly integration already created.
- Shelly devices on the network show up under **Discovered**, so you can add them without typing an IP address.

BLU devices that sit behind a gateway (for example BLU TRV) are updated by the official Shelly integration through the gateway. This integration updates the gateway and the Wall Display, not those sleepy BLU devices.

## Install

Add this repository in HACS as a custom integration, install **Shelly Local Firmware**, and restart Home Assistant.

In HACS, open **Integrations**, choose the menu, then **Custom repositories**. Add `https://github.com/diogolccsilva/ha-shelly-firmware` with category **Integration**. Download **Shelly Local Firmware** and restart.

To install without HACS, copy `custom_components/shelly_firmware` into your Home Assistant `config/custom_components` folder and restart.

Restart Home Assistant after installing. Zeroconf and DHCP discovery are
registered at startup, so a restart is what makes the devices appear.

Then open **Settings → Devices & services**. Shelly devices on the network
show up under **Discovered** as **Shelly Local Firmware**, including devices
already added to the official Shelly integration. Choose **Add** and confirm
the **Home Assistant address**. That address has to be one the device can
open, usually `http://192.168.1.10:8123`. `homeassistant.local` often does not
resolve on the device.

If a device does not appear, add the integration manually and enter its IP
address. A password is asked for only when the device requires one.

## Options

- **Host the firmware on Home Assistant** — on by default. Turn it off only if the device can reach `fwcdn.shelly.cloud` itself.
- **Channel** — `stable` or `beta`.
- **Home Assistant address** — origin used in the firmware URL.

After a successful install the staged zip is deleted. Files in `www` are
reachable on your LAN without a Home Assistant login while an update is in
progress.

## Certificate pins

Shelly's update hosts use a certificate signed by Allterco, which is not in the
public trust store. `catalog.py` pins the current certificates for
`updates.shelly.cloud` and `fwcdn.shelly.cloud`. The firmware file is also
checked against the SHA-256 in the catalog URL. If Shelly rotates a
certificate, update the pins in `catalog.py` (the fixtures in `tests/fixtures`
are the certificates the pins were taken from).

## Tested with

- Shelly Wall Display (`SAWD-0A1XX10EU1`, app `WallDisplay`)
- Shelly BLU Gateway Gen3 (`S3GW-1DBT001`, app `BluGwG3`)

## Disclaimer

This is an unofficial, community integration. "Shelly" is a trademark of its
owner. Installing firmware reboots the device. Use at your own risk.

This integration was built with AI assistance and tested on real hardware by the
author.

## License

[MIT](LICENSE)

[hacs]: https://github.com/hacs/integration
[hacs-badge]: https://img.shields.io/badge/HACS-Custom-41BDF5.svg
[validate-badge]: https://github.com/diogolccsilva/ha-shelly-firmware/actions/workflows/validate.yml/badge.svg
[validate-workflow]: https://github.com/diogolccsilva/ha-shelly-firmware/actions/workflows/validate.yml
[tests-badge]: https://github.com/diogolccsilva/ha-shelly-firmware/actions/workflows/tests.yml/badge.svg
[tests-workflow]: https://github.com/diogolccsilva/ha-shelly-firmware/actions/workflows/tests.yml
