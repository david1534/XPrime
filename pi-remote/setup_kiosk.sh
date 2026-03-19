#!/usr/bin/env bash
# setup_kiosk.sh — Configure Raspberry Pi 4 for Chromium kiosk mode
# Called by install.sh or can be run standalone.
set -euo pipefail

echo "=== Kiosk Setup ==="

# --- Ensure X11 display server (not Wayland) ---
echo "[1/6] Setting display server to X11..."
if command -v raspi-config &>/dev/null; then
    sudo raspi-config nonint do_wayland W1 2>/dev/null || true
else
    echo "  raspi-config not found — skipping display server config."
    echo "  Ensure X11 is selected manually in raspi-config."
fi

# --- Enable auto-login ---
echo "[2/6] Enabling auto-login to desktop..."
if command -v raspi-config &>/dev/null; then
    sudo raspi-config nonint do_boot_behaviour B4 2>/dev/null || true
else
    echo "  raspi-config not found — skipping auto-login config."
fi

# --- Disable screen blanking ---
echo "[3/6] Disabling screen blanking..."
if command -v raspi-config &>/dev/null; then
    sudo raspi-config nonint do_blanking 1 2>/dev/null || true
fi
# Also disable DPMS via X11 config
DPMS_CONF="/etc/X11/xorg.conf.d/10-no-dpms.conf"
sudo mkdir -p /etc/X11/xorg.conf.d
if [ ! -f "$DPMS_CONF" ]; then
    sudo tee "$DPMS_CONF" > /dev/null <<'XCONF'
Section "ServerFlags"
    Option "BlankTime" "0"
    Option "StandbyTime" "0"
    Option "SuspendTime" "0"
    Option "OffTime" "0"
EndSection

Section "ServerLayout"
    Option "BlankTime" "0"
    Option "StandbyTime" "0"
    Option "SuspendTime" "0"
    Option "OffTime" "0"
EndSection
XCONF
    echo "  Created $DPMS_CONF"
fi

# --- GPU memory allocation ---
echo "[4/6] Setting GPU memory to 256MB..."
CONFIG_FILE=""
if [ -f /boot/firmware/config.txt ]; then
    CONFIG_FILE="/boot/firmware/config.txt"
elif [ -f /boot/config.txt ]; then
    CONFIG_FILE="/boot/config.txt"
fi
if [ -n "$CONFIG_FILE" ]; then
    if grep -q "^gpu_mem=" "$CONFIG_FILE"; then
        sudo sed -i 's/^gpu_mem=.*/gpu_mem=256/' "$CONFIG_FILE"
    else
        echo "gpu_mem=256" | sudo tee -a "$CONFIG_FILE" > /dev/null
    fi
    echo "  Updated $CONFIG_FILE"
fi

# --- HDMI audio output ---
echo "[5/6] Configuring HDMI audio output..."
if command -v raspi-config &>/dev/null; then
    # Force audio to HDMI (option 2)
    sudo raspi-config nonint do_audio 2 2>/dev/null || true
fi

# --- Install kiosk systemd service ---
echo "[6/6] Installing kiosk systemd service..."
PI_USER="${SUDO_USER:-$(whoami)}"
# The service template uses %I for the username
sudo cp /opt/pi-remote/kiosk.service /etc/systemd/system/kiosk@.service
sudo systemctl daemon-reload
sudo systemctl enable "kiosk@${PI_USER}.service"
echo "  Enabled kiosk@${PI_USER}.service"

# --- Nightly restart cron job (4 AM) ---
CRON_CMD="0 4 * * * systemctl restart kiosk@${PI_USER}.service"
if ! (sudo crontab -l 2>/dev/null | grep -qF "kiosk@${PI_USER}"); then
    (sudo crontab -l 2>/dev/null; echo "$CRON_CMD") | sudo crontab -
    echo "  Added nightly Chromium restart cron job (4 AM)"
else
    echo "  Nightly restart cron already exists"
fi

echo "=== Kiosk setup complete ==="
