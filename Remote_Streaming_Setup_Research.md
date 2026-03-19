# Remote-Controlled Streaming Setup for Roku TV — Feasibility Research

**Objective:** Evaluate approaches for controlling a browser-based streaming site ("X Prime") on a Roku TV from an iPhone, using a dedicated always-on device connected via HDMI, with a TV-remote-style navigation experience.

---

## Executive Summary

This is very doable. The strongest approach is a **Raspberry Pi 5 running Chromium in kiosk mode**, controlled from your iPhone via a **custom web-based remote interface** that sends commands over WebSocket to a lightweight server on the Pi. The server translates button presses into browser actions (keyboard/mouse simulation via `xdotool` or `ydotool`). This gives you the "Roku remote feel" on your phone without the lag and clunkiness of VNC.

A budget mini PC (Intel N100-class) is the higher-performance alternative if the Pi's browser video playback falls short for X Prime specifically. Total cost ranges from **$90–$200** depending on the path.

---

## Part 1: Hardware Options Compared

### Option A — Raspberry Pi 5 (4GB)

The Pi 5 is the natural fit for this project. It boots directly into a full-screen Chromium browser via well-documented kiosk mode setups, and the community has extensively validated this use case for digital signage and dashboard displays.

**Video performance reality check:** The Pi 5 handles 1080p browser-based video streaming reasonably well on Raspberry Pi OS with the Labwc compositor. Tom's Hardware testing showed only ~1.8% dropped frames at 1080p/60fps on YouTube with the latest OS. However, H.264 decoding is software-only on the Pi 5 (the hardware block was removed from the SoC) — only H.265/HEVC gets hardware acceleration. This means CPU usage during video playback sits around 50% at 1080p. For a browser-based streaming site, performance depends heavily on what codec X Prime uses and how heavy their web UI is.

**HDMI-CEC caveat (important for your Roku TV):** The Pi 5 supports HDMI-CEC natively, and there's a kiosk project on GitHub that passes TV remote d-pad presses directly through to the Chromium browser. However, Roku TVs have notoriously limited CEC support. By default, the Roku remote only sends basic CEC commands (power, volume, input switching) — it does *not* forward directional/OK/back button presses to HDMI-connected devices. There's a hidden developer menu (Home ×5, Rewind, Down, Fast Forward, Down, Rewind) that enables "CEC Remote Control," but even with this enabled, results are inconsistent. Multiple community reports confirm that Roku TVs struggle to pass navigation keypresses to a Pi. **This means you should plan around iPhone-based control from the start, not the Roku remote.**

| Attribute | Detail |
|-----------|--------|
| Board (4GB) | ~$65 |
| Power supply (27W official) | ~$12 |
| Case + cooling | ~$10–15 |
| MicroSD card (32GB A2) | ~$8 |
| Micro-HDMI to HDMI cable | ~$8 |
| **Total** | **~$95–110** |
| Power draw | ~3.5W idle, ~8W under load |
| Video capability | 1080p/60 in browser (software H.264), good with latest Pi OS + Labwc |
| Boot to browser | ~15–30 seconds with kiosk setup |
| Form factor | Credit-card sized, tucks behind TV easily |

**Pros:** Cheapest option, lowest power, tiny form factor, huge community/documentation, GPIO for future tinkering, native HDMI-CEC support (even if Roku limits it).

**Cons:** Browser video performance is "good enough" but not bulletproof — depends on X Prime's codec/UI complexity. No H.264 hardware decode. 4GB RAM can get tight with heavy web pages. MicroSD storage is slower than SSD (can add NVMe HAT for ~$15–25 more).

---

### Option B — Budget Mini PC (Intel N100/N150 class)

A mini PC with an Intel N100 or N150 processor is the "just works" option for browser-based streaming. Intel Quick Sync provides hardware-accelerated decode for H.264, H.265, VP9, and AV1 — every codec X Prime might use. Browser performance is effectively identical to a modern laptop.

New options like the GMKtec NucBox G3 or similar N100/N150 units run $130–180 with RAM and SSD included. Used options (HP EliteDesk 800 G3, Lenovo ThinkCentre Tiny, etc.) can be found for $80–120 on eBay with an SSD and 8GB+ RAM. These have full-size HDMI or DisplayPort and idle at 3.5–10W depending on the model and OS.

| Attribute | Detail |
|-----------|--------|
| New N100 mini PC | ~$140–180 (includes RAM + SSD) |
| Used business micro PC | ~$80–120 on eBay |
| Additional accessories | ~$0–10 (HDMI cable, maybe a USB WiFi adapter) |
| **Total** | **$80–190** |
| Power draw | 3.5–12W idle depending on model/OS |
| Video capability | Full hardware decode for all codecs, handles 4K easily |
| Form factor | Roughly 5"×5"×1.5" (small but not Pi-small) |

**Pros:** Dramatically better browser/video performance, runs full Linux or Windows, no codec worries, often includes SSD + RAM, silent or near-silent at idle, x86 means perfect software compatibility.

**Cons:** Larger than a Pi, slightly higher power draw, used units may have cosmetic wear, no GPIO (irrelevant for this project), HDMI-CEC requires a Pulse-Eight USB adapter (~$35) if you want TV remote passthrough (which you probably don't need given the Roku CEC limitation).

---

### Option C — Android TV Stick/Box (e.g., Fire TV Stick, Chromecast)

These could work if X Prime has an Android app or a mobile-friendly site, since Android TV devices have a built-in browser (Silk on Fire TV, or sideloaded Chrome). The physical remote already provides the d-pad/select/back navigation you want.

However, the browser experience on Android TV devices is generally poor for desktop-oriented streaming sites. You'd be fighting with a mobile user-agent, limited browser capabilities, no extensions, and potential DRM issues. If X Prime is specifically a desktop browser site, this path likely dead-ends.

| Attribute | Detail |
|-----------|--------|
| Cost | $25–50 (Fire TV Stick 4K, etc.) |
| Browser quality | Poor for desktop sites — mobile UA, limited features |
| Remote control | Built-in d-pad remote works natively |
| Feasibility for desktop streaming site | **Low** |

**Pros:** Cheapest option, zero setup, native remote.

**Cons:** Browser on Android TV devices is not designed for desktop streaming sites. Likely a dealbreaker.

---

### Option D — Keep Laptop, Add Wireless Control

Before going the dedicated-device route: your existing laptop could be controlled via VNC or a remote desktop app from your iPhone, eliminating the "get up to use the trackpad" problem entirely. RealVNC Viewer (free for personal use) on iOS works well for this. The UX wouldn't be as clean as a custom remote, but it's zero additional cost.

| Attribute | Detail |
|-----------|--------|
| Cost | $0 (software only) |
| Setup time | ~15 minutes |
| UX quality | Mediocre — pinch/zoom/tiny targets on phone screen |

---

## Part 2: Software & Control Methods Compared

This is the more interesting design problem. You want Roku-remote-style navigation (d-pad, select, play/pause, back) on your iPhone, controlling a browser that's displaying on your TV. Here are the viable approaches, ordered from best to worst UX.

### Method 1: Custom Web-Based Remote UI (★ Recommended)

**Architecture:** The Pi (or mini PC) runs a lightweight Python or Node.js WebSocket server alongside the kiosk browser. You open a web page on your iPhone (served by the Pi on your local network) that displays a TV-remote-style interface — big d-pad buttons, play/pause, select, back, scroll up/down. Each button press sends a WebSocket message to the server, which translates it into a keyboard/mouse event injected into the browser using `xdotool` (X11) or `ydotool` (Wayland).

There's even an existing project called **xdotoolweb** on GitHub that does exactly this — it pipes WebSocket messages into xdotool's stdin, providing a web-based keyboard/mouse interface designed for phone-to-Pi remote control.

**Why this is the best approach:**

- The remote UI loads instantly in Safari — no app install needed
- You design the button layout specifically for X Prime's navigation (scroll to next title, select, play/pause, fullscreen toggle, back)
- Near-zero latency (WebSocket on local network is sub-10ms)
- No video stream to your phone (VNC's main overhead), since you're only sending button commands
- You can add X Prime-specific macro buttons (e.g., "go to search," "scroll down 3 rows")
- The web page can be saved to your iPhone home screen as a PWA for app-like access
- Works identically whether the device is a Pi or a mini PC

**Technical complexity:** Moderate. You'd write ~100 lines of Python for the WebSocket server + xdotool bridge, and a single HTML/CSS/JS file for the remote UI. With your Python background, this is very achievable. The key mapping would look something like:

- D-pad Up/Down → `xdotool key Up` / `xdotool key Down` (or `key Tab` / `shift+Tab` depending on how X Prime handles focus)
- D-pad Left/Right → `xdotool key Left` / `xdotool key Right`
- Select/OK → `xdotool key Return`
- Play/Pause → `xdotool key space`
- Back → `xdotool key alt+Left` (browser back) or `xdotool key Escape`
- Scroll → `xdotool click 4` / `xdotool click 5` (scroll wheel)

**Estimated build time:** 2–4 hours for a working prototype, another 2–4 hours to polish the UI.

---

### Method 2: VNC (RealVNC or noVNC)

**Architecture:** The Pi runs a VNC server, and you connect with RealVNC Viewer on iOS (native app) or noVNC (browser-based — no app install needed). You see the Pi's screen mirrored on your phone and interact via touch.

**RealVNC Viewer (iOS app):** Free, mature, works well with the Pi's built-in VNC server. Your finger acts as a mouse — tap to click, drag to move cursor. It's functional but not TV-friendly. You're essentially using your phone as a tiny trackpad, which is the opposite of the "big button Roku remote" feel you want.

**noVNC (browser-based):** Same concept but runs in Safari — no app needed. Slightly more latency than the native app, but avoids the App Store.

| Attribute | Detail |
|-----------|--------|
| Setup complexity | Very low — enable VNC on Pi, install viewer on iPhone |
| UX quality | **Poor for TV navigation** — you're mousing around a browser on a 6" phone screen |
| Latency | Low-medium (acceptable on local WiFi, occasional frame drops) |
| Bandwidth | Moderate — streaming the screen image to your phone continuously |

**Pros:** Quick to set up, well-documented, works out of the box.

**Cons:** This gives you a "tiny laptop on your phone" experience, not a "remote control" experience. You'll be pinching, zooming, and trying to tap small browser elements — exactly the kind of fiddly interaction you're trying to escape. Fine for occasional admin tasks, bad for nightly movie watching.

**Verdict:** Good as a fallback/admin channel, but not the primary control method.

---

### Method 3: Existing Remote Control Apps (Unified Remote, Remote Pi, etc.)

There are iOS apps like **Unified Remote** and **Remote Pi** that turn your phone into a keyboard/trackpad for a remote machine. They typically work over TCP/WiFi and provide a touchpad surface plus a keyboard.

**Remote Pi** specifically targets the Raspberry Pi and sends keyboard/mouse events over the network. However, the app hasn't been meaningfully updated in years and reviews are mixed.

**Unified Remote** is more polished and offers a "media remote" layout with play/pause/volume buttons, plus a touchpad mode. It requires a server component running on the Pi.

| Attribute | Detail |
|-----------|--------|
| Cost | Free–$5 (varies by app) |
| Setup | Install server on Pi + app on iPhone |
| UX quality | Moderate — better than VNC, worse than a custom solution |
| Customizability | Limited — you're stuck with their button layouts |

**Verdict:** Decent middle-ground if you don't want to build anything custom. Unified Remote's media layout gets you 70% of the way there.

---

### Method 4: SSH + Scripted Commands

If you just want bare-minimum control, you can SSH into the Pi from an iOS terminal app (Termius, Blink) and run xdotool commands manually. This is ugly but functional:

```
ssh pi@192.168.1.x "xdotool key space"  # play/pause
ssh pi@192.168.1.x "xdotool key Return"  # select
```

You could wrap these in iOS Shortcuts for one-tap execution, but the UX would be clunky — each command opens a new SSH session with noticeable latency.

**Verdict:** Emergency fallback only. Not a real solution for daily use.

---

## Part 3: Can You Get a Roku-Remote-Style UI on iPhone?

**Yes, absolutely.** This is the most straightforward part of the project. A web-based remote interface (Method 1) gives you complete control over the layout. You'd build an HTML page with large, thumb-friendly buttons arranged in a familiar d-pad pattern:

```
        [  ▲  ]
   [ ◄ ] [OK] [ ► ]
        [  ▼  ]

   [⏮] [⏯] [⏭]   [🔍]
   
   [ ← Back ]     [ ⛶ Fullscreen ]
```

Modern CSS makes this trivial — `touch-action: manipulation` prevents accidental zooms, large `min-height: 60px` buttons are easy to hit in the dark, and you can theme it with a dark background so it doesn't blast your eyes in a dim room.

The web page gets served directly from the Pi, so you just bookmark `http://pi.local:8080/remote` on your iPhone. Save it to your home screen and it launches like a native app with no Safari chrome.

**The hard part isn't the UI — it's the key mapping.** X Prime is a browser-based site, and how well keyboard navigation works depends entirely on how the site was built. If X Prime uses standard HTML focus management, Tab/Shift+Tab/Enter/Escape will navigate cleanly. If it's a heavily custom React/JS app with non-standard focus handling, you may need to fall back to injecting mouse clicks at specific coordinates (less elegant but still workable via xdotool mouse commands).

You'd need to spend some time testing how X Prime responds to keyboard-only navigation and calibrate your remote mappings accordingly. This is the main "unknown" in the project.

---

## Part 4: Feasibility Confidence Ratings

| Category | Rating | Notes |
|----------|--------|-------|
| **Hardware feasibility** | **95/100** | Pi 5 and mini PCs are thoroughly proven for kiosk/browser use. Plug-and-play HDMI. The only risk is Pi 5 video performance if X Prime uses heavy UI or unsupported codecs — mitigated by choosing a mini PC instead. |
| **Software/UI feasibility** | **85/100** | WebSocket + xdotool remote control is well-established. The 15% uncertainty is around X Prime's keyboard navigability — some sites just don't respond well to keyboard-only input, requiring coordinate-based mouse injection instead. |
| **Ease of setup** | **75/100** | Pi kiosk mode has good tutorials but isn't a one-click install — you'll be SSH'd in, editing config files, installing packages. The custom remote UI needs ~4 hours of coding. A mini PC with Linux is slightly easier (better browser out of the box). Overall, this is an afternoon project for someone at your level. |
| **Cost-effectiveness** | **90/100** | $95–$110 for a Pi 5 setup, or $80–$190 for a mini PC. Either way, you're getting a permanently mounted, always-on streaming appliance for well under $200. That's competitive with (and more flexible than) commercial solutions. |
| **Reliability** | **80/100** | Pi + kiosk mode is proven for 24/7 digital signage deployments. The main reliability concern is the browser — Chromium on a Pi can occasionally crash or leak memory over days. A cron job to restart the browser nightly handles this. WiFi stability for the phone-to-Pi WebSocket connection is important. |
| **User experience quality** | **75/100** | With a custom web remote, the from-bed experience will be genuinely good — big buttons, instant response, dark theme. The gap from "75" to "100" is the keyboard-navigation question: if X Prime responds cleanly to keyboard input, UX is excellent. If you're reduced to simulating mouse clicks at screen coordinates, it's functional but fragile (breaks if the site layout changes). |
| **Overall feasibility** | **83/100** | This project is clearly achievable and practical. The architecture is sound, the components are proven, and the cost is low. The main variable is how well X Prime cooperates with keyboard-only navigation — something you can test early with your laptop before buying any hardware. |

---

## Recommended Approach

1. **Test first (free):** Before buying anything, open X Prime on your laptop and try navigating it using only keyboard — Tab, Enter, Arrow keys, Space, Escape. If it works well, the Pi path is ideal. If navigation is painful, you'll need the xdotool mouse-coordinate approach, which still works but takes more calibration.

2. **Buy a Raspberry Pi 5 4GB** (~$95–110 total with accessories). If you discover during testing that Pi browser performance is inadequate for X Prime, return it and grab a used mini PC with an Intel N100/i5 instead (~$80–130 on eBay).

3. **Set up kiosk mode** using the well-documented Raspberry Pi Kiosk Display System (GitHub: TOLDOTECHNIK) or Jeff Geerling's pi-kiosk. Configure Chromium to auto-launch X Prime in fullscreen on boot.

4. **Build the WebSocket remote** — ~100 lines of Python server + one HTML file for the remote UI. The xdotoolweb project on GitHub is a solid starting point to fork.

5. **Save the remote page as a PWA** on your iPhone home screen. Done.

**Total estimated time:** One weekend afternoon to get everything working, another session to polish the remote UI to your liking.
