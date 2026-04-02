#!/usr/bin/env python3
"""
Pi Remote Control Server
WebSocket + HTTP server that serves the remote UI and translates
button commands into xdotool keyboard/mouse events for Chromium.
"""

import asyncio
import http
import json
import logging
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

try:
    import websockets
    from websockets.server import serve
    from websockets.client import connect as ws_connect
except ImportError:
    print("Error: 'websockets' package not found. Install with:")
    print("  sudo apt install python3-websockets")
    print("  or: pip3 install websockets")
    sys.exit(1)

# --- Configuration ---
HOST = "0.0.0.0"
PORT = 8080
CDP_PORT = 9222
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

# --- Navigation key mapping ---
NAV_KEYS = {
    "up":    "Up",
    "down":  "Down",
    "left":  "Left",
    "right": "Right",
}

COMMANDS = {
    "select":     ["xdotool", "key", "Return"],
    "back":       ["xdotool", "key", "alt+Left"],
    "playpause":  ["xdotool", "key", "space"],
    "fullscreen": ["xdotool", "key", "f"],
    "tab":        ["xdotool", "key", "Tab"],
    "escape":     ["xdotool", "key", "Escape"],
    "reload":     ["xdotool", "key", "F5"],
    "mouse_click":["xdotool", "click", "1"],
}

SCROLL_REPEAT = 3


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


def get_cdp_ws_url() -> str | None:
    """Get the Chrome DevTools Protocol WebSocket URL for the active page."""
    try:
        with urllib.request.urlopen(f"http://localhost:{CDP_PORT}/json", timeout=2) as r:
            tabs = json.loads(r.read())
        for tab in tabs:
            if tab.get("type") == "page":
                return tab.get("webSocketDebuggerUrl")
    except Exception:
        pass
    return None


async def move_mouse_to_focused_element() -> None:
    """Use CDP to find the focused element and move the mouse cursor to it."""
    ws_url = get_cdp_ws_url()
    if not ws_url:
        return
    try:
        async with ws_connect(ws_url, open_timeout=2) as cdp:
            # Get bounding box of the focused element
            msg_id = 1
            await cdp.send(json.dumps({
                "id": msg_id,
                "method": "Runtime.evaluate",
                "params": {
                    "expression": """
                        (function() {
                            var el = document.activeElement;
                            if (!el || el === document.body) return null;
                            var r = el.getBoundingClientRect();
                            return {
                                x: Math.round(r.left + r.width / 2),
                                y: Math.round(r.top + r.height / 2)
                            };
                        })()
                    """,
                    "returnByValue": True,
                }
            }))
            response = json.loads(await asyncio.wait_for(cdp.recv(), timeout=2))
            result = response.get("result", {}).get("result", {})
            pos = result.get("value")
            if pos and pos.get("x") and pos.get("y"):
                run_xdotool(["xdotool", "mousemove", str(pos["x"]), str(pos["y"])])
    except Exception as e:
        log.debug("CDP mouse move failed: %s", e)


async def handle_action_async(data: dict) -> None:
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
    elif action in NAV_KEYS:
        run_xdotool(["xdotool", "key", NAV_KEYS[action]])
        await asyncio.sleep(0.1)
        await move_mouse_to_focused_element()
    elif action in COMMANDS:
        run_xdotool(COMMANDS[action])
    else:
        log.warning("Unknown action: %s", action)


async def process_request(path, request_headers):
    """Serve remote.html for regular HTTP requests (legacy websockets API)."""
    if path == "/ws":
        return None  # allow WebSocket upgrade

    if REMOTE_HTML.exists():
        body = REMOTE_HTML.read_bytes()
        headers = [
            ("Content-Type", "text/html; charset=utf-8"),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-cache"),
        ]
        return http.HTTPStatus.OK, headers, body
    else:
        body = b"remote.html not found"
        headers = [
            ("Content-Type", "text/plain"),
            ("Content-Length", str(len(body))),
        ]
        return http.HTTPStatus.NOT_FOUND, headers, body


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
                await handle_action_async(data)
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
