#!/usr/bin/env bash
# install.sh — Master installer for Pi Remote Streaming Kiosk
# Run on a fresh Raspberry Pi OS (Desktop) install.
# Usage: sudo bash install.sh
set -euo pipefail

INSTALL_DIR="/opt/pi-remote"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- Preflight checks ---
echo "============================================"
echo "  Pi Remote Streaming Kiosk — Installer"
echo "============================================"
echo ""

# Must run as root
if [ "$(id -u)" -ne 0 ]; then
    echo "Error: This script must be run with sudo."
    echo "Usage: sudo bash install.sh"
    exit 1
fi

# Check for Raspberry Pi
if [ ! -f /proc/device-tree/model ]; then
    echo "Warning: Cannot detect Raspberry Pi hardware."
    echo "This script is designed for Raspberry Pi OS."
    read -rp "Continue anyway? [y/N] " choice
    [[ "$choice" =~ ^[Yy]$ ]] || exit 1
else
    MODEL=$(tr -d '\0' < /proc/device-tree/model)
    echo "Detected: $MODEL"
fi

PI_USER="${SUDO_USER:-pi}"
echo "Installing for user: $PI_USER"
echo ""

# --- Step 1: Install packages ---
echo "[1/7] Installing required packages..."
apt-get update -qq
apt-get install -y -qq \
    xdotool \
    unclutter \
    python3-websockets \
    chromium \
    avahi-daemon \
    > /dev/null

# Ensure python3-websockets is available (fallback to pip if apt version missing)
if ! python3 -c "import websockets" 2>/dev/null; then
    echo "  Installing websockets via pip..."
    pip3 install --break-system-packages websockets 2>/dev/null \
        || pip3 install websockets
fi

echo "  Packages installed."

# --- Step 2: Copy project files ---
echo "[2/7] Copying project files to $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"
cp "$SCRIPT_DIR/remote_server.py" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/remote.html" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/kiosk.service" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/remote.service" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/setup_kiosk.sh" "$INSTALL_DIR/"
chmod +x "$INSTALL_DIR/setup_kiosk.sh"
chmod +x "$INSTALL_DIR/remote_server.py"
echo "  Files copied."

# --- Step 3: Install remote control service ---
echo "[3/7] Installing remote control server service..."
cp "$INSTALL_DIR/remote.service" /etc/systemd/system/remote.service
systemctl daemon-reload
systemctl enable remote.service
systemctl restart remote.service || true
echo "  remote.service enabled and started."

# --- Step 4: Run kiosk setup ---
echo "[4/7] Running kiosk setup..."
bash "$INSTALL_DIR/setup_kiosk.sh"

# --- Step 5: Enable SSH ---
echo "[5/7] Ensuring SSH is enabled..."
if command -v raspi-config &>/dev/null; then
    raspi-config nonint do_ssh 0 2>/dev/null || true
fi
systemctl enable ssh 2>/dev/null || true
echo "  SSH enabled."

# --- Step 6: Configure unclutter (hide mouse cursor) ---
echo "[6/7] Configuring mouse cursor auto-hide..."
UNCLUTTER_AUTOSTART="/etc/xdg/autostart/unclutter.desktop"
cat > "$UNCLUTTER_AUTOSTART" <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=Unclutter
Exec=unclutter -idle 3 -root
NoDisplay=true
DESKTOP
echo "  Unclutter will hide cursor after 3 seconds of inactivity."

# --- Step 7: Check Widevine DRM ---
echo "[7/7] Checking Widevine DRM support..."
WIDEVINE_LIB=$(find /usr/lib/chromium-browser /usr/lib/chromium /opt/chromium.org 2>/dev/null \
    -name "libwidevinecdm.so" -print -quit || true)
if [ -n "$WIDEVINE_LIB" ]; then
    echo "  Widevine found: $WIDEVINE_LIB"
else
    echo "  Warning: Widevine DRM library not found."
    echo "  Some protected content on X Prime may not play."
    echo "  If needed, install a 32-bit Chromium build or check:"
    echo "    chrome://components → WidevineCdm"
fi

# --- Summary ---
echo ""
echo "============================================"
echo "  Installation Complete!"
echo "============================================"
echo ""

# Get Pi's IP address
PI_IP=$(hostname -I | awk '{print $1}')
PI_HOSTNAME=$(hostname)

echo "  Remote control URL:"
echo "    http://${PI_IP}:8080"
echo "    http://${PI_HOSTNAME}.local:8080"
echo ""
echo "  On your iPhone:"
echo "    1. Open Safari"
echo "    2. Go to http://${PI_IP}:8080"
echo "    3. Tap Share > Add to Home Screen"
echo "    4. Use it like a TV remote!"
echo ""
echo "  Services installed:"
echo "    - kiosk@${PI_USER}.service  (Chromium kiosk browser)"
echo "    - remote.service            (Remote control server)"
echo ""
echo "  To apply all changes, please reboot:"
echo "    sudo reboot"
echo ""
echo "============================================"
