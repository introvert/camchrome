#!/usr/bin/env python3
"""camchrome fingerprint library: capture real devices, check them, pick one to launch with.

Each entry in profiles/library/<os>/<id>.json is one real device's capture in the exact JSON
that --fingerprint-profile imports (components/ungoogled/persona_profile.cc), plus a little
metadata. The browser stays seed-free when a profile is used: a seed next to a profile is the
combination strict scorers flag.

    # 1. On each real device, open the capture page in stock Chrome (same major as camchrome):
    tools/fingerprint_library.py capture --bind 0.0.0.0 --port 8765
    #    then browse to http://<this machine>:8765/ on the device and press "Capture".
    # 2. Check the library (also run by CI):
    tools/fingerprint_library.py check
    tools/fingerprint_library.py list
    # 3. Launch flags for one entry, chosen by OS / GPU vendor, stable for a given --seed:
    tools/fingerprint_library.py pick --os windows --gpu nvidia --seed customer-42
    #    -> --fingerprint-platform=windows --fingerprint-profile=H4sI... --window-size=1536,816

Captures are personal device data. Capture devices you own or have permission to capture, and
keep the library private if it holds anything identifying (mediaDevices labels are dropped).
"""

from __future__ import annotations

import argparse
import base64
import datetime
import gzip
import hashlib
import http.server
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LIBRARY = REPO / "profiles" / "library"
OSES = ("windows", "macos", "linux", "android")
GPU_VENDORS = ("nvidia", "amd", "intel", "apple", "arm", "qualcomm", "imagination", "other")
DEVICE_MEMORY_GRID = (0.25, 0.5, 1, 2, 4, 8, 16, 32)

# WebGL parameters persona_profile.cc reads from webgl.webgl1 / webgl.webgl2 "parameters".
WEBGL1_PARAMS = [
    "ALIASED_LINE_WIDTH_RANGE", "ALIASED_POINT_SIZE_RANGE", "ALPHA_BITS", "BLUE_BITS",
    "DEPTH_BITS", "GREEN_BITS", "RED_BITS", "SAMPLES", "SAMPLE_BUFFERS", "STENCIL_BITS",
    "STENCIL_VALUE_MASK", "STENCIL_WRITEMASK", "STENCIL_BACK_VALUE_MASK",
    "STENCIL_BACK_WRITEMASK", "SUBPIXEL_BITS", "MAX_TEXTURE_SIZE", "MAX_CUBE_MAP_TEXTURE_SIZE",
    "MAX_RENDERBUFFER_SIZE", "MAX_VIEWPORT_DIMS", "MAX_VERTEX_UNIFORM_VECTORS",
    "MAX_FRAGMENT_UNIFORM_VECTORS", "MAX_VARYING_VECTORS", "MAX_COMBINED_TEXTURE_IMAGE_UNITS",
    "MAX_TEXTURE_IMAGE_UNITS", "MAX_VERTEX_TEXTURE_IMAGE_UNITS", "MAX_VERTEX_ATTRIBS",
]
WEBGL2_PARAMS = [
    "MAX_3D_TEXTURE_SIZE", "MAX_ARRAY_TEXTURE_LAYERS", "MAX_COLOR_ATTACHMENTS",
    "MAX_COMBINED_TEXTURE_IMAGE_UNITS", "MAX_COMBINED_UNIFORM_BLOCKS",
    "MAX_CUBE_MAP_TEXTURE_SIZE", "MAX_DRAW_BUFFERS", "MAX_FRAGMENT_UNIFORM_BLOCKS",
    "MAX_FRAGMENT_UNIFORM_COMPONENTS", "MAX_FRAGMENT_UNIFORM_VECTORS", "MAX_RENDERBUFFER_SIZE",
    "MAX_SAMPLES", "MAX_TEXTURE_SIZE", "MAX_UNIFORM_BUFFER_BINDINGS", "MAX_VARYING_VECTORS",
    "MAX_VERTEX_UNIFORM_BLOCKS", "MAX_VERTEX_UNIFORM_COMPONENTS", "MAX_VERTEX_UNIFORM_VECTORS",
    "MAX_ELEMENT_INDEX", "MAX_TEXTURE_LOD_BIAS",
]
WEBGPU_LIMITS = [
    "maxTextureDimension1D", "maxTextureDimension2D", "maxTextureDimension3D",
    "maxTextureArrayLayers", "maxBindGroups", "maxBindGroupsPlusVertexBuffers",
    "maxBindingsPerBindGroup", "maxDynamicUniformBuffersPerPipelineLayout",
    "maxDynamicStorageBuffersPerPipelineLayout", "maxSampledTexturesPerShaderStage",
    "maxSamplersPerShaderStage", "maxStorageBuffersPerShaderStage",
    "maxStorageTexturesPerShaderStage", "maxUniformBuffersPerShaderStage",
    "maxUniformBufferBindingSize", "maxStorageBufferBindingSize",
    "minUniformBufferOffsetAlignment", "minStorageBufferOffsetAlignment", "maxVertexBuffers",
    "maxBufferSize", "maxVertexAttributes", "maxVertexBufferArrayStride",
    "maxInterStageShaderVariables", "maxColorAttachments", "maxColorAttachmentBytesPerSample",
    "maxComputeWorkgroupStorageSize", "maxComputeInvocationsPerWorkgroup",
    "maxComputeWorkgroupSizeX", "maxComputeWorkgroupSizeY", "maxComputeWorkgroupSizeZ",
    "maxComputeWorkgroupsPerDimension", "maxStorageBuffersInFragmentStage",
    "maxStorageTexturesInFragmentStage", "maxStorageBuffersInVertexStage",
    "maxStorageTexturesInVertexStage",
]
CODECS = {
    "h264": 'video/mp4; codecs="avc1.640028"',
    "vp8": 'video/webm; codecs="vp8"',
    "vp9": 'video/webm; codecs="vp09.00.40.08"',
    "av1": 'video/mp4; codecs="av01.0.08M.08"',
    "hevc": 'video/mp4; codecs="hvc1.1.6.L120.90"',
}


def font_candidates() -> list[str]:
    """Every family name the engine's persona font lists know (040, 992), so a capture records
    which of them the device really has."""
    names: set[str] = set()
    for patch in ("040-fonts.patch", "992-android-persona-mobile-behaviour.patch"):
        path = REPO / "patches" / patch
        if not path.exists():
            continue
        inside = False
        for line in path.read_text(errors="replace").splitlines():
            if "GetPlatformFonts" in line or 'platform == "android"' in line:
                inside = True
            elif "IsBasicFont" in line or "return {};" in line:
                inside = False
            if inside and line.startswith(("+", " ")) and "//" not in line.split('"')[0]:
                names.update(re.findall(r'"([^"\\]{2,60})"', line))
    names -= {"windows", "linux", "macos", "android"}
    return sorted(names) or ["Arial", "Segoe UI", "Calibri", "Helvetica Neue", "Ubuntu", "Roboto"]


CAPTURE_PAGE = r"""<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>camchrome capture</title>
<style>body{font:16px system-ui,sans-serif;margin:24px;max-width:640px}
button{font-size:18px;padding:12px 20px}pre{white-space:pre-wrap;font-size:12px}</style>
<h1>camchrome device capture</h1>
<p>Records this browser's fingerprint surfaces for the camchrome profile library. Nothing is sent
anywhere except the machine that served this page. Use stock Chrome, not a modified browser,
with no extensions that change fingerprints.</p>
<button id="go">Capture</button> <span id="state"></span>
<pre id="out"></pre>
<script>
const WEBGL1 = __WEBGL1__, WEBGL2 = __WEBGL2__, WEBGPU_LIMITS = __WEBGPU__, CODECS = __CODECS__;
const FONTS = __FONTS__;

function glInfo(kind, names) {
  const c = document.createElement('canvas');
  const gl = c.getContext(kind);
  if (!gl) return null;
  const out = {parameters: {}, extensions: gl.getSupportedExtensions() || []};
  const dbg = gl.getExtension('WEBGL_debug_renderer_info');
  if (dbg) out.debug = {UNMASKED_VENDOR_WEBGL: gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL),
                        UNMASKED_RENDERER_WEBGL: gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL)};
  for (const n of names) {
    if (gl[n] === undefined) continue;
    const v = gl.getParameter(gl[n]);
    out.parameters[n] = (v && typeof v === 'object' && 'length' in v) ? Array.from(v) : v;
  }
  const an = gl.getExtension('EXT_texture_filter_anisotropic');
  if (an) out.parameters.MAX_TEXTURE_MAX_ANISOTROPY_EXT =
      gl.getParameter(an.MAX_TEXTURE_MAX_ANISOTROPY_EXT);
  return out;
}

function detectFonts() {
  const ctx = document.createElement('canvas').getContext('2d');
  const w = f => { ctx.font = `72px ${f}`; return ctx.measureText('mmmmmmmmmmlli10OOwW@#').width; };
  const bases = ['monospace', 'serif', 'sans-serif'];
  const baseW = Object.fromEntries(bases.map(b => [b, w(b)]));
  return FONTS.filter(f => bases.some(b => w(`"${f}", ${b}`) !== baseW[b]));
}

async function voices() {
  let v = speechSynthesis.getVoices();
  if (!v.length) await new Promise(r => { speechSynthesis.onvoiceschanged = r; setTimeout(r, 2000); });
  return speechSynthesis.getVoices().map(x => ({voice_uri: x.voiceURI, name: x.name, lang: x.lang,
                                                local_service: x.localService, default: x.default}));
}

async function capture() {
  const p = {};
  p.hardware_concurrency = navigator.hardwareConcurrency;
  p.device_memory = navigator.deviceMemory;
  p.max_touch_points = navigator.maxTouchPoints;
  p.screen = {width: screen.width, height: screen.height, avail_width: screen.availWidth,
              avail_height: screen.availHeight, color_depth: screen.colorDepth,
              device_pixel_ratio: devicePixelRatio};
  p.webgl = {webgl1: glInfo('webgl', WEBGL1), webgl2: glInfo('webgl2', WEBGL2)};
  try {
    const ac = new AudioContext();
    p.audio = {BaseAudioContextSampleRate: ac.sampleRate, AudioContextBaseLatency: ac.baseLatency,
               AudioContextOutputLatency: ac.outputLatency};
    ac.close();
  } catch (e) {}
  if (performance.memory) p.perf_memory = {jsHeapSizeLimit: performance.memory.jsHeapSizeLimit};
  if (navigator.userAgentData) {
    const he = await navigator.userAgentData.getHighEntropyValues(
        ['uaFullVersion', 'platformVersion', 'model', 'architecture', 'bitness']);
    p.navigator = {uadata: {platform: navigator.userAgentData.platform,
                            mobile: navigator.userAgentData.mobile, high_entropy: he}};
  }
  p.speech = await voices();
  p.fonts = {detected: detectFonts()};
  try {
    const a = navigator.gpu && await navigator.gpu.requestAdapter();
    if (a) {
      p.webgpu = {info: {vendor: a.info.vendor, architecture: a.info.architecture},
                  features: Array.from(a.features).sort(), limits: {}};
      for (const k of WEBGPU_LIMITS) if (a.limits[k] !== undefined) p.webgpu.limits[k] = a.limits[k];
    }
  } catch (e) {}
  p.mediaCapabilities = {};
  for (const [k, type] of Object.entries(CODECS)) {
    try {
      const r = await navigator.mediaCapabilities.decodingInfo({type: 'file', video:
          {contentType: type, width: 1920, height: 1080, bitrate: 4000000, framerate: 30}});
      p.mediaCapabilities[k] = {supported: r.supported, smooth: r.smooth,
                                powerEfficient: r.powerEfficient};
    } catch (e) {}
  }
  try {
    // Kinds only: labels need a permission prompt and identify the owner.
    p.mediaDevices = (await navigator.mediaDevices.enumerateDevices())
        .map(d => ({kind: d.kind, label: ''}));
  } catch (e) {}
  const mq = q => matchMedia(q).matches;
  p.css = {'color-gamut:srgb': mq('(color-gamut: srgb)'), 'color-gamut:p3': mq('(color-gamut: p3)'),
           'color-gamut:rec2020': mq('(color-gamut: rec2020)')};
  return {profile: p, window: [outerWidth, outerHeight], userAgent: navigator.userAgent};
}

document.getElementById('go').onclick = async () => {
  const state = document.getElementById('state');
  state.textContent = 'capturing...';
  const c = await capture();
  const r = await fetch('/capture', {method: 'POST', body: JSON.stringify(c)});
  const t = await r.text();
  state.textContent = r.ok ? 'saved' : 'rejected';
  document.getElementById('out').textContent = t;
};
</script>"""


# ---- entries ---------------------------------------------------------------------------------

def os_of(capture: dict) -> str:
    plat = (((capture.get("profile") or {}).get("navigator") or {}).get("uadata") or {}).get(
        "platform", "")
    ua = capture.get("userAgent", "")
    if plat == "Android" or "Android" in ua:
        return "android"
    if plat == "Windows" or "Windows NT" in ua:
        return "windows"
    if plat == "macOS" or "Macintosh" in ua:
        return "macos"
    return "linux"


def gpu_vendor(renderer: str) -> str:
    r = renderer.lower()
    for key, names in (("nvidia", ("nvidia", "geforce", "quadro", "rtx")),
                       ("amd", ("amd", "radeon", "ati ")),
                       ("intel", ("intel",)),
                       ("apple", ("apple",)),
                       ("qualcomm", ("qualcomm", "adreno")),
                       ("arm", ("arm,", "mali")),
                       ("imagination", ("powervr", "imagination"))):
        if any(n in r for n in names):
            return key
    return "other"


def renderer_of(profile: dict) -> str:
    return ((((profile.get("webgl") or {}).get("webgl1") or {}).get("debug") or {})
            .get("UNMASKED_RENDERER_WEBGL", ""))


def make_entry(capture: dict) -> dict:
    profile = capture["profile"]
    # Drop what would identify the owner.
    for d in profile.get("mediaDevices") or []:
        d["label"] = ""
        d.pop("device_id", None)
        d.pop("group_id", None)
    os_name = os_of(capture)
    renderer = renderer_of(profile)
    scr = profile.get("screen") or {}
    digest = hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest()[:10]
    return {
        "id": f"{os_name}-{gpu_vendor(renderer)}-{scr.get('width')}x{scr.get('height')}-{digest}",
        "platform": os_name,
        "gpu_vendor": gpu_vendor(renderer),
        "renderer": renderer,
        "captured": datetime.date.today().isoformat(),
        "chrome": (((profile.get("navigator") or {}).get("uadata") or {}).get("high_entropy")
                   or {}).get("uaFullVersion", ""),
        "window": capture.get("window"),
        "profile": profile,
    }


def check_entry(e: dict) -> list[str]:
    """Coherence rules a real device satisfies; an entry that breaks one is not imported."""
    errs = []
    p = e.get("profile") or {}
    os_name = e.get("platform")
    if os_name not in OSES:
        errs.append(f"platform {os_name!r} not one of {OSES}")
    scr = p.get("screen") or {}
    w, h = scr.get("width", 0), scr.get("height", 0)
    if not (w and h):
        errs.append("screen size missing")
    if scr.get("avail_width", w) > w or scr.get("avail_height", h) > h:
        errs.append("available screen larger than the screen")
    if scr.get("color_depth") not in (None, 24, 30, 48):
        errs.append(f"colour depth {scr.get('color_depth')}")
    dpr = scr.get("device_pixel_ratio", 1)
    if not 0.5 <= dpr <= 5:
        errs.append(f"device pixel ratio {dpr}")
    hc = p.get("hardware_concurrency")
    if not isinstance(hc, int) or not 1 <= hc <= 256:
        errs.append(f"hardwareConcurrency {hc}")
    dm = p.get("device_memory")
    if dm is not None and dm not in DEVICE_MEMORY_GRID:
        errs.append(f"deviceMemory {dm} is not on Chrome's grid")
    touch = p.get("max_touch_points", 0)
    renderer = renderer_of(p)
    vendor = ((((p.get("webgl") or {}).get("webgl1") or {}).get("debug") or {})
              .get("UNMASKED_VENDOR_WEBGL", ""))
    if not renderer:
        errs.append("no WebGL renderer (UNMASKED_RENDERER_WEBGL)")
    if renderer and not vendor.startswith("Google Inc."):
        errs.append(f"WebGL vendor {vendor!r}: Chrome reports 'Google Inc. (...)' via ANGLE")
    r = renderer.lower()
    if "swiftshader" in r or "llvmpipe" in r or "software" in r:
        errs.append("software renderer: not a real GPU")
    if os_name == "windows" and renderer and "direct3d" not in r and "d3d" not in r:
        errs.append("Windows renderer without Direct3D")
    if os_name == "macos" and renderer and "metal" not in r and "apple" not in r:
        errs.append("macOS renderer without Metal/Apple")
    if os_name == "android":
        if not touch:
            errs.append("Android with maxTouchPoints 0")
        if renderer and "opengl es" not in r and "vulkan" not in r:
            errs.append("Android renderer without OpenGL ES/Vulkan")
    elif touch > 10:
        errs.append(f"desktop with maxTouchPoints {touch}")
    if os_name != "android" and not ((p.get("fonts") or {}).get("detected")):
        errs.append("no fonts detected")
    gpu = ((p.get("webgpu") or {}).get("info") or {}).get("vendor", "")
    if gpu and renderer and gpu.lower() not in r and not (gpu == "apple" and "apple" in r):
        errs.append(f"WebGPU vendor {gpu!r} does not match the WebGL renderer")
    return errs


def load_library() -> list[tuple[Path, dict]]:
    out = []
    for path in sorted(LIBRARY.glob("*/*.json")):
        out.append((path, json.loads(path.read_text())))
    return out


def encode_profile(profile: dict) -> str:
    raw = json.dumps(profile, separators=(",", ":"), sort_keys=True).encode()
    return base64.b64encode(gzip.compress(raw, mtime=0)).decode()


def launch_flags(e: dict) -> list[str]:
    flags = [f"--fingerprint-platform={e['platform']}",
             f"--fingerprint-profile={encode_profile(e['profile'])}"]
    scr = e["profile"].get("screen") or {}
    win = e.get("window")
    if e["platform"] == "android":
        flags.append(f"--window-size={scr.get('width')},{scr.get('height')}")
    elif win and win[0] and win[1]:
        w = min(win[0], scr.get("avail_width", win[0]))
        h = min(win[1], scr.get("avail_height", win[1]))
        flags.append(f"--window-size={w},{h}")
    return flags


# ---- commands --------------------------------------------------------------------------------

def cmd_capture(args) -> int:
    page = (CAPTURE_PAGE.replace("__WEBGL1__", json.dumps(WEBGL1_PARAMS))
            .replace("__WEBGL2__", json.dumps(WEBGL2_PARAMS))
            .replace("__WEBGPU__", json.dumps(WEBGPU_LIMITS))
            .replace("__CODECS__", json.dumps(CODECS))
            .replace("__FONTS__", json.dumps(font_candidates()))).encode()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            try:
                entry = make_entry(json.loads(body))
                errs = check_entry(entry)
            except Exception as e:  # noqa: BLE001 -- report anything back to the page
                entry, errs = None, [f"unreadable capture: {e}"]
            if errs:
                msg = "not saved:\n  " + "\n  ".join(errs)
                code = 422
            else:
                path = LIBRARY / entry["platform"] / f"{entry['id']}.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(entry, indent=1, sort_keys=True) + "\n")
                msg = f"saved {path.relative_to(REPO)}\n{entry['renderer']}"
                code = 200
            print(msg, flush=True)
            out = msg.encode()
            self.send_response(code)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer((args.bind, args.port), Handler)
    print(f"capture page on http://{args.bind}:{args.port}/ -- open it on each device; "
          "Ctrl-C to stop", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def cmd_check(args) -> int:
    entries = load_library()
    bad = 0
    ids = set()
    for path, e in entries:
        errs = check_entry(e)
        if e.get("id") in ids:
            errs.append("duplicate id")
        ids.add(e.get("id"))
        if path.parent.name != e.get("platform"):
            errs.append(f"stored under {path.parent.name}/ but platform is {e.get('platform')}")
        if errs:
            bad += 1
            print(f"FAIL {path.relative_to(REPO)}: " + "; ".join(errs))
    print(f"library: {len(entries)} entries, {bad} failing")
    return 1 if bad else 0


def cmd_list(args) -> int:
    for _, e in load_library():
        scr = e["profile"].get("screen") or {}
        print(f"{e['platform']:8} {e['gpu_vendor']:9} {scr.get('width')}x{scr.get('height')}"
              f"@{scr.get('device_pixel_ratio')}  cores={e['profile'].get('hardware_concurrency')}"
              f"  {e['renderer'][:60]}  {e['id']}")
    return 0


def select(entries: list[dict], os_name: str | None, gpu: str | None, seed: str | None) -> dict:
    pool = [e for e in entries if (not os_name or e["platform"] == os_name)
            and (not gpu or e["gpu_vendor"] == gpu)]
    if not pool:
        raise SystemExit(f"no library entry for os={os_name} gpu={gpu}")
    pool.sort(key=lambda e: e["id"])
    if seed is None:
        import secrets
        return pool[secrets.randbelow(len(pool))]
    h = int.from_bytes(hashlib.sha256(seed.encode()).digest()[:8], "big")
    return pool[h % len(pool)]


def cmd_pick(args) -> int:
    e = select([e for _, e in load_library()], args.os, args.gpu, args.seed)
    if args.format == "json":
        print(json.dumps({"id": e["id"], "flags": launch_flags(e)}, indent=1))
    else:
        print(" ".join(launch_flags(e)))
    return 0


def cmd_encode(args) -> int:
    e = json.loads(Path(args.entry).read_text())
    print(encode_profile(e.get("profile", e)))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("capture", help="serve the capture page and save what devices send")
    c.add_argument("--bind", default="127.0.0.1")
    c.add_argument("--port", type=int, default=8765)
    sub.add_parser("check", help="check every library entry's coherence")
    sub.add_parser("list", help="list the library")
    p = sub.add_parser("pick", help="print launch flags for one entry")
    p.add_argument("--os", choices=OSES)
    p.add_argument("--gpu", choices=GPU_VENDORS)
    p.add_argument("--seed", help="same seed, same entry (default: random)")
    p.add_argument("--format", choices=["flags", "json"], default="flags")
    en = sub.add_parser("encode", help="print the --fingerprint-profile value of one entry")
    en.add_argument("entry")
    args = ap.parse_args()
    return {"capture": cmd_capture, "check": cmd_check, "list": cmd_list, "pick": cmd_pick,
            "encode": cmd_encode}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
