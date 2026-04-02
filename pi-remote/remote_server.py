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


GET_ALL_CARDS_JS = """
(function() {
    var all = document.querySelectorAll('a, [role="link"], [role="button"], button');
    var cards = [];
    for (var i = 0; i < all.length; i++) {
        var r = all[i].getBoundingClientRect();
        if (r.width >= %d && r.height >= %d && r.bottom > 0 && r.top < window.innerHeight + 300) {
            cards.push({x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2),
                        w: Math.round(r.width), h: Math.round(r.height)});
        }
    }
    return cards;
})()
""" % (CARD_MIN_SIZE, CARD_MIN_SIZE)


def get_mouse_css_pos() -> tuple[int, int]:
    """Get the real current mouse position in CSS pixels (physical ÷ DEVICE_SCALE)."""
    env = os.environ.copy()
    env["DISPLAY"] = DISPLAY
    try:
        out = subprocess.check_output(
            ["xdotool", "getmouselocation", "--shell"], env=env, timeout=2
        ).decode()
        x = int(next(l for l in out.splitlines() if l.startswith("X=")).split("=")[1])
        y = int(next(l for l in out.splitlines() if l.startswith("Y=")).split("=")[1])
        return x // DEVICE_SCALE, y // DEVICE_SCALE
    except Exception:
        return 0, 0


FAST_TRANSITIONS_JS = """
(function() {
    if (document.getElementById('_rm_fast_tx')) return;
    var s = document.createElement('style');
    s.id = '_rm_fast_tx';
    s.textContent = '* { transition-duration: 80ms !important; transition-delay: 0s !important; animation-duration: 80ms !important; }';
    document.head.appendChild(s);
})()
"""


async def cdp_navigate(key: str) -> None:
    ROW_TOLERANCE = 40
    ws_url = get_cdp_ws_url()

    if not ws_url:
        run_xdotool(["xdotool", "key", key])
        return

    try:
        from websockets.asyncio.client import connect as cdp_connect
        async with cdp_connect(ws_url, open_timeout=2) as cdp:

            async def eval_js(expr, msg_id=1):
                await cdp.send(json.dumps({"id": msg_id, "method": "Runtime.evaluate",
                                           "params": {"expression": expr, "returnByValue": True}}))
                r = json.loads(await asyncio.wait_for(cdp.recv(), timeout=2))
                return r.get("result", {}).get("result", {}).get("value")

            # Inject fast transitions (no-op if already present)
            await eval_js(FAST_TRANSITIONS_JS, 1)

            # Always use the real mouse position — not JS-tracked state which can drift
            cur_x, cur_y = get_mouse_css_pos()

            cards = await eval_js(GET_ALL_CARDS_JS, 2)
            if not cards:
                return

            if key in ("Up", "Down"):
                if key == "Up":
                    candidates = [c for c in cards if c["y"] < cur_y - ROW_TOLERANCE]
                    target_row_y = max((c["y"] for c in candidates), default=None)
                else:
                    candidates = [c for c in cards if c["y"] > cur_y + ROW_TOLERANCE]
                    target_row_y = min((c["y"] for c in candidates), default=None)

                if target_row_y is None:
                    scroll_dir = -400 if key == "Up" else 400
                    await eval_js(f"window.scrollBy(0, {scroll_dir})", 3)
                    return

                row_cards = [c for c in cards if abs(c["y"] - target_row_y) <= ROW_TOLERANCE]
                target = min(row_cards, key=lambda c: abs(c["x"] - cur_x))

            else:
                # Same row = within ROW_TOLERANCE pixels vertically
                row_cards = [c for c in cards if abs(c["y"] - cur_y) <= ROW_TOLERANCE]
                if not row_cards:
                    row_cards = cards  # fallback

                if key == "Left":
                    candidates = [c for c in row_cards if c["x"] < cur_x - c["w"] // 3]
                    target = max(candidates, key=lambda c: c["x"]) if candidates else None
                else:
                    candidates = [c for c in row_cards if c["x"] > cur_x + c["w"] // 3]
                    target = min(candidates, key=lambda c: c["x"]) if candidates else None

                # No more cards in this direction — find and click the row's arrow button
                if target is None:
                    arrow_js = """
(function(curY, direction, rowTol) {
    var btns = Array.from(document.querySelectorAll('button, [role="button"], a'));
    var candidates = btns.filter(function(el) {
        var r = el.getBoundingClientRect();
        if (r.width < 5 || r.height < 5) return false;
        if (r.width > 120 || r.height > 120) return false;
        var elCy = r.top + r.height / 2;
        if (Math.abs(elCy - curY) > rowTol) return false;
        var text = (el.textContent || '').trim();
        var aria = (el.getAttribute('aria-label') || '').toLowerCase();
        var cls = (el.className || '').toLowerCase();
        var isArrow = /[›»>❯→▶⟩]/.test(text) || /[‹«<❮←◀⟨]/.test(text) ||
                      aria.includes('next') || aria.includes('prev') ||
                      aria.includes('forward') || aria.includes('back') ||
                      cls.includes('arrow') || cls.includes('chevron') ||
                      cls.includes('next') || cls.includes('prev') ||
                      cls.includes('slider') || cls.includes('scroll');
        if (!isArrow) return false;
        var cx = r.left + r.width / 2;
        return direction === 'Right' ? cx > curY : cx < curY;
    });
    if (!candidates.length) return null;
    candidates.sort(function(a, b) {
        var ar = a.getBoundingClientRect(), br = b.getBoundingClientRect();
        var ax = ar.left + ar.width / 2, bx = br.left + br.width / 2;
        return direction === 'Right' ? ax - bx : bx - ax;
    });
    var r = candidates[0].getBoundingClientRect();
    candidates[0].click();
    return {x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2)};
})(%d, '%s', %d)
""" % (cur_y, key, ROW_TOLERANCE * 3)
                    clicked = await eval_js(arrow_js, 3)
                    if clicked:
                        log.debug("%s → clicked row arrow at css(%d,%d)", key, clicked.get("x", 0), clicked.get("y", 0))
                    return

            if target:
                run_xdotool(["xdotool", "mousemove",
                              str(target["x"] * DEVICE_SCALE),
                              str(target["y"] * DEVICE_SCALE)])
                log.debug("%s → card at css(%d,%d)", key, target["x"], target["y"])

    except Exception as e:
        log.debug("CDP navigate failed: %s", e)


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
    elif action in NAV_KEYS:
        await cdp_navigate(NAV_KEYS[action])
    elif action == "select":
        # Click at current mouse position (navigation is mouse-based)
        run_xdotool(["xdotool", "click", "1"])
    elif action == "playpause":
        focus_chromium()
        run_xdotool(["xdotool", "key", "space"])
    elif action == "back":
        ws_url = get_cdp_ws_url()
        if ws_url:
            try:
                await cdp_eval(ws_url, "window.history.back()")
            except Exception:
                pass
        else:
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
