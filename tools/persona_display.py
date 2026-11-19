#!/usr/bin/env python3
"""Run a command on a virtual X display shaped like the persona's screen, with a window manager.

On a Linux server without a monitor, camchrome runs headful under Xvfb. Two things about that
display are visible to pages and differ from a desktop unless they are set up:

  * the screen size: a fullscreen or maximised window covers the *X* screen, so an X screen of
    another size than the persona's screen gives a window that does not fit the screen the page
    is told about;
  * the window manager: without one, X11 windows never change state, so requestFullscreen()
    switches display-mode but the window keeps its size (stock Chrome too).

This tool asks the browser for the persona's screen (`--clearcote-validate-persona=current`,
patch 974), starts Xvfb at exactly that size, starts a window manager, and runs the command with
DISPLAY pointing at it. The browser renders at the X screen's scale (1 under Xvfb) and reports
the persona's devicePixelRatio to scripts, so the X screen is the persona screen in CSS pixels;
pass --scale=<dpr> only if you also run the browser with --force-device-scale-factor=<dpr>.

    tools/persona_display.py --binary build/src/out/Default/chrome \\
        --fingerprint=alice --fingerprint-platform=windows -- \\
        build/src/out/Default/chrome --fingerprint=alice --fingerprint-platform=windows

    # or around a harness that launches the browser itself
    tools/persona_display.py --fingerprint=testseed --fingerprint-platform=linux -- \\
        tools/stealth_audit.py --seed testseed --platform linux --interact

Needs Xvfb (package xvfb) and a window manager on PATH (openbox, fluxbox, xfwm4, icewm,
matchbox-window-manager, metacity), or --wm "<command>". Persona switches go before `--`; they
only select the screen and are not added to the command.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WMS = ["openbox", "fluxbox", "xfwm4", "icewm", "matchbox-window-manager", "metacity"]


def persona_screen(binary: str, persona_flags: list[str]) -> tuple[int, int, float]:
    out = subprocess.run([binary, "--clearcote-validate-persona=current", *persona_flags],
                         capture_output=True, text=True, timeout=60).stdout
    m = re.search(r"screen=(\d+)x(\d+).*?dpr=([\d.]+)", out)
    if not m:
        raise SystemExit(f"could not read the persona screen from {binary}:\n{out[:400]}")
    return int(m.group(1)), int(m.group(2)), float(m.group(3))


def free_display() -> int:
    for n in range(90, 200):
        if not Path(f"/tmp/.X11-unix/X{n}").exists() and not Path(f"/tmp/.X{n}-lock").exists():
            return n
    raise SystemExit("no free X display number")


def main() -> int:
    argv = sys.argv[1:]
    if "--" not in argv:
        print(__doc__)
        return 2
    cut = argv.index("--")
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--binary", default=str(REPO / "build/src/out/Default/chrome"))
    ap.add_argument("--wm", help="window manager command (default: the first of %s on PATH)"
                    % ", ".join(WMS))
    ap.add_argument("--depth", default="24")
    ap.add_argument("--scale", type=float, default=1.0,
                    help="X screen pixels per CSS pixel (match --force-device-scale-factor)")
    args, persona = ap.parse_known_args(argv[:cut])
    command = argv[cut + 1:]
    if not command:
        raise SystemExit("nothing to run after --")
    if not any(f.startswith("--fingerprint") for f in persona):
        raise SystemExit("pass the persona switches (--fingerprint=..., --fingerprint-platform=...)")

    w, h, dpr = persona_screen(args.binary, persona)
    pw, ph = round(w * args.scale), round(h * args.scale)
    wm = args.wm or next((x for x in WMS if shutil.which(x)), None)
    if not wm:
        raise SystemExit("no window manager found; install openbox or pass --wm")
    if not shutil.which("Xvfb"):
        raise SystemExit("Xvfb not found; install the xvfb package")

    n = free_display()
    display = f":{n}"
    xvfb = subprocess.Popen(["Xvfb", display, "-screen", "0", f"{pw}x{ph}x{args.depth}",
                             "-nolisten", "tcp"], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    procs = [xvfb]
    try:
        for _ in range(100):
            if Path(f"/tmp/.X11-unix/X{n}").exists():
                break
            time.sleep(0.05)
        env = dict(os.environ, DISPLAY=display)
        procs.append(subprocess.Popen(wm, shell=True, env=env, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL, start_new_session=True))
        time.sleep(1.0)
        print(f"persona_display: {display} {pw}x{ph} (persona {w}x{h}, reported dpr {dpr}), "
              f"wm: {wm}",
              file=sys.stderr, flush=True)
        return subprocess.call(command, env=env)
    finally:
        for p in reversed(procs):
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGTERM) if p is not xvfb else p.terminate()
                p.wait(timeout=5)
            except Exception:
                p.kill()


if __name__ == "__main__":
    sys.exit(main())
