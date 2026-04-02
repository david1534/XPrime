#!/usr/bin/env python3
"""
Pi Remote Control Server
WebSocket + HTTP server that serves the remote UI and translates
button commands into xdotool keyboard/mouse events for Chromium.

D-pad navigation is card-aware: it skips small sub-buttons (play/add/info)
and only lands on full movie card elements, then moves the mouse cursor to
the card center to trigger hover effects.
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
    print("Error: 'websockets' package not found.")
    sys.exit(1)

# --- Configuration ---
HOST = "0.0.0.0"
PORT = 8080
CDP_PORT = 9222
DISPLAY = os.environ.get("DISPLAY", ":0")
REMOTE_HTML = Path(__file__).parent / "remote.html"
LOG_FILE = Path(__file__).parent / "remote_server.log"

# Elements smaller than this (in either dimension) are considered sub-buttons,
# not movie cards. Adjust if needed.
CARD_MIN_SIZE = 80

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("pi-remote")

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
    env = os.environ.copy()
    env["DISPLAY"] = DISPLAY
    try:
        subprocess.run(cmd, env=env, check=True, timeout=5,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        log.error("xdotool error: %s — %s", cmd, e.stderr.decode().strip())
    except FileNotFoundError:
        log.error("xdotool not found.")


def get_cdp_ws_url() -> str | None:
    try:
        with urllib.request.urlopen(f"http://localhost:{CDP_PORT}/json", timeout=2) as r:
            tabs = json.loads(r.read())
        for tab in tabs:
            if tab.get("type") == "page":
                return tab.get("webSocketDebuggerUrl")
    except Exception:
        pass
    return None


# JS that returns the focused element's bounding box and whether it's card-sized
FOCUSED_ELEMENT_JS = """
(function() {
    var el = document.activeElement;
    if (!el || el === document.body || el === document.documentElement) return null;
    var r = el.getBoundingClientRect();
    return {
        x: Math.round(r.left + r.width / 2),
        y: Math.round(r.top + r.height / 2),
        w: Math.round(r.width),
        h: Math.round(r.height)
    };
})()
"""

# JS that clicks the primary action (play button) on the currently hovered card.
# Tries to find a play button within the focused/hovered element's parent card,
# falls back to clicking the element itself.
CLICK_PRIMARY_ACTION_JS = """
(function() {
    var el = document.activeElement;
    if (!el || el === document.body) return 'no_focus';
    // Walk up to find the card container
    var card = el;
    for (var i = 0; i < 5; i++) {
        if (!card.parentElement) break;
        var r = card.getBoundingClientRect();
        if (r.width > %d && r.height > %d) break;
        card = card.parentElement;
    }
    // Find the first/primary button or link inside the card
    var btn = card.querySelector('a, button, [role="button"]');
    if (btn) { btn.click(); return 'clicked_primary'; }
    el.click();
    return 'clicked_self';
})()
""" % (CARD_MIN_SIZE, CARD_MIN_SIZE)


async def cdp_navigate(key: str) -> None:
    """
    Press a navigation key, then check if we landed on a card-sized element.
    If we landed on a small sub-button, keep pressing the same key until we
    reach a card, or until we've tried MAX_SKIPS times.
    Then move the mouse to the card center to trigger hover.
    """
    MAX_SKIPS = 8
    ws_url = get_cdp_ws_url()

    run_xdotool(["xdotool", "key", key])

    if not ws_url:
        return

    try:
        async with ws_connect(ws_url, open_timeout=2) as cdp:
            for attempt in range(MAX_SKIPS):
                await asyncio.sleep(0.08)

                await cdp.send(json.dumps({
                    "id": 1,
                    "method": "Runtime.evaluate",
                    "params": {"expression": FOCUSED_ELEMENT_JS, "returnByValue": True}
                }))
                resp = json.loads(await asyncio.wait_for(cdp.recv(), timeout=2))
                pos = resp.get("result", {}).get("result", {}).get("value")

                if not pos:
                    break

                w, h = pos.get("w", 0), pos.get("h", 0)
                is_card = w >= CARD_MIN_SIZE and h >= CARD_MIN_SIZE

                if is_card:
                    # Move mouse to card center to trigger hover
                    run_xdotool(["xdotool", "mousemove", str(pos["x"]), str(pos["y"])])
                    log.debug("Landed on card (%dx%d) at %d,%d after %d skip(s)",
                              w, h, pos["x"], pos["y"], attempt)
                    break
                else:
                    # Sub-button — skip past it
                    log.debug("Skipping sub-button (%dx%d), pressing %s again", w, h, key)
                    run_xdotool(["xdotool", "key", key])

    except Exception as e:
        log.debug("CDP navigate failed: %s", e)


async def handle_action_async(data: dict) -> None:
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
        await cdp_navigate(NAV_KEYS[action])
    elif action == "select":
        # Click the primary action on the hovered card
        ws_url = get_cdp_ws_url()
        if ws_url:
            try:
                async with ws_connect(ws_url, open_timeout=2) as cdp:
                    await cdp.send(json.dumps({
                        "id": 1,
                        "method": "Runtime.evaluate",
                        "params": {"expression": CLICK_PRIMARY_ACTION_JS, "returnByValue": True}
                    }))
                    await asyncio.wait_for(cdp.recv(), timeout=2)
                    return
            except Exception as e:
                log.debug("CDP select failed: %s — falling back to Return key", e)
        run_xdotool(["xdotool", "key", "Return"])
    elif action in COMMANDS:
        run_xdotool(COMMANDS[action])
    else:
        log.warning("Unknown action: %s", action)


async def process_request(path, request_headers):
    if path == "/ws":
        return None
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
        headers = [("Content-Type", "text/plain"), ("Content-Length", str(len(body)))]
        return http.HTTPStatus.NOT_FOUND, headers, body


async def ws_handler(websocket):
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


async def main():
    log.info("Starting Pi Remote server on %s:%d", HOST, PORT)
    log.info("Remote UI: http://<pi-ip>:%d/", PORT)
    log.info("WebSocket endpoint: ws://<pi-ip>:%d/ws", PORT)
    async with serve(ws_handler, HOST, PORT, process_request=process_request):
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Server stopped.")
