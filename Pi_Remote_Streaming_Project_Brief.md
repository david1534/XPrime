# Project Brief: Raspberry Pi 4 Remote-Controlled Streaming Kiosk

## What This Document Is

This is a complete project specification for a Claude Code session. The goal is to build a system where a Raspberry Pi 4 acts as a dedicated streaming appliance connected to a TV via HDMI, controlled remotely from an iPhone through a custom web-based remote interface. Claude should handle all planning, scripting, and file creation. Only ask the human to intervene for physical actions (plugging in cables, flashing SD cards, running installers on the Pi) or decisions that require their input.

---

## Project Overview

### The Problem
The user watches movies on a browser-based streaming website called "X Prime" (xprime.tv / xprime.stream) by connecting a laptop via HDMI to a Roku TV. Every time they need to pause, scroll, or navigate, they have to physically get up and use the laptop. They want a hands-free-from-bed solution.

### The Solution
A Raspberry Pi 4 permanently connected to the Roku TV via HDMI, running X Prime in a full-screen Chromium browser, controlled from an iPhone via a custom web-based remote interface that mimics a TV remote (d-pad, select, play/pause, back, scroll, fullscreen).

### Key Architecture
```
[iPhone Safari] --(WebSocket over WiFi)--> [Python server on Pi 4] --(xdotool/ydotool)--> [Chromium browser on Pi 4] --(HDMI)--> [Roku TV]
```

The iPhone loads a web page served by the Pi that displays large, thumb-friendly remote control buttons. Each button press sends a WebSocket message to a lightweight Python server running on the Pi. The server translates those messages into simulated keyboard/mouse events injected into the Chromium browser using xdotool (for X11) or ydotool (for Wayland). The browser displays X Prime full-screen on the TV via HDMI.

---

## Hardware Context

- **Device:** Raspberry Pi 4 (4GB RAM preferred, 2GB workable)
- **Storage:** MicroSD card (32GB, A2 rated preferred)
- **OS:** Raspberry Pi OS (latest stable — Bookworm or Trixie)
- **Display server:** Use X11 (not Wayland) — the Pi 4 has better browser video acceleration and DRM/Widevine support under X11
- **Power supply:** USB-C, 15W (5V/3A) minimum
- **TV:** Roku TV connected via micro-HDMI to HDMI cable
- **Control device:** iPhone (any modern iOS version with Safari)
- **Network:** Both Pi and iPhone on the same local WiFi network

### Why Pi 4 over Pi 5
The Pi 4 has hardware H.264 decoding (the Pi 5 removed this). Since browser-based streaming sites typically serve H.264 video, the Pi 4 can offload decoding to the GPU, keeping CPU usage low during playback. The Pi 5 is faster for general browsing but handles video decode in software only (except H.265). For a kiosk that spends 95% of its time playing video, the Pi 4's hardware decode is the better fit.

### HDMI-CEC Note
Roku TVs have very limited HDMI-CEC support — they don't reliably forward remote button presses to connected HDMI devices even with the hidden developer menu enabled. Do NOT rely on or attempt to configure CEC-based control. The entire control path goes through the iPhone WebSocket remote.

---

## Software Components to Build

### Component 1: Pi Setup & Kiosk Configuration

**Goal:** Configure the Pi 4 to boot directly into a full-screen Chromium browser displaying X Prime, with no desktop environment visible to the user.

**Requirements:**
- Raspberry Pi OS with desktop (needed for Chromium), using X11 display server
- Auto-login enabled (no login screen)
- Chromium launches automatically on boot in kiosk mode (full-screen, no toolbars, no error dialogs)
- Default URL: `https://xprime.tv` (the user can change this later)
- Screen blanking / power management disabled (screen should never go to sleep)
- Mouse cursor hidden after a few seconds of inactivity (use unclutter or similar)
- SSH enabled for remote administration
- WiFi configured and connected to home network
- Audio output routed through HDMI

**Chromium flags to include:**
```
--kiosk
--noerrdialogs
--disable-infobars
--disable-session-crashed-bubble
--disable-translate
--no-first-run
--fast
--fast-start
--disable-features=TranslateUI
--disable-pinch
--overscroll-history-navigation=0
--start-fullscreen
```

**Resilience:**
- If Chromium crashes, it should auto-restart (use a systemd service with Restart=always, or a watchdog script)
- Consider a nightly scheduled restart of Chromium (cron job at e.g., 4 AM) to prevent memory leaks from accumulating over days of continuous use

**Create:**
- A setup shell script (`setup_kiosk.sh`) that the user can run on a fresh Raspberry Pi OS install to configure everything above
- A systemd service file (`kiosk.service`) for Chromium auto-launch and auto-restart
- A cron entry for the nightly Chromium restart

---

### Component 2: WebSocket Remote Control Server

**Goal:** A lightweight server running on the Pi that accepts WebSocket connections from the iPhone and translates button commands into simulated keyboard/mouse input on the Chromium browser.

**Requirements:**
- Python 3 (pre-installed on Raspberry Pi OS)
- Minimal dependencies — use only what's available via apt or pip on Pi OS. Prefer `websockets` (pip) or `asyncio` with a simple WebSocket implementation
- Also serves the static HTML remote control page via HTTP (so the iPhone only needs to hit one address)
- Runs as a systemd service, auto-starts on boot, restarts on crash
- Listens on a single port (e.g., 8080) — HTTP for the remote page, WebSocket upgrade for control messages

**Command mapping (WebSocket message → xdotool action):**

| Remote Button | WebSocket Message | xdotool Command | Purpose |
|---------------|-------------------|-----------------|---------|
| D-pad Up | `{"action": "up"}` | `xdotool key Up` | Navigate up through content |
| D-pad Down | `{"action": "down"}` | `xdotool key Down` | Navigate down through content |
| D-pad Left | `{"action": "left"}` | `xdotool key Left` | Navigate left |
| D-pad Right | `{"action": "right"}` | `xdotool key Right` | Navigate right |
| OK / Select | `{"action": "select"}` | `xdotool key Return` | Select/confirm |
| Back | `{"action": "back"}` | `xdotool key alt+Left` | Browser back |
| Play/Pause | `{"action": "playpause"}` | `xdotool key space` | Toggle play/pause in video player |
| Fullscreen | `{"action": "fullscreen"}` | `xdotool key f` | Toggle fullscreen on video player |
| Scroll Up | `{"action": "scroll_up"}` | `xdotool click 4` (repeat 3x) | Scroll page up |
| Scroll Down | `{"action": "scroll_down"}` | `xdotool click 5` (repeat 3x) | Scroll page down |
| Tab (next element) | `{"action": "tab"}` | `xdotool key Tab` | Move focus to next element |
| Escape | `{"action": "escape"}` | `xdotool key Escape` | Close overlays / exit fullscreen |
| Reload | `{"action": "reload"}` | `xdotool key F5` | Refresh the page |
| Mouse mode - move | `{"action": "mouse_move", "dx": N, "dy": N}` | `xdotool mousemove_relative -- N N` | Relative mouse movement (fallback for when keyboard nav doesn't work) |
| Mouse mode - click | `{"action": "mouse_click"}` | `xdotool click 1` | Left click at current position |

**Important xdotool notes:**
- xdotool works with X11. Since we're using X11 on the Pi 4, this is fine.
- The DISPLAY environment variable must be set to `:0` when running xdotool from a service context: `DISPLAY=:0 xdotool key ...`
- If the user later switches to Wayland, ydotool would be needed instead. For now, target X11 + xdotool only.

**Create:**
- `remote_server.py` — the WebSocket + HTTP server
- `remote.service` — systemd service file
- Server should log connections and commands to a file for debugging

---

### Component 3: iPhone Remote Control Web UI

**Goal:** A single HTML file served by the Pi that displays a TV-remote-style interface optimized for iPhone Safari, with large touch targets and a dark theme.

**Requirements:**
- Single self-contained HTML file (inline CSS + JS, no external dependencies)
- Dark theme (dark gray/black background, subtle button colors) — comfortable for use in a dim room
- Responsive layout that works well on iPhone screen sizes (375px–430px wide)
- PWA-capable: include a web app manifest and meta tags so the user can "Add to Home Screen" and it launches without Safari chrome (no URL bar, full-screen web app feel)
- WebSocket connection to the server (same host, same port, `/ws` endpoint)
- Auto-reconnect if WebSocket connection drops (with visual indicator)

**Button layout (top to bottom):**

```
┌─────────────────────────────────┐
│         X Prime Remote          │  ← Title / connection status
├─────────────────────────────────┤
│                                 │
│            [ ▲ ]                │  ← D-pad: Up
│                                 │
│      [ ◄ ] [OK] [ ► ]          │  ← D-pad: Left, Select, Right
│                                 │
│            [ ▼ ]                │  ← D-pad: Down
│                                 │
├─────────────────────────────────┤
│                                 │
│    [ ⏯ Play/Pause ]            │  ← Wide button
│                                 │
├─────────────────────────────────┤
│                                 │
│  [ ← Back ]    [ ⛶ Fullscreen] │  ← Two buttons side by side
│                                 │
├─────────────────────────────────┤
│                                 │
│  [ ↑ Scroll ]  [ ↓ Scroll ]    │  ← Two buttons side by side
│                                 │
├─────────────────────────────────┤
│                                 │
│  [ Tab ]  [ Esc ]  [ Reload ]   │  ← Three utility buttons
│                                 │
├─────────────────────────────────┤
│                                 │
│  ┌─────────────────────────┐    │
│  │     Mouse Touchpad      │    │  ← Draggable area for relative
│  │     (drag to move,      │    │     mouse movement (fallback)
│  │      tap to click)      │    │
│  └─────────────────────────┘    │
│                                 │
└─────────────────────────────────┘
```

**UI behavior details:**
- All buttons should have `touch-action: manipulation` to prevent double-tap zoom
- Buttons should give immediate visual feedback on press (brief color change / highlight)
- The mouse touchpad area should track finger drag distance and send relative mouse_move messages. A tap on the touchpad sends a mouse_click.
- Connection status indicator: green dot = connected, red dot = disconnected/reconnecting
- Haptic feedback on button press if the browser supports it (`navigator.vibrate(10)`)
- Prevent pull-to-refresh and other Safari gestures that would interfere with remote use (`overscroll-behavior: none`, appropriate meta viewport tags)

**Create:**
- `remote.html` — the complete remote control interface

---

### Component 4: Installation & Deployment Script

**Goal:** A single master script that installs everything and gets the system running.

**Create: `install.sh`**

This script should:
1. Check that it's running on a Raspberry Pi with Raspberry Pi OS
2. Install required packages: `xdotool`, `unclutter`, `python3-pip`, `python3-websockets` (or install websockets via pip with `--break-system-packages`)
3. Copy `remote_server.py` and `remote.html` to an appropriate location (e.g., `/opt/pi-remote/`)
4. Install and enable the `remote.service` systemd service
5. Copy/install the `kiosk.service` systemd service
6. Configure auto-login if not already set (via raspi-config noninteractive)
7. Disable screen blanking
8. Configure HDMI audio output
9. Set up the nightly Chromium restart cron job
10. Print a summary with the Pi's IP address and the URL to open on iPhone
11. Prompt the user to reboot to apply all changes

**The script should be idempotent** — safe to run multiple times without breaking things.

---

## File Manifest

When complete, the project should consist of these files:

```
pi-remote/
├── install.sh              # Master installer (run once on fresh Pi OS)
├── setup_kiosk.sh          # Kiosk-specific configuration (called by install.sh)
├── remote_server.py        # Python WebSocket + HTTP server
├── remote.html             # iPhone remote control UI
├── kiosk.service           # Systemd service for Chromium kiosk
├── remote.service          # Systemd service for remote control server
└── README.md               # Quick-start instructions
```

---

## User Workflow (What the Human Will Do)

1. **Flash Raspberry Pi OS** onto a microSD card using Raspberry Pi Imager (desktop version, not Lite). Enable SSH and configure WiFi during the imaging process.
2. **Boot the Pi**, plug it into the Roku TV via HDMI, and SSH in from their laptop.
3. **Clone or copy the project files** onto the Pi.
4. **Run `install.sh`** — this handles all configuration.
5. **Reboot the Pi** — it should boot directly into full-screen Chromium showing X Prime.
6. **On their iPhone**, open Safari and navigate to `http://<pi-ip>:8080`. Save to home screen for PWA experience.
7. **Use the remote** to navigate X Prime from bed.

The human should only need to interact for: flashing the SD card, initial SSH, running the install script, rebooting, and opening the URL on their iPhone. Everything else should be automated.

---

## Technical Notes & Edge Cases

### Display Server
- **Use X11, not Wayland.** Set this via raspi-config. The Pi 4 has better Chromium video acceleration and DRM support under X11. xdotool only works with X11.
- If the Pi OS version defaults to Wayland (newer Bookworm/Trixie builds may), the install script should switch to X11 via `sudo raspi-config nonint do_wayland W1` (W1 = X11).

### Widevine / DRM
- Some embedded video players on X Prime may require Widevine DRM support. Chromium on Raspberry Pi OS includes Widevine by default on 32-bit builds. If using 64-bit Pi OS, Widevine may need manual installation. The install script should check and handle this.
- The Chromium package `chromium-browser` on Raspberry Pi OS typically bundles the necessary DRM components. Verify by navigating to `chrome://components` and checking for WidevineCdm.

### Ad Settings
- X Prime has a built-in option to disable ads on the site itself. The user will toggle this manually on first use. No ad blocker extension is needed.

### Network Discovery
- The install script should print the Pi's local IP address. Optionally, set up mDNS/Avahi so the iPhone can reach the Pi at `http://raspberrypi.local:8080` (Avahi/mDNS is usually pre-installed on Pi OS).

### Performance Tips
- Allocate at least 128MB to the GPU via `gpu_mem=128` in `/boot/config.txt` (or `/boot/firmware/config.txt` on newer builds). 256MB is better for video-heavy use.
- Use Chromium's `--enable-features=VaapiVideoDecoder` flag if applicable to encourage hardware video decode.
- If the user experiences choppy video, they can try overclocking the Pi 4 mildly (e.g., `arm_freq=1800` in config.txt with adequate cooling).

### Security
- The remote control server has no authentication. This is acceptable for a home network but should be noted. Anyone on the same WiFi can control the browser.
- If the user wants basic security, a simple shared secret / PIN could be added later.

---

## Success Criteria

The project is complete when:
1. The Pi boots directly into X Prime in full-screen Chromium with no visible desktop or toolbars
2. The iPhone can load the remote control page and all buttons work (navigate, select, play/pause, back, fullscreen, scroll)
3. Video playback on X Prime is smooth at 1080p (low dropped frames)
4. The system survives a power cycle (everything auto-starts on boot)
5. The remote control page works as a PWA from the iPhone home screen
6. The mouse touchpad fallback works for situations where keyboard nav doesn't reach a UI element

---

## Out of Scope (Do Not Build)

- Native iOS app (web-based remote is sufficient)
- HDMI-CEC control (Roku TV doesn't support it well)
- VNC / screen mirroring to iPhone (too much overhead, wrong UX)
- Multiple browser tab management
- Authentication / user accounts on the remote
- Automatic content discovery or scraping
