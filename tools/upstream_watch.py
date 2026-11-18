#!/usr/bin/env python3
"""Compare the pinned upstreams with what upstream has released since.

    tools/upstream_watch.py            # table of pins vs latest, plus the reference repos
    tools/upstream_watch.py --json out.json
    tools/upstream_watch.py --strict   # exit 1 when a pin is behind (for a scheduled job)

Reads UPSTREAM, then asks:
  * ungoogled-chromium and its Windows/macOS overlays: newest tag (git ls-remote)
  * Google Chrome stable per platform: Chrome's public version history API
  * Clearcote: whether the public repo moved past CLEARCOTE_COMMIT, and whether patches/
    changed (shallow, blob-less clone of the patches directory's history)
  * the reference repos in docs/UPSTREAM-SOURCES.md: newest tag and last commit date

Nothing is downloaded beyond git refs, one blob-less clone and one small JSON request per
platform. The list of reference repos is REFERENCE below; keep it in step with the doc.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

PINNED = {  # UPSTREAM key -> GitHub repo whose tags it follows
    "UG_TAG": "ungoogled-software/ungoogled-chromium",
    "UGW_TAG": "ungoogled-software/ungoogled-chromium-windows",
    "UGM_TAG": "ungoogled-software/ungoogled-chromium-macos",
}

# (repo, why it is watched). Same order as docs/UPSTREAM-SOURCES.md.
REFERENCE = [
    ("adryfish/fingerprint-chromium", "engine patches, BSD-3; Clearcote's base"),
    ("arman-bd/chromiumfish", "engine patches (TLS pinning, canvas bridge), MIT"),
    ("brave/brave-core", "farbling reference, MPL-2.0"),
    ("uazo/cromite", "anti-fingerprinting patches, GPL-3.0: read only"),
    ("daijro/camoufox", "Firefox engine; widest surface checklist, MPL-2.0"),
    ("botswin/BotBrowser", "closed engine; changelog lists detection vectors it fixed"),
    ("ProxyShard/ShardBrowser", "closed engine, MIT launcher; feature parity"),
    ("CloakHQ/CloakBrowser", "closed engine, MIT wrapper (humanize, geoip, widevine)"),
    ("Kaliiiiiiiiii-Vinyzu/patchright", "driver: CDP leak fixes"),
    ("rebrowser/rebrowser-patches", "driver: CDP leak list"),
    ("daijro/browserforge", "persona distributions, Apache-2.0"),
    ("apify/fingerprint-suite", "persona distributions and injector, Apache-2.0"),
    ("fingerprintjs/fingerprintjs", "detector: what a common library reads"),
    ("fingerprintjs/BotD", "detector: bot signals"),
    ("abrahamjuliot/creepjs", "detector: lie and coherence tests"),
]

PLATFORMS = ("win", "mac", "linux", "android")


def git(*args: str, cwd: str | None = None, timeout: int = 120) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          timeout=timeout, check=True).stdout


def ver_key(tag: str) -> list:
    return [int(x) if x.isdigit() else x for x in re.split(r"[.\-_]", tag.lstrip("v"))]


def newest_tag(repo: str) -> str | None:
    out = git("ls-remote", "--tags", f"https://github.com/{repo}.git")
    tags = {line.split("refs/tags/")[1].removesuffix("^{}") for line in out.splitlines()
            if "refs/tags/" in line}
    # Version-shaped tags only; a bare date tag (20250728) would outrank every version.
    numeric = [t for t in tags if re.match(r"v?\d{1,4}([.\-_]|$)", t)]
    if not numeric:
        return None
    try:
        return max(numeric, key=ver_key)
    except TypeError:  # mixed shapes; fall back to plain sorting
        return sorted(numeric)[-1]


def last_commit(repo: str) -> str | None:
    with tempfile.TemporaryDirectory() as d:
        try:
            git("clone", "-q", "--depth", "1", "--filter=blob:none", "--no-checkout",
                f"https://github.com/{repo}.git", d, timeout=300)
            return git("log", "-1", "--format=%ad", "--date=short", cwd=d).strip()
        except subprocess.CalledProcessError:
            return None


def chrome_stable(platform: str) -> str | None:
    url = ("https://versionhistory.googleapis.com/v1/chrome/platforms/"
           f"{platform}/channels/stable/versions?pageSize=1")
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return json.load(r)["versions"][0]["version"]
    except Exception as e:  # noqa: BLE001 - report, don't crash the table
        return f"error: {e}"


def clearcote(pinned: str) -> dict:
    with tempfile.TemporaryDirectory() as d:
        git("clone", "-q", "--filter=blob:none", "--no-checkout",
            "https://github.com/clearcotelabs/clearcote-browser.git", d, timeout=600)
        head = git("rev-parse", "HEAD", cwd=d).strip()
        since = git("log", "--oneline", f"{pinned}..HEAD", cwd=d).splitlines()
        patches = git("log", "--format=%h %ad %s", "--date=short", f"{pinned}..HEAD", "--",
                      "patches", cwd=d).splitlines()
        rev = git("show", "HEAD:UPSTREAM_REVISION", cwd=d).strip()
    return {"head": head[:12], "commits_since_pin": len(since),
            "patch_commits_since_pin": patches, "chromium": rev}


def read_pins() -> dict:
    pins = {}
    for line in (REPO / "UPSTREAM").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            pins[k.strip()] = v.strip()
    return pins


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", help="also write the result here")
    ap.add_argument("--strict", action="store_true", help="exit 1 when a pin is behind")
    ap.add_argument("--no-reference", action="store_true", help="skip the reference repos")
    args = ap.parse_args()

    pins = read_pins()
    result: dict = {"pins": {}, "chrome_stable": {}, "reference": []}
    behind = []

    print("Pinned upstreams")
    for key, repo in PINNED.items():
        latest = newest_tag(repo)
        cur = pins.get(key)
        is_behind = bool(latest and cur and ver_key(latest) > ver_key(cur))
        if is_behind:
            behind.append(key)
        result["pins"][key] = {"repo": repo, "pinned": cur, "latest": latest,
                               "behind": is_behind}
        print(f"  {key:8} {cur:24} latest {latest or '?':24} "
              f"{'REBASE DUE' if is_behind else 'ok'}")

    print("\nGoogle Chrome stable")
    for p in PLATFORMS:
        v = chrome_stable(p)
        result["chrome_stable"][p] = v
        print(f"  {p:8} {v}")
    base_major = (pins.get("UG_TAG") or "").split(".")[0]
    majors = {str(v).split(".")[0] for v in result["chrome_stable"].values()}
    if base_major and majors and base_major not in majors:
        print(f"  engine major {base_major} is behind stable {sorted(majors)}; rebase when "
              "ungoogled-chromium tags it")

    print("\nClearcote (vendored patch source)")
    try:
        cc = clearcote(pins.get("CLEARCOTE_COMMIT", "HEAD"))
        result["clearcote"] = cc
        print(f"  head {cc['head']}  {cc['commits_since_pin']} commits since the pin, "
              f"{len(cc['patch_commits_since_pin'])} touch patches/  "
              f"(their tree: Chromium {cc['chromium']})")
        for line in cc["patch_commits_since_pin"][:20]:
            print(f"    {line}")
        if cc["patch_commits_since_pin"]:
            behind.append("CLEARCOTE_COMMIT")
    except Exception as e:  # noqa: BLE001
        result["clearcote"] = {"error": str(e)}
        print(f"  error: {e}")

    if not args.no_reference:
        print("\nReference repos (newest tag, last commit)")
        for repo, why in REFERENCE:
            try:
                tag = newest_tag(repo)
            except Exception:  # noqa: BLE001
                tag = None
            date = last_commit(repo)
            result["reference"].append({"repo": repo, "tag": tag, "last_commit": date,
                                        "why": why})
            print(f"  {repo:36} {tag or '-':28} {date or 'unreachable':12} {why}")

    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=2) + "\n")
    return 1 if (args.strict and behind) else 0


if __name__ == "__main__":
    sys.exit(main())
