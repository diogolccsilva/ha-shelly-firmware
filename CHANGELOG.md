# Changelog

## 0.1.2

- Serve staged firmware from a path the integration registers itself, so the first update works without an extra Home Assistant restart.

## 0.1.1

- Discover Shelly devices on the network and offer this integration under Discovered.
- Ask for a device password only when the device requires one.

## 0.1.0

- Check Shelly's firmware catalog from Home Assistant and show a Firmware update entity.
- Host the image on Home Assistant and install it with `Shelly.Update` when the device cannot reach Shelly.
- Stable and beta channels. Stable is offered only when it is newer than the installed version.
