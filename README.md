# Headpat Server

Windows/Linux app that bridges VRChat OSC contact data to the Headpat haptic headpat system.

## How it works

```
VRChat  →  OSC  →  Headpat Server  →  USB Serial  →  Dongle  →  BLE  →  Headpat
```

- Receives OSC messages from VRChat on port 9001
- Detects `Headpat_Left` / `Headpat_Right` / `PatStrap_*` avatar parameters
- Scales contact depth (0.0–1.0) to motor intensity
- Sends motor commands via USB serial to the Headpat Dongle

## Features

- Auto-connect to dongle on startup
- Intensity slider (saved between sessions)
- OSC debug console with verbose toggle
- Automatic firmware update detection (Headpat, Dongle, Server)
- Auto-flash when dongle is in bootloader mode (NRF52BOOT drive detected)
- Supports Pro Micro nRF52840 and Holyiot nRF52840 dongles

## Installation

### Windows
Download `HeadpatServer-Setup.exe` from [Releases](../../releases) and run it. Installs to `C:\Program Files\Headpat Server`.

### Linux
Download `HeadpatServer-x86_64.AppImage` from [Releases](../../releases), make it executable and run it.

```bash
chmod +x HeadpatServer-x86_64.AppImage
./HeadpatServer-x86_64.AppImage
```

On first launch the app will offer to set up the udev rule for serial port access (requires admin password).

## Running from source

Requires Python 3.11+.

```bash
pip install pyserial python-osc
python heatpett_server.py
```

## VRChat setup

Enable OSC in VRChat: **Settings → OSC → Enable**

Add contact receivers to your avatar with parameter names containing `headpat` or `patstrap`:
- `Headpat_Left` — left motor
- `Headpat_Right` — right motor
- `Headpat` — both motors

## Dongle board selection

In the settings (⚙) select your dongle board:
- **Pro Micro nRF52840** — nice!nano and compatible Pro Micro form factor boards
- **Holyiot nRF52840** — Holyiot nRF52840 USB Dongle

## Firmware updates

The server checks GitHub for new firmware versions on startup. When an update is available, a **↑** badge appears in the title bar. Click it to open the update dialog.

To flash dongle firmware: click **Flashen →** in the update dialog — the server triggers bootloader mode automatically. For the Headpat device: connect it via USB and double-tap reset.

## Related

- [Headpat](https://github.com/LucyWolf/Headpat) — Headpat device firmware
- [dongel_NRF](https://github.com/LucyWolf/dongel_NRF) — Dongle firmware
