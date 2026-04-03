#!/usr/bin/env python3
"""
Pi Remote Control Server
- HTTP server on port 8080 serves remote.html
- WebSocket server on port 8081 handles button commands
"""

import asyncio
import http
import http.server
import json
import logging
import os
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path

try:
    import websockets
    from websockets.asyncio.server import serve as ws_serve
except ImportError:
    print("Error: 'websockets' package not found.")
    sys.exit(1)

# --- Configuration ---
HTTP_HOST = "0.0.0.0"
HTTP_PORT = 8080
WS_HOST = "0.0.0.0"
WS_PORT = 8081
CDP_PORT = 9222
DISPLAY = os.environ.get("DISPLAY", ":0")
REMOTE_HTML = Path(__file__).parent / "remote.html"
LOG_FILE = Path(__file__).parent / "remote_server.log"

# Elements smaller than this are sub-buttons, not movie cards
CARD_MIN_SIZE = 80

# Must match --force-device-scale-factor in kiosk launch command.
# CDP returns CSS pixels; xdotool needs physical X11 pixels.
DEVICE_SCALE = 3

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
    "playpause":  None,  # handled specially below
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


FOCUSED_ELEMENT_JS = """
(function() {
    var el = document.activeElement;
    if (!el || el === document.body || el === document.documentElement) return null;
    var r = el.getBoundingClientRect();
    return { x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2),
             w: Math.round(r.width), h: Math.round(r.height) };
})()
"""

CLICK_PRIMARY_JS = """
(function() {
    var el = document.activeElement;
    if (!el || el === document.body) return;
    var card = el;
    for (var i = 0; i < 5; i++) {
        if (!card.parentElement) break;
        var r = card.getBoundingClientRect();
        if (r.width > %d && r.height > %d) break;
        card = card.parentElement;
    }
    var btn = card.querySelector('a, button, [role="button"]');
    if (btn) { btn.click(); } else { el.click(); }
})()
""" % (CARD_MIN_SIZE, CARD_MIN_SIZE)


async def cdp_eval(cdp_ws_url: str, expression: str) -> object:
    from websockets.asyncio.client import connect as cdp_connect
    async with cdp_connect(cdp_ws_url, open_timeout=2) as cdp:
        await cdp.send(json.dumps({
            "id": 1, "method": "Runtime.evaluate",
            "params": {"expression": expression, "returnByValue": True}
        }))
        resp = json.loads(await asyncio.wait_for(cdp.recv(), timeout=2))
        return resp.get("result", {}).get("result", {}).get("value")


# --- Persistent CDP connection ---
_cdp_conn = None
_cdp_url = None
_cdp_msg_id = 0


async def cdp_send(expression: str):
    """Send a JS expression over the persistent CDP connection, return value."""
    global _cdp_conn, _cdp_url, _cdp_msg_id
    from websockets.asyncio.client import connect as cdp_connect

    # Only fetch the WS URL when we don't have a live connection
    if _cdp_conn is None:
        ws_url = get_cdp_ws_url()
        if not ws_url:
            return None
        _cdp_conn = await cdp_connect(ws_url, open_timeout=3)
        _cdp_url = ws_url

    _cdp_msg_id += 1
    msg_id = _cdp_msg_id
    try:
        await _cdp_conn.send(json.dumps({
            "id": msg_id, "method": "Runtime.evaluate",
            "params": {"expression": expression, "returnByValue": True}
        }))
        # Drain until we get our response (skip CDP events)
        for _ in range(10):
            raw = await asyncio.wait_for(_cdp_conn.recv(), timeout=3)
            resp = json.loads(raw)
            if resp.get("id") == msg_id:
                return resp.get("result", {}).get("result", {}).get("value")
    except Exception as e:
        log.debug("CDP send failed, will reconnect next call: %s", e)
        _cdp_conn = None
    return None


# --- Mouse position tracked in Python (avoids xdotool subprocess per press) ---
_mouse_css = [0, 0]  # [x, y] in CSS pixels


def update_mouse(x: int, y: int) -> None:
    _mouse_css[0] = x
    _mouse_css[1] = y


# --- Card position cache (avoids re-querying DOM on every press) ---
import time as _time
_cards_cache: list | None = None
_cards_time: float = 0.0
CARDS_TTL = 2.0  # seconds — card layout is static, no need to re-query often


GET_CARDS_JS = """
(function(cardMin) {
    if (!document.getElementById('_rm_fast_tx')) {
        var s = document.createElement('style');
        s.id = '_rm_fast_tx';
        s.textContent = '* { transition-duration: 80ms !important; transition-delay: 0s !important; animation-duration: 80ms !important; }';
        document.head.appendChild(s);
    }
    var all = document.querySelectorAll('a, [role="link"], [role="button"], button');
    var cards = [];
    for (var i = 0; i < all.length; i++) {
        var r = all[i].getBoundingClientRect();
        if (r.width >= cardMin && r.height >= cardMin && r.bottom > 0 && r.top < window.innerHeight + 300)
            cards.push({x: Math.round(r.left+r.width/2), y: Math.round(r.top+r.height/2),
                        w: Math.round(r.width), h: Math.round(r.height)});
    }
    return cards;
})(%d)
""" % CARD_MIN_SIZE

CLICK_ARROW_JS = """
(function(mouseX, mouseY, direction, rowTol) {
    var btns = Array.from(document.querySelectorAll('button, [role="button"], a'));
    var arrows = btns.filter(function(el) {
        var r = el.getBoundingClientRect();
        if (r.width < 5 || r.height < 5 || r.width > 120 || r.height > 120) return false;
        if (Math.abs((r.top + r.height/2) - mouseY) > rowTol) return false;
        var text = (el.textContent||'').trim();
        var aria = (el.getAttribute('aria-label')||'').toLowerCase();
        var cls  = (el.className||'').toLowerCase();
        var isArrow = /[›»>❯→▶⟩]/.test(text)||/[‹«<❮←◀⟨]/.test(text)||
                      aria.includes('next')||aria.includes('prev')||
                      cls.includes('arrow')||cls.includes('chevron')||
                      cls.includes('next')||cls.includes('prev')||cls.includes('slider');
        if (!isArrow) return false;
        var cx = r.left + r.width/2;
        return direction === 'Right' ? cx > mouseX : cx < mouseX;
    });
    if (!arrows.length) return false;
    arrows.sort(function(a,b){
        var ax=a.getBoundingClientRect().left, bx=b.getBoundingClientRect().left;
        return direction==='Right' ? ax-bx : bx-ax;
    });
    arrows[0].click();
    return true;
})(%d, %d, '%s', %d)
"""


def _nav_find_target(cards: list, cur_x: int, cur_y: int, key: str):
    """Pure Python navigation logic. Returns ('move', card) | ('scroll', dy) | ('arrow', None) | ('none', None)."""
    ROW_TOL = 40
    if key in ("Up", "Down"):
        if key == "Up":
            cands = [c for c in cards if c["y"] < cur_y - ROW_TOL]
            if not cands: return "scroll", -400
            row_y = max(c["y"] for c in cands)
        else:
            cands = [c for c in cards if c["y"] > cur_y + ROW_TOL]
            if not cands: return "scroll", 400
            row_y = min(c["y"] for c in cands)
        row = [c for c in cards if abs(c["y"] - row_y) <= ROW_TOL]
        return "move", min(row, key=lambda c: abs(c["x"] - cur_x))
    else:
        row_cards = [c for c in cards if abs(c["y"] - cur_y) <= ROW_TOL] or cards
        if key == "Left":
            cands = [c for c in row_cards if c["x"] < cur_x - c["w"] // 3]
            t = max(cands, key=lambda c: c["x"]) if cands else None
        else:
            cands = [c for c in row_cards if c["x"] > cur_x + c["w"] // 3]
            t = min(cands, key=lambda c: c["x"]) if cands else None
        if t: return "move", t
        return "arrow", None


async def cdp_navigate(key: str) -> None:
    global _cards_cache, _cards_time
    ROW_TOL = 40

    cur_x, cur_y = _mouse_css[0], _mouse_css[1]

    # Refresh card cache only when stale
    now = _time.monotonic()
    if _cards_cache is None or (now - _cards_time) > CARDS_TTL:
        try:
            _cards_cache = await cdp_send(GET_CARDS_JS)
            _cards_time = _time.monotonic()
        except Exception as e:
            log.debug("CDP get cards failed: %s", e)
            run_xdotool(["xdotool", "key", key])
            return

    if not _cards_cache:
        return

    action, payload = _nav_find_target(_cards_cache, cur_x, cur_y, key)

    if action == "move":
        nx, ny = payload["x"], payload["y"]
        run_xdotool(["xdotool", "mousemove",
                     str(nx * DEVICE_SCALE), str(ny * DEVICE_SCALE)])
        update_mouse(nx, ny)
        log.debug("%s → card at css(%d,%d)", key, nx, ny)
    elif action == "scroll":
        await cdp_send(f"window.scrollBy(0, {payload})")
    elif action == "arrow":
        js = CLICK_ARROW_JS % (cur_x, cur_y, key, ROW_TOL * 3)
        await cdp_send(js)


def focus_chromium() -> None:
    env = os.environ.copy()
    env["DISPLAY"] = DISPLAY
    try:
        win_id = subprocess.check_output(
            ["xdotool", "search", "--onlyvisible", "--class", "chromium"],
            env=env, timeout=3).decode().strip().split()[0]
        run_xdotool(["xdotool", "windowfocus", win_id])
    except Exception:
        pass


async def handle_action(data: dict) -> None:
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
        update_mouse(_mouse_css[0] + dx // DEVICE_SCALE, _mouse_css[1] + dy // DEVICE_SCALE)
    elif action in NAV_KEYS:
        await cdp_navigate(NAV_KEYS[action])
    elif action == "select":
        # Click at current mouse position (navigation is mouse-based)
        run_xdotool(["xdotool", "click", "1"])
    elif action == "playpause":
        focus_chromium()
        run_xdotool(["xdotool", "key", "space"])
    elif action == "back":
        try:
            await cdp_send("window.history.back()")
        except Exception:
            focus_chromium()
            run_xdotool(["xdotool", "key", "alt+Left"])
    elif action == "type":
        text = data.get("text", "")
        if text:
            focus_chromium()
            run_xdotool(["xdotool", "type", "--clearmodifiers", "--", text])
    elif action == "restart_browser":
        log.info("Restarting Chromium...")
        subprocess.run(["pkill", "-u", "david1534", "chromium"],
                       capture_output=True, timeout=5)
        import time; time.sleep(3)
        chromium_cmd = (
            "DISPLAY=:0 XAUTHORITY=/home/david1534/.Xauthority "
            "HOME=/home/david1534 "
            "/usr/bin/chromium "
            "--kiosk --noerrdialogs --disable-infobars "
            "--disable-session-crashed-bubble --disable-translate "
            "--no-first-run --fast --fast-start "
            "--disable-features=TranslateUI --disable-pinch "
            "--overscroll-history-navigation=0 --start-fullscreen "
            "--enable-features=VaapiVideoDecoder "
            "--check-for-update-interval=31536000 "
            "--password-store=basic "
            "--load-extension=/home/david1534/extensions/ublock/uBlock0.chromium "
            "--enable-spatial-navigation "
            f"--remote-debugging-port={CDP_PORT} "
            f"--force-device-scale-factor={DEVICE_SCALE} "
            "https://xprime.tv"
        )
        subprocess.Popen(
            ["sudo", "-u", "david1534", "bash", "-c", chromium_cmd],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    elif action in COMMANDS:
        run_xdotool(COMMANDS[action])
    else:
        log.warning("Unknown action: %s", action)


# --- WebSocket handler ---

async def ws_handler(websocket):
    remote = websocket.remote_address
    log.info("Client connected: %s:%s", remote[0], remote[1])
    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                log.info("Action from %s: %s", remote[0], data.get("action", "?"))
                await handle_action(data)
            except json.JSONDecodeError:
                log.warning("Invalid JSON: %s", message)
    except Exception:
        pass
    finally:
        log.info("Client disconnected: %s:%s", remote[0], remote[1])


# --- HTTP server (serves remote.html, injects correct WS port) ---

class RemoteHTTPHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if not REMOTE_HTML.exists():
            self.send_error(404)
            return
        body = REMOTE_HTML.read_bytes()
        # Patch the WebSocket URL to use port 8081
        body = body.replace(
            b"${proto}//${location.host}/ws",
            b"${proto}//${location.hostname}:%d/ws" % WS_PORT
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # suppress HTTP access logs


def run_http_server():
    server = http.server.HTTPServer((HTTP_HOST, HTTP_PORT), RemoteHTTPHandler)
    log.info("HTTP server on %s:%d", HTTP_HOST, HTTP_PORT)
    server.serve_forever()


# --- Main ---

async def main():
    log.info("Starting Pi Remote — HTTP:%d  WebSocket:%d", HTTP_PORT, WS_PORT)

    # HTTP server in background thread
    t = threading.Thread(target=run_http_server, daemon=True)
    t.start()

    # WebSocket server
    async with ws_serve(ws_handler, WS_HOST, WS_PORT):
        log.info("WebSocket server on %s:%d", WS_HOST, WS_PORT)
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Server stopped.")
