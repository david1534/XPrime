#!/usr/bin/env python3
"""
Pi Remote Control Server
WebSocket + HTTP server that serves the remote UI and translates
button commands into xdotool keyboard/mouse events for Chromium.
"""

import asyncio
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

try:
    import websockets
    from websockets.server import serve
except ImportError:
    print("Error: 'websockets' package not found. Install with:")
    print("  sudo apt install python3-websockets")
    print("  or: pip3 install websockets")
    sys.exit(1)

# --- Configuration ---
HOST = "0.0.0.0"
PORT = 8080
DISPLAY = os.environ.get("DISPLAY", ":0")
REMOTE_HTML = Path(__file__).parent / "remote.html"
LOG_FILE = Path(__file__).parent / "remote_server.log"

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("pi-remote")

# --- Command mapping ---
COMMANDS = {
    "up":         ["xdotool", "key", "Up"],
    "down":       ["xdotool", "key", "Down"],
    "left":       ["xdotool", "key", "Left"],
    "right":      ["xdotool", "key", "Right"],
    "select":     ["xdotool", "key", "Return"],
    "back":       ["xdotool", "key", "alt+Left"],
    "playpause":  ["xdotool", "key", "space"],
    "fullscreen": ["xdotool", "key", "f"],
    "tab":        ["xdotool", "key", "Tab"],
    "escape":     ["xdotool", "key", "Escape"],
    "reload":     ["xdotool", "key", "F5"],
    "mouse_click":["xdotool", "click", "1"],
}

SCROLL_REPEAT = 3  # number of scroll ticks per button press


def run_xdotool(cmd: list[str]) -> None:
    """Execute an xdotool command with the correct DISPLAY."""
    env = os.environ.copy()
    env["DISPLAY"] = DISPLAY
    try:
        subprocess.run(cmd, env=env, check=True, timeout=5,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        log.error("xdotool error: %s — %s", cmd, e.stderr.decode().strip())
    except FileNotFoundError:
        log.error("xdotool not found. Install with: sudo apt install xdotool")


def handle_action(data: dict) -> None:
    """Dispatch a single action from the remote."""
    action = data.get("action")
    if not action:
        return

    if action == "scroll_up":
        for _ in range(SCROLL_REPEAT):
            run_xdotool(["xdotool", "click", "4"])
    elif action == "scroll_down":
        for _ in range(SCROLL_REPEAT):
            run_xdotool(["xdotool", "click", "5"])
    elif action == "mouse_move":
        dx = int(data.get("dx", 0))
        dy = int(data.get("dy", 0))
        run_xdotool(["xdotool", "mousemove_relative", "--", str(dx), str(dy)])
    elif action in COMMANDS:
        run_xdotool(COMMANDS[action])
    else:
        log.warning("Unknown action: %s", action)


# --- HTTP handler (serves remote.html) ---

async def http_handler(path, request_headers):
    """Intercept HTTP requests and serve the remote UI for non-WebSocket paths."""
    # This is called by websockets library before the WebSocket handshake.
    # Return an HTTP response tuple to serve static content,
    # or None to proceed with WebSocket upgrade.
    pass  # handled below in process_request


async def process_request(connection, request):
    """Serve remote.html for regular HTTP requests."""
    if request.path == "/ws":
        return  # allow WebSocket upgrade

    # Serve the HTML file for any other path
    if REMOTE_HTML.exists():
        body = REMOTE_HTML.read_bytes()
        return connection.respond(200, "OK", websockets.Headers({
            "Content-Type": "text/html; charset=utf-8",
            "Content-Length": str(len(body)),
            "Cache-Control": "no-cache",
        }), body)
    else:
        body = b"remote.html not found"
        return connection.respond(404, "Not Found", websockets.Headers({
            "Content-Type": "text/plain",
            "Content-Length": str(len(body)),
        }), body)


# --- WebSocket handler ---

async def ws_handler(websocket):
    """Handle a WebSocket connection from the remote UI."""
    remote = websocket.remote_address
    log.info("Client connected: %s:%s", remote[0], remote[1])
    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                action = data.get("action", "?")
                log.info("Action from %s: %s", remote[0], action)
                handle_action(data)
            except json.JSONDecodeError:
                log.warning("Invalid JSON from %s: %s", remote[0], message)
    except websockets.ConnectionClosed:
        pass
    finally:
        log.info("Client disconnected: %s:%s", remote[0], remote[1])


# --- Main ---

async def main():
    log.info("Starting Pi Remote server on %s:%d", HOST, PORT)
    log.info("Remote UI: http://<pi-ip>:%d/", PORT)
    log.info("WebSocket endpoint: ws://<pi-ip>:%d/ws", PORT)

    async with serve(
        ws_handler,
        HOST,
        PORT,
        process_request=process_request,
    ):
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Server stopped.")
