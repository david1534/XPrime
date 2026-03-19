# Pi Remote Streaming Kiosk

A Raspberry Pi 4 that acts as a dedicated streaming appliance for X Prime, controlled from your iPhone via a web-based remote.

## Quick Start

### 1. Flash Raspberry Pi OS
- Use [Raspberry Pi Imager](https://www.raspberrypi.com/software/)
- Choose **Raspberry Pi OS with Desktop** (not Lite)
- Enable SSH and configure WiFi in the imager settings

### 2. Boot & Connect
- Insert the SD card, connect HDMI to your TV, power on
- SSH in: `ssh pi@raspberrypi.local` (or use the Pi's IP)

### 3. Install
```bash
git clone <your-repo-url> ~/pi-remote
cd ~/pi-remote
sudo bash install.sh
```

### 4. Reboot
```bash
sudo reboot
```

The Pi will boot directly into X Prime in full-screen Chromium.

### 5. Use the Remote
On your iPhone, open Safari and go to:
```
http://raspberrypi.local:8080
```
Tap **Share > Add to Home Screen** for a native app-like experience.

## Remote Controls

| Button | Action |
|--------|--------|
| D-pad | Navigate content |
| OK | Select / confirm |
| Play/Pause | Toggle video playback |
| Back | Browser back |
| Fullscreen | Toggle video fullscreen |
| Scroll Up/Down | Scroll the page |
| Tab | Move focus to next element |
| Esc | Close overlays / exit fullscreen |
| Reload | Refresh the page |
| Touchpad | Drag to move mouse, tap to click |

## Files

| File | Purpose |
|------|---------|
| `install.sh` | Master installer — run once on fresh Pi OS |
| `setup_kiosk.sh` | Kiosk-specific configuration |
| `remote_server.py` | Python WebSocket + HTTP server |
| `remote.html` | iPhone remote control UI |
| `kiosk.service` | Systemd service for Chromium kiosk |
| `remote.service` | Systemd service for remote control server |

## Troubleshooting

**Remote won't connect:**
```bash
sudo systemctl status remote.service
sudo journalctl -u remote.service -f
```

**Browser not launching:**
```bash
sudo systemctl status kiosk@pi.service
sudo journalctl -u kiosk@pi.service -f
```

**Restart services:**
```bash
sudo systemctl restart remote.service
sudo systemctl restart kiosk@pi.service
```

**View server logs:**
```bash
cat /opt/pi-remote/remote_server.log
```

## Security Note

The remote server has no authentication. Anyone on your WiFi network can control the browser. This is fine for a home network.
