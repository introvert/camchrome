#!/usr/bin/env python3
"""Offline per-surface fingerprint self-test for a camchrome build.

The Clearcote audit (tools/stealth_audit.py) is the acceptance test, but it is a
remote page, it needs network, and its result names checks rather than patches.
This harness is the local counterpart: it launches the built browser over raw CDP
(no driver), serves its own pages from 127.0.0.1 and localhost, and asserts one
invariant per patched surface. Every check names the patch(es) it covers, so a
regression points at the diff that caused it.

    tools/fingerprint_selftest.py                         # host-platform persona
    tools/fingerprint_selftest.py --platform windows      # cross-OS persona
    tools/fingerprint_selftest.py --binary /path/chrome --stock   # harness smoke test

What it covers without network:
  * identity: UA / UA-CH / navigator.platform / headers, engine version (010, 987)
  * realms: main frame, iframe and dedicated worker report the same identity (002)
  * locale and timezone: Accept-Language, Intl, --timezone (090, 092)
  * hardware: cores, memory, JS heap, storage quota (030, 145, 146, 980)
  * screen: screen.*, outer window, media queries, DPR, Sec-CH-DPR (140-142, 961, 962, 975)
  * render: canvas determinism per seed and per site, alpha-0 and grey (060, 986),
    WebGL vs WebGPU vendor (070, 075, 987), readPixels sentinel (988)
  * audio determinism per seed (020)
  * devices: voices, media devices, battery, keyboard, connection vs Sec-CH-RTT (148, 150,
    170, 979, 981, 989)
  * WebRTC: no host/private IP in ICE candidates (100)
  * geolocation from --fingerprint-location (180)
  * the persona validator exits clean (974)
  * no injected JS: patched getters still read as native code

`--stock` runs the same harness without any --fingerprint flag and marks the
persona-only checks as expected to fail, which is how the harness itself is tested
on a stock Chromium.
"""

from __future__ import annotations

import argparse
import http.server
import ipaddress
import json
import re
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cdp import Browser  # noqa: E402

REPO = Path(__file__).resolve().parents[1]

ACCEPT_CH = ", ".join([
    "Sec-CH-UA-Full-Version-List", "Sec-CH-UA-Platform-Version", "Sec-CH-UA-Arch",
    "Sec-CH-UA-Bitness", "Sec-CH-UA-Model", "Sec-CH-UA-WoW64", "Sec-CH-DPR", "DPR",
    "Sec-CH-Viewport-Width", "RTT", "Downlink", "ECT", "Sec-CH-Prefers-Color-Scheme",
])

# Collected in every realm that can run it (window, iframe, dedicated worker).
COMMON_JS = r"""
async function collectCommon() {
  const o = {};
  o.userAgent = navigator.userAgent;
  o.platform = navigator.platform;
  o.language = navigator.language;
  o.languages = navigator.languages;
  o.hardwareConcurrency = navigator.hardwareConcurrency;
  o.deviceMemory = navigator.deviceMemory;
  o.webdriver = navigator.webdriver;
  o.timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  o.intlLocale = Intl.DateTimeFormat().resolvedOptions().locale;
  try {
    o.uaData = await navigator.userAgentData.getHighEntropyValues(
      ["fullVersionList", "platformVersion", "architecture", "bitness", "model",
       "wow64", "uaFullVersion"]);
    o.uaData.brands = navigator.userAgentData.brands;
    o.uaData.platform = navigator.userAgentData.platform;
    o.uaData.mobile = navigator.userAgentData.mobile;
  } catch (e) { o.uaDataError = String(e); }
  if (self.navigator.connection) {
    o.connection = {rtt: navigator.connection.rtt, downlink: navigator.connection.downlink,
                    effectiveType: navigator.connection.effectiveType};
  }
  try {
    const c = new OffscreenCanvas(64, 64);
    const gl = c.getContext('webgl');
    if (gl) {
      const d = gl.getExtension('WEBGL_debug_renderer_info');
      o.webgl = {vendor: gl.getParameter(d ? d.UNMASKED_VENDOR_WEBGL : gl.VENDOR),
                 renderer: gl.getParameter(d ? d.UNMASKED_RENDERER_WEBGL : gl.RENDERER)};
    }
  } catch (e) { o.webglError = String(e); }
  return o;
}
"""

MAIN_JS = COMMON_JS + r"""
function hashBytes(a) { let h = 2166136261; for (const b of a) { h ^= b; h = Math.imul(h, 16777619) >>> 0; } return h; }

function canvasProbe() {
  const c = document.createElement('canvas'); c.width = 220; c.height = 40;
  const x = c.getContext('2d');
  x.textBaseline = 'top'; x.font = '16px Arial'; x.fillStyle = '#f60'; x.fillRect(100, 1, 62, 20);
  x.fillStyle = '#069'; x.fillText('camchrome \u{1F600} 1.0', 2, 15);
  x.fillStyle = 'rgba(102,204,0,0.7)'; x.fillText('camchrome', 4, 17);
  const img = x.getImageData(0, 0, c.width, c.height).data;
  const url = c.toDataURL();
  // Re-decode the PNG export into a second canvas: every export path must agree.
  return {hash: hashBytes(img), urlLen: url.length,
          urlHash: hashBytes(new TextEncoder().encode(url)),
          measure: x.measureText('camchrome fingerprint').width};
}

function alphaGreyProbe() {
  const c = document.createElement('canvas'); c.width = 32; c.height = 32;
  const x = c.getContext('2d');
  x.fillStyle = 'rgb(128,128,128)'; x.fillRect(0, 0, 16, 32);   // left: grey
  // right half stays transparent (alpha 0)
  x.fillStyle = '#123'; x.font = '10px Arial'; x.fillText('ab', 18, 10);  // text in the transparent half only
  const d = x.getImageData(0, 0, 32, 32).data;
  let alphaZeroNotBlack = 0, greyTinted = 0;
  for (let i = 0; i < d.length; i += 4) {
    const px = (i / 4) % 32;
    if (d[i + 3] === 0 && (d[i] || d[i + 1] || d[i + 2])) alphaZeroNotBlack++;
    if (px >= 8 && px < 16 && d[i + 3] === 255 && !(d[i] === d[i + 1] && d[i + 1] === d[i + 2])) greyTinted++;
  }
  return {alphaZeroNotBlack, greyTinted};
}

function readPixelsSentinel() {
  const c = document.createElement('canvas'); c.width = 4; c.height = 4;
  const gl = c.getContext('webgl');
  if (!gl) return null;
  gl.clearColor(0.2, 0.4, 0.6, 1); gl.clear(gl.COLOR_BUFFER_BIT);
  // Ask for 6x6 at (-1,-1): the outer ring is outside the framebuffer and must stay untouched.
  const buf = new Uint8Array(6 * 6 * 4).fill(0xAB);
  gl.readPixels(-1, -1, 6, 6, gl.RGBA, gl.UNSIGNED_BYTE, buf);
  let touched = 0;
  for (let y = 0; y < 6; y++) for (let x = 0; x < 6; x++) {
    if (x > 0 && x < 5 && y > 0 && y < 5) continue;
    const i = (y * 6 + x) * 4;
    if (buf[i] !== 0xAB || buf[i + 1] !== 0xAB || buf[i + 2] !== 0xAB || buf[i + 3] !== 0xAB) touched++;
  }
  return {sentinelTouched: touched};
}

async function audioProbe() {
  try {
    const ctx = new OfflineAudioContext(1, 5000, 44100);
    const osc = ctx.createOscillator(); osc.type = 'triangle'; osc.frequency.value = 10000;
    const comp = ctx.createDynamicsCompressor();
    osc.connect(comp); comp.connect(ctx.destination); osc.start(0);
    const buf = await ctx.startRendering();
    const d = buf.getChannelData(0);
    let s = 0; for (let i = 4500; i < 5000; i++) s += Math.abs(d[i]);
    const live = new AudioContext();
    const meta = {sampleRate: live.sampleRate, baseLatency: live.baseLatency,
                  maxChannelCount: live.destination.maxChannelCount};
    live.close();
    return {sum: s, ...meta};
  } catch (e) { return {error: String(e)}; }
}

async function webrtcProbe() {
  if (!self.RTCPeerConnection) return null;
  const pc = new RTCPeerConnection({iceServers: []});
  pc.createDataChannel('x');
  const cands = [];
  pc.onicecandidate = e => { if (e.candidate) cands.push(e.candidate.candidate); };
  await pc.setLocalDescription(await pc.createOffer());
  await new Promise(r => { const t = setTimeout(r, 3000);
    pc.onicegatheringstatechange = () => { if (pc.iceGatheringState === 'complete') { clearTimeout(t); r(); } }; });
  const sdp = pc.localDescription ? pc.localDescription.sdp : '';
  pc.close();
  return {candidates: cands, sdpCandidates: sdp.split('\n').filter(l => l.startsWith('a=candidate'))};
}

// Families that only one desktop OS ships. A persona must not show another OS's families,
// whatever the host has installed (040's per-OS allowlists).
const FOREIGN_FONTS = ['Segoe UI', 'Calibri', 'Helvetica Neue', 'Hiragino Sans', 'DejaVu Sans',
                       'Liberation Sans', 'Cantarell', 'Ubuntu'];
function fontFamilies(list) {
  const c = document.createElement('canvas').getContext('2d');
  const w = f => { c.font = `72px ${f}`; return c.measureText('mmmmmmmmmmlli10OOwW').width; };
  const out = {};
  for (const f of list)
    out[f] = ['monospace', 'serif', 'sans-serif'].some(b => w(`"${f}", ${b}`) !== w(b));
  return out;
}

// Two APIs answer "can this codec play": MediaSource.isTypeSupported and
// MediaCapabilities.decodingInfo (147). They must agree.
async function mediaCapsProbe() {
  const types = ['video/mp4; codecs="avc1.42E01E"', 'video/webm; codecs="vp09.00.10.08"',
                 'video/mp4; codecs="av01.0.05M.08"', 'video/mp4; codecs="hev1.1.6.L93.B0"',
                 'audio/mp4; codecs="mp4a.40.2"', 'audio/webm; codecs="opus"'];
  const out = [];
  for (const t of types) {
    const audio = t.startsWith('audio');
    const cfg = {type: 'media-source'};
    if (audio) cfg.audio = {contentType: t, channels: '2', bitrate: 128000, samplerate: 48000};
    else cfg.video = {contentType: t, width: 1280, height: 720, bitrate: 2000000, framerate: 30};
    let info = null;
    try { info = await navigator.mediaCapabilities.decodingInfo(cfg); } catch (e) {}
    out.push({type: t, mse: self.MediaSource ? MediaSource.isTypeSupported(t) : null,
              caps: info && info.supported});
  }
  return out;
}

async function waitVoices() {
  if (!self.speechSynthesis) return [];
  return await new Promise(r => {
    const done = () => r(speechSynthesis.getVoices().map(v => [v.name, v.lang, v.localService, v.default]));
    if (speechSynthesis.getVoices().length) return done();
    speechSynthesis.addEventListener('voiceschanged', done, {once: true});
    setTimeout(done, 2500);
  });
}

function nativeCheck() {
  const getters = [
    [Navigator.prototype, 'userAgent'], [Navigator.prototype, 'platform'],
    [Navigator.prototype, 'hardwareConcurrency'], [Navigator.prototype, 'webdriver'],
    [Screen.prototype, 'width'], [Navigator.prototype, 'languages'],
  ];
  const bad = [];
  for (const [proto, k] of getters) {
    const d = Object.getOwnPropertyDescriptor(proto, k);
    if (!d || !d.get) { bad.push(k + ':missing'); continue; }
    const s = Function.prototype.toString.call(d.get);
    if (!/\{\s*\[native code\]\s*\}$/.test(s)) bad.push(k);
  }
  for (const f of [HTMLCanvasElement.prototype.toDataURL, CanvasRenderingContext2D.prototype.getImageData,
                   WebGLRenderingContext.prototype.getParameter]) {
    if (!/\[native code\]/.test(Function.prototype.toString.call(f))) bad.push(f.name);
  }
  return bad;
}

// Readback round trips the way detectors run them (Clearcote audit 2026-11): a canvas exported
// with toDataURL, decoded by drawing it into a second canvas and read with getImageData, must
// return exactly what getImageData returned; WebGL readPixels likewise. 256x256 non-uniform
// content so that noise which is not idempotent shows on any seed (1013).
async function readbackRoundTrip() {
  const N = 256;
  const decode = async (url) => {
    const img = new Image();
    await new Promise((r, j) => { img.onload = r; img.onerror = j; img.src = url; });
    const c = document.createElement('canvas'); c.width = N; c.height = N;
    const g = c.getContext('2d', {willReadFrequently: true});
    g.drawImage(img, 0, 0);
    return g.getImageData(0, 0, N, N).data;
  };
  const count = (a, b) => { let n = 0; for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) n++; return n; };
  const out = {};
  const c = document.createElement('canvas'); c.width = N; c.height = N;
  const g = c.getContext('2d', {willReadFrequently: true});
  const gr = g.createLinearGradient(0, 0, N, N);
  gr.addColorStop(0, '#f60'); gr.addColorStop(1, '#09c');
  g.fillStyle = gr; g.fillRect(0, 0, N, N);
  g.fillStyle = '#123'; g.font = '30px sans-serif'; g.fillText('Cwm fjord bank', 4, 120);
  out.canvasDiffs = count(g.getImageData(0, 0, N, N).data, await decode(c.toDataURL()));
  const w = document.createElement('canvas'); w.width = N; w.height = N;
  const gl = w.getContext('webgl', {preserveDrawingBuffer: true});
  if (gl) {
    const sh = (t, src) => { const x = gl.createShader(t); gl.shaderSource(x, src); gl.compileShader(x); return x; };
    const pr = gl.createProgram();
    gl.attachShader(pr, sh(gl.VERTEX_SHADER, 'attribute vec2 p;varying vec2 v;void main(){v=p;gl_Position=vec4(p,0,1);}'));
    gl.attachShader(pr, sh(gl.FRAGMENT_SHADER, 'precision mediump float;varying vec2 v;void main(){gl_FragColor=vec4(fract(v.x*7.3),fract(v.y*3.1+v.x),0.4+0.5*sin(v.x*9.0),1);}'));
    gl.linkProgram(pr); gl.useProgram(pr);
    gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1,1,-1,-1,1,1,1]), gl.STATIC_DRAW);
    gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    const rp = new Uint8Array(N * N * 4); gl.readPixels(0, 0, N, N, gl.RGBA, gl.UNSIGNED_BYTE, rp);
    const top = new Uint8Array(rp.length);
    for (let y = 0; y < N; y++) top.set(rp.subarray((N - 1 - y) * N * 4, (N - y) * N * 4), y * N * 4);
    out.webglDiffs = count(top, await decode(w.toDataURL()));
    const M = N + 32, big = new Uint8Array(M * M * 4).fill(7);
    gl.readPixels(-16, -16, M, M, gl.RGBA, gl.UNSIGNED_BYTE, big);
    let outside = 0;
    for (let y = 0; y < M; y++) for (let x = 0; x < M; x++) {
      if (x >= 16 && x < N + 16 && y >= 16 && y < N + 16) continue;
      for (let k = 0; k < 4; k++) if (big[(y * M + x) * 4 + k] !== 7) { outside++; break; }
    }
    out.readPixelsOutside = outside;
  }
  return out;
}

// Text rendering the way detectors read it (1014-1017): font-size stepping, the anti-aliasing
// ladder of canvas text, regional-indicator pairs, local() of a persona font, and the device
// pixel size of a 100 px box.
async function textProbe() {
  const out = {};
  const c = document.createElement('canvas').getContext('2d');
  let prev = null, changed = 0;
  for (let i = 0; i < 200; i++) {
    c.font = `${(16 + i * 0.01).toFixed(2)}px Arial`;
    const w = c.measureText('The quick brown fox jumps over the lazy dog').width;
    if (prev !== null && w !== prev) changed++;
    prev = w;
  }
  out.stepChanges = changed;
  // The audit's text-scaler probe: a DOM span, font-size 20.01..22.00 px in 0.01 px steps.
  {
    const e = document.createElement('span');
    e.textContent = 'The quick brown fox jumps over the lazy dog 0123456789';
    e.style.cssText = 'position:absolute;left:-9999px;top:0;visibility:hidden;white-space:nowrap;font-family:sans-serif';
    document.body.appendChild(e);
    const m = t => { e.style.fontSize = t + 'px'; return e.getBoundingClientRect().width; };
    let n = 0, o = m(20);
    for (let a = 2001; a <= 2200; a++) { const t = m(a / 100); if (t !== o) n++; o = t; }
    e.remove();
    out.domStepChanges = n;
  }
  const t = document.createElement('canvas'); t.width = 300; t.height = 60;
  const g = t.getContext('2d', {willReadFrequently: true});
  g.fillStyle = '#000'; g.font = '28px Arial'; g.fillText('Hamburgefonstiv 0123', 4, 40);
  const d = g.getImageData(0, 0, 300, 60).data, levels = new Set();
  for (let i = 3; i < d.length; i += 4) if (d[i] > 0 && d[i] < 255) levels.add(d[i]);
  out.aaLevels = levels.size;
  const m = s => { c.font = '100px sans-serif'; return c.measureText(s).width; };
  out.flagRatio = m('\u{1F1FA}\u{1F1F8}') / (m('\u{1F1FA}') + m('\u{1F1F8}'));
  try {
    const f = new FontFace('cc-local-probe', 'local("Calibri")');
    await f.load(); out.localCalibri = true;
  } catch (e) { out.localCalibri = false; }
  await new Promise(r => {
    const b = document.createElement('div');
    b.style.cssText = 'width:100px;height:100px;position:absolute;top:0;left:0';
    document.body.appendChild(b);
    const ro = new ResizeObserver(es => {
      const e = es[0];
      out.devicePixelBox = e.devicePixelContentBoxSize ? e.devicePixelContentBoxSize[0].inlineSize : null;
      ro.disconnect(); b.remove(); r();
    });
    ro.observe(b, {box: 'device-pixel-content-box'});
    setTimeout(r, 1500);
  });
  out.dpr = devicePixelRatio;
  return out;
}

async function collectMain(workerUrl) {
  const o = await collectCommon();
  o.screen = {width: screen.width, height: screen.height, availWidth: screen.availWidth,
              availHeight: screen.availHeight, colorDepth: screen.colorDepth,
              pixelDepth: screen.pixelDepth, dpr: devicePixelRatio,
              outerWidth: outerWidth, outerHeight: outerHeight,
              innerWidth: innerWidth, innerHeight: innerHeight,
              orientation: screen.orientation && screen.orientation.type};
  const mq = q => matchMedia(q).matches;
  o.media = {
    deviceWidth: mq(`(device-width: ${screen.width}px)`),
    deviceHeight: mq(`(device-height: ${screen.height}px)`),
    resolution: mq(`(resolution: ${devicePixelRatio}dppx)`),
    pointerFine: mq('(pointer: fine)'), pointerCoarse: mq('(pointer: coarse)'),
    hover: mq('(hover: hover)'), anyPointerFine: mq('(any-pointer: fine)'),
    colorDepth: mq(`(color: ${screen.colorDepth / 3})`),
  };
  o.colorSchemeDark = mq('(prefers-color-scheme: dark)');
  o.scrollbar = (() => {
    const d = document.createElement('div');
    d.style.cssText = 'width:100px;height:100px;overflow:scroll;position:absolute;top:-999px';
    document.body.appendChild(d);
    const w = d.offsetWidth - d.clientWidth; d.remove(); return w;
  })();
  o.mediaCaps = await mediaCapsProbe();
  o.fontFamilies = fontFamilies(FOREIGN_FONTS);
  o.apis = {eyeDropper: 'EyeDropper' in window, barcode: 'BarcodeDetector' in window,
            bluetooth: 'bluetooth' in navigator, wco: 'windowControlsOverlay' in navigator,
            managed: 'managed' in navigator, docPip: 'documentPictureInPicture' in window,
            hid: 'hid' in navigator,
            activeText: (() => { const d = document.createElement('i'); d.style.color = 'ActiveText';
              document.body.appendChild(d); const c = getComputedStyle(d).color; d.remove(); return c; })(),
            orientation: screen.orientation && screen.orientation.type,
            // 1023: the surface Google's variations seed gives every Chrome.
            seedRetired: ['browsingTopics' in document, 'fence' in window,
                          typeof HTMLFencedFrameElement !== 'undefined',
                          'attributionSrc' in HTMLAnchorElement.prototype,
                          'setAttributionReporting' in XMLHttpRequest.prototype].filter(Boolean).length,
            gpc: navigator.globalPrivacyControl,
            hrefTranslate: typeof document.createElement('a').hrefTranslate};
  // 1024: "native" Blob line endings are the claimed OS's.
  o.blobNative = await new Blob(['a\nb\rc\r\nd'], {endings: 'native'}).text();
  o.maxTouchPoints = navigator.maxTouchPoints;
  o.cpuPerformance = navigator.cpuPerformance;
  o.jsHeapSizeLimit = performance.memory && performance.memory.jsHeapSizeLimit;
  try { o.quota = (await navigator.storage.estimate()).quota; } catch (e) { o.quotaError = String(e); }
  o.canvas = canvasProbe();
  o.canvasAgain = canvasProbe();
  o.alphaGrey = alphaGreyProbe();
  o.readPixels = readPixelsSentinel();
  try { o.roundTrip = await readbackRoundTrip(); } catch (e) { o.roundTripError = String(e); }
  try { o.text = await textProbe(); } catch (e) { o.textError = String(e); }
  o.audio = await audioProbe();
  try {
    if (navigator.gpu) {
      const a = await navigator.gpu.requestAdapter();
      o.webgpu = a ? {vendor: a.info.vendor, architecture: a.info.architecture,
                      device: a.info.device, description: a.info.description,
                      maxTextureDimension2D: a.limits.maxTextureDimension2D,
                      preferredFormat: navigator.gpu.getPreferredCanvasFormat()} : null;
    }
  } catch (e) { o.webgpuError = String(e); }
  o.voices = await waitVoices();
  try { o.devices = (await navigator.mediaDevices.enumerateDevices()).map(d => [d.kind, !!d.deviceId, d.label]); }
  catch (e) { o.devicesError = String(e); }
  try { const b = await navigator.getBattery(); o.battery = {charging: b.charging, level: b.level,
        chargingTime: b.chargingTime, dischargingTime: b.dischargingTime}; }
  catch (e) { o.batteryError = String(e); }
  try { const m = await navigator.keyboard.getLayoutMap(); o.keyboard = {size: m.size, KeyQ: m.get('KeyQ')}; }
  catch (e) { o.keyboardError = String(e); }
  o.webrtc = await webrtcProbe();
  o.tanh = Math.tanh(0.123456789);
  try { o.urlC = new URL('C:/').protocol; } catch (e) { o.urlC = 'throws'; }
  o.native = nativeCheck();
  o.iframe = await new Promise(r => {
    const f = document.createElement('iframe'); f.src = '/frame';
    window.addEventListener('message', e => r(e.data), {once: true});
    setTimeout(() => r(null), 8000);
    document.body.appendChild(f);
  });
  o.worker = await new Promise(r => {
    const w = new Worker(workerUrl);
    w.onmessage = e => { r(e.data); w.terminate(); };
    w.onerror = e => r({error: String(e.message)});
    setTimeout(() => r(null), 8000);
  });
  return o;
}
"""

FRAME_HTML = ("<!doctype html><script>" + COMMON_JS +
              "collectCommon().then(o => parent.postMessage(o, '*'));</script>")
WORKER_JS = COMMON_JS + "collectCommon().then(o => postMessage(o));"

# Phone behaviour (992): touch feature detection, a few deviceorientation / devicemotion
# events, which handset-only and desktop-only families render, and listeners that record
# what a mouse click turns into.
# A page that asks for the device width, as nearly every mobile page does.
VIEWPORT_HTML = ('<!doctype html><meta name="viewport" content="width=device-width, '
                 'initial-scale=1"><title>vp</title><body>')
VIEWPORT_JS = """({inner: innerWidth, client: document.documentElement.clientWidth,
                   screen: screen.width, outer: outerWidth,
                   scale: visualViewport && visualViewport.scale})"""

MOBILE_JS = r"""
(async () => {
  const o = {touchProp: 'ontouchstart' in window};
  const once = type => new Promise(r => {
    const h = e => { window.removeEventListener(type, h); r(e); };
    window.addEventListener(type, h);
    setTimeout(() => { window.removeEventListener(type, h); r(null); }, 2000);
  });
  const take = async (type, n, pick) => {
    const out = [];
    for (let i = 0; i < n; i++) {
      const e = await once(type);
      if (!e) break;
      out.push(pick(e));
      await new Promise(r => setTimeout(r, 120));
    }
    return out;
  };
  o.orientation = await take('deviceorientation', 3, e => [e.alpha, e.beta, e.gamma]);
  o.motion = await take('devicemotion', 3, e => {
    const g = e.accelerationIncludingGravity, r = e.rotationRate;
    return {g: g && [g.x, g.y, g.z], r: r && [r.alpha, r.beta, r.gamma], interval: e.interval};
  });
  const c = document.createElement('canvas').getContext('2d');
  const w = f => { c.font = `72px ${f}`; return c.measureText('mmmmmmmmmmlli10OOwW').width; };
  const has = f => ['monospace', 'serif', 'sans-serif'].some(b => w(`"${f}", ${b}`) !== w(b));
  o.fonts = {};
  for (const f of ['sans-serif-condensed', 'Droid Sans Mono', 'Segoe UI', 'Helvetica Neue',
                   'Calibri'])
    o.fonts[f] = has(f);
  window.__input = [];
  for (const t of ['touchstart', 'touchend', 'pointerdown', 'mousedown', 'click'])
    window.addEventListener(t, e => window.__input.push(t + ':' + (e.pointerType || '')), true);
  return o;
})()
"""


class Server:
    """Serves the probe pages and records each request's headers."""

    def __init__(self):
        self.requests: list[dict] = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                outer.requests.append({"path": self.path, "host": self.headers.get("Host"),
                                       "headers": {k.lower(): v for k, v in self.headers.items()}})
                if self.path.startswith("/worker.js"):
                    body, ctype = WORKER_JS, "text/javascript"
                elif self.path.startswith("/frame"):
                    body, ctype = FRAME_HTML, "text/html"
                elif self.path.startswith("/vp"):
                    body, ctype = VIEWPORT_HTML, "text/html"
                else:
                    body, ctype = "<!doctype html><title>camchrome self-test</title><body>", "text/html"
                data = body.encode()
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Accept-CH", ACCEPT_CH)
                self.send_header("Critical-CH", "Sec-CH-UA-Full-Version-List, Sec-CH-DPR, RTT, "
                                               "Sec-CH-Prefers-Color-Scheme")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_port
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def last(self, host_prefix: str, path: str = "/") -> dict:
        for r in reversed(self.requests):
            if r["path"] == path and (r["host"] or "").startswith(host_prefix):
                return r["headers"]
        return {}

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


# Two registrable domains, both mapped to the local server. Per-site noise is keyed by eTLD+1, and
# 127.0.0.1 / localhost have none (an IP literal, and a name outside the public suffix list), so
# they share one seed by design and cannot show the per-site difference.
SITE_HOSTS = ("alpha.example.com", "beta.example.org")
SITE_RULES = "--host-resolver-rules=MAP alpha.example.com 127.0.0.1, MAP beta.example.org 127.0.0.1"
CANVAS_JS = """(() => { const c = document.createElement('canvas');
  c.width = 200; c.height = 50; const x = c.getContext('2d');
  x.font = '20px sans-serif'; x.fillStyle = '#f60'; x.fillRect(10, 5, 80, 30);
  x.fillStyle = '#069'; x.fillText('camchrome \u2713', 4, 30);
  return c.toDataURL(); })()"""


def canvas_per_site(binary: str, flags: list[str], headless: bool) -> list[str]:
    """One canvas export on each of two registrable domains, in one launch."""
    server = Server()
    browser = Browser(binary, flags=flags + [SITE_RULES], headless=headless)
    out = []
    try:
        cdp = browser.open_page()
        cdp.call("Page.enable")
        for host in SITE_HOSTS:
            cdp.call("Page.navigate", {"url": f"http://{host}:{server.port}/"})
            cdp.wait_for("document.readyState === 'complete' && location.hostname === %s"
                         % json.dumps(host), timeout=30)
            out.append(cdp.evaluate(CANVAS_JS))
        cdp.close()
    finally:
        browser.stop()
        server.stop()
    return out


def collect(binary: str, flags: list[str], headless: bool, origins: list[str]) -> dict:
    """One browser launch; returns the probe result for each origin plus server headers."""
    server = Server()
    browser = cdp = None
    out: dict = {}
    try:
        browser = Browser(binary, flags=flags, headless=headless)
        out["version"] = browser.version
        cdp = browser.open_page()
        cdp.call("Page.enable")
        # Geolocation needs a grant; give it through CDP rather than a prompt.
        for host in origins:
            origin = f"http://{host}:{server.port}"
            try:
                cdp.call("Browser.grantPermissions",
                         {"origin": origin, "permissions": ["geolocation"]})
            except RuntimeError:
                pass
            cdp.call("Page.navigate", {"url": origin + "/"})
            cdp.wait_for("document.readyState === 'complete'", timeout=30)
            result = cdp.evaluate(MAIN_JS + f"collectMain({json.dumps(origin + '/worker.js')})")
            result["geo"] = cdp.evaluate(
                "new Promise(r => navigator.geolocation.getCurrentPosition("
                "p => r([p.coords.latitude, p.coords.longitude]), e => r({error: e.message}),"
                "{timeout: 5000}))")
            result["mobile"] = cdp.evaluate(MOBILE_JS)
            result["no_meta_viewport"] = cdp.evaluate(VIEWPORT_JS)
            # Away from the top-left corner, where collectMain left its probe iframe.
            for kind in ("mousePressed", "mouseReleased"):
                cdp.call("Input.dispatchMouseEvent", {"type": kind, "x": 600, "y": 450,
                                                      "button": "left", "clickCount": 1})
            result["mobile"]["input"] = cdp.evaluate(
                "new Promise(r => setTimeout(() => r(window.__input), 400))")
            result["headers"] = server.last(host)
            cdp.call("Page.navigate", {"url": origin + "/vp"})
            cdp.wait_for("document.title === 'vp'", timeout=30)
            result["meta_viewport"] = cdp.evaluate(VIEWPORT_JS)
            out[host] = result
    finally:
        if cdp:
            cdp.close()
        if browser:
            browser.stop()
        server.stop()
    return out


@dataclass
class Check:
    id: str
    patches: str
    status: str  # PASS / FAIL / SKIP / XFAIL
    detail: str = ""


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)
    persona_only: set[str] = field(default_factory=set)
    stock: bool = False

    def add(self, cid, patches, ok, detail="", skip=False, persona=False):
        if skip:
            status = "SKIP"
        elif ok:
            status = "PASS"
        elif self.stock and persona:
            status = "XFAIL"
        else:
            status = "FAIL"
        self.checks.append(Check(cid, patches, status, str(detail)[:300]))


def major(v: str | None) -> str | None:
    m = re.match(r"(\d+)", v or "")
    return m.group(1) if m else None


def private_ip(token: str) -> bool:
    try:
        ip = ipaddress.ip_address(token)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local


def evaluate(a: dict, a_again: dict, b: dict, args, validator: tuple[int, str] | None) -> Report:
    rep = Report(stock=args.stock)
    h1, h2 = "127.0.0.1", "localhost"
    m, m2 = a[h1], a[h2]
    hdr = m["headers"]
    engine = a["version"].get("Browser", "")  # "Chrome/157.0.8103.78" or "HeadlessChrome/..."
    engine_major = major(engine.split("/")[-1])
    ua_major = major(re.search(r"Chrome/([\d.]+)", m["userAgent"]).group(1)) \
        if re.search(r"Chrome/([\d.]+)", m["userAgent"]) else None
    ud = m.get("uaData") or {}
    brand_majors = {b["brand"]: b["version"] for b in ud.get("brands", [])}
    full = {b["brand"]: b["version"] for b in ud.get("fullVersionList", [])}

    # --- identity -------------------------------------------------------------
    rep.add("webdriver-false", "010", m["webdriver"] is False, m["webdriver"])
    rep.add("ua-no-headless", "010 120", "Headless" not in m["userAgent"], m["userAgent"],
            persona=True)
    rep.add("ua-major-equals-engine", "987", ua_major == engine_major,
            f"UA {ua_major} engine {engine_major}")
    rep.add("uach-brand-equals-ua", "010 987",
            any(major(v) == ua_major for k, v in brand_majors.items() if "Brand" not in k),
            brand_majors)
    rep.add("uach-full-version-equals-engine", "977 987",
            any(v.split(".")[:1] == [engine_major] for v in full.values()) if full else False,
            full, persona=False)
    rep.add("header-ua-equals-js", "010", hdr.get("user-agent") == m["userAgent"],
            hdr.get("user-agent"))
    plat_hdr = (hdr.get("sec-ch-ua-platform") or "").strip('"')
    rep.add("header-platform-equals-uadata", "010", plat_hdr == ud.get("platform"),
            f"{plat_hdr} vs {ud.get('platform')}")
    plat_ok = {"Windows": "Win32", "macOS": "MacIntel", "Linux": "Linux", "Android": "Linux"}
    want = plat_ok.get(ud.get("platform", ""), "")
    rep.add("navigator-platform-coherent", "010", m["platform"].startswith(want) if want else False,
            f"{m['platform']} for {ud.get('platform')}")
    if args.platform not in ("auto", None) and not args.stock:
        expect = {"windows": "Windows", "macos": "macOS", "linux": "Linux",
                  "android": "Android"}[args.platform]
        rep.add("persona-platform-honoured", "002 010", ud.get("platform") == expect,
                ud.get("platform"))
    fvl = hdr.get("sec-ch-ua-full-version-list", "")
    rep.add("header-full-version-list-sent", "010 977", bool(fvl), fvl or "not sent",
            skip=not hdr)

    # --- realms -----------------------------------------------------------------
    for realm in ("iframe", "worker"):
        r = m.get(realm) or {}
        keys = ["userAgent", "platform", "hardwareConcurrency", "deviceMemory", "language",
                "timezone"]
        diff = {k: (m.get(k), r.get(k)) for k in keys if m.get(k) != r.get(k)}
        rep.add(f"{realm}-identity-matches-window", "002", r and not diff, diff or "")
        if r.get("webgl") and m.get("webgl"):
            rep.add(f"{realm}-webgl-matches-window", "070", r["webgl"] == m["webgl"],
                    (m["webgl"], r["webgl"]))
    rep.add("webgl-session-constant-across-sites", "070",
            m.get("webgl") == m2.get("webgl"), (m.get("webgl"), m2.get("webgl")))

    # --- locale / timezone --------------------------------------------------------
    al = (hdr.get("accept-language") or "").split(",")[0].split(";")[0]
    rep.add("accept-language-equals-navigator", "092", al == m["language"],
            f"{al} vs {m['language']}")
    rep.add("intl-locale-equals-navigator", "092",
            m["intlLocale"].split("-")[0] == m["language"].split("-")[0],
            f"{m['intlLocale']} vs {m['language']}")
    if args.timezone:
        rep.add("timezone-pinned", "090", m["timezone"] == args.timezone, m["timezone"])

    # --- hardware -----------------------------------------------------------------
    dm = m.get("deviceMemory")
    rep.add("device-memory-on-chrome-grid", "030",
            dm in (0.25, 0.5, 1, 2, 4, 8, 16, 32), dm)
    heap = m.get("jsHeapSizeLimit") or 0
    rep.add("js-heap-limit-plausible", "146 980", 1 << 30 <= heap <= 1 << 33, heap)
    rep.add("storage-quota-present", "145", (m.get("quota") or 0) > 0, m.get("quota"))
    if args.cores:
        rep.add("cores-override", "030", m["hardwareConcurrency"] == args.cores,
                m["hardwareConcurrency"], persona=True)

    # --- screen -------------------------------------------------------------------
    s, mq = m["screen"], m["media"]
    rep.add("avail-within-screen", "140",
            s["availWidth"] <= s["width"] and s["availHeight"] <= s["height"], s)
    rep.add("window-fits-screen", "972 975",
            s["outerWidth"] <= s["width"] and s["outerHeight"] <= s["height"], s, persona=True)
    rep.add("mq-device-size-equals-screen", "141", mq["deviceWidth"] and mq["deviceHeight"], mq)
    rep.add("mq-resolution-equals-dpr", "962", mq["resolution"], s["dpr"])
    rep.add("color-depth-24-or-30", "140", s["colorDepth"] in (24, 30), s["colorDepth"])
    # Sec-CH-Prefers-Color-Scheme must say what matchMedia says.
    pcs = (hdr.get("sec-ch-prefers-color-scheme") or "").strip('"')
    rep.add("color-scheme-header-equals-media", "141",
            pcs == ("dark" if m.get("colorSchemeDark") else "light"),
            f"{pcs or 'not sent'} vs dark={m.get('colorSchemeDark')}", skip=not pcs)
    # Classic scrollbars take layout width on Windows and Linux; Android uses overlay
    # scrollbars (0). A cross-OS persona that keeps the host's scrollbar shows it here
    # (e.g. a Windows persona on a Mac host with overlay scrollbars reports 0).
    sb = m.get("scrollbar")
    if args.platform in ("windows", "linux", "android") and not args.stock:
        want_sb = (lambda w: w == 0) if args.platform == "android" else (lambda w: (w or 0) > 0)
        rep.add("scrollbar-width-matches-persona-os", "999",
                sb is not None and want_sb(sb), f"{sb}px for a {args.platform} persona")
    touch = m.get("maxTouchPoints", 0)
    if touch == 0:
        rep.add("no-touch-means-fine-pointer", "141 142",
                mq["pointerFine"] and mq["hover"] and not mq["pointerCoarse"], mq, persona=True)
    dpr_hdr = hdr.get("sec-ch-dpr") or hdr.get("dpr")
    rep.add("sec-ch-dpr-equals-dpr", "961",
            dpr_hdr is not None and abs(float(dpr_hdr) - s["dpr"]) < 1e-6,
            f"{dpr_hdr} vs {s['dpr']}", skip=dpr_hdr is None and args.stock)

    # --- render -------------------------------------------------------------------
    rep.add("canvas-stable-within-page", "001 060",
            m["canvas"]["hash"] == m["canvasAgain"]["hash"], "")
    rep.add("canvas-same-seed-same-site-stable", "001 060",
            m["canvas"]["hash"] == a_again[h1]["canvas"]["hash"], "relaunch with the same seed")
    sites = a.get("canvas_sites") or ["", ""]
    rep.add("canvas-differs-per-site", "001 060", sites[0] != sites[1],
            " vs ".join(SITE_HOSTS), persona=True)
    rep.add("canvas-differs-per-seed", "001 060",
            m["canvas"]["hash"] != b[h1]["canvas"]["hash"], "seed A vs seed B", persona=True)
    rep.add("measuretext-nonzero", "060", m["canvas"]["measure"] > 50, m["canvas"]["measure"])
    rep.add("canvas-alpha-zero-reads-black", "986", m["alphaGrey"]["alphaZeroNotBlack"] == 0,
            m["alphaGrey"])
    rep.add("canvas-grey-stays-grey", "986", m["alphaGrey"]["greyTinted"] == 0, m["alphaGrey"])
    tx = m.get("text") or {}
    if args.platform == "windows" and not args.stock:
        # DirectWrite changes the width on nearly every 0.01 px size step (FreeType/Fontations
        # at a 1/64 px size grid: ~64%); the audit reads >= 90% as Windows (1021).
        rep.add("windows-text-steps-like-directwrite", "1014 1021",
                (tx.get("domStepChanges") or 0) >= 180, tx.get("domStepChanges"))
        rep.add("windows-canvas-text-aa-ladder", "1015",
                0 < (tx.get("aaLevels") or 0) <= 20, tx.get("aaLevels"))
        rep.add("windows-no-flag-ligature", "1016",
                0.9 <= (tx.get("flagRatio") or 0) <= 1.1, tx.get("flagRatio"))
        # Needs the clone fonts (scripts/install-persona-fonts.sh); skipped without Carlito.
        rep.add("windows-local-font-loads", "1014", tx.get("localCalibri") is True,
                tx.get("localCalibri"), skip=not args.persona_fonts)
    if args.platform == "linux" and not args.stock:
        rep.add("linux-text-steps-like-freetype", "1021",
                100 <= (tx.get("domStepChanges") or 0) <= 160, tx.get("domStepChanges"))
    if args.platform == "macos" and not args.stock and tx.get("devicePixelBox"):
        rep.add("macos-raster-scale-is-dpr", "1025",
                abs(tx["devicePixelBox"] - 100 * tx["dpr"]) <= 1, tx)
        rep.add("macos-text-steps-like-coretext", "1025",
                (tx.get("domStepChanges") or 0) >= 180, tx.get("domStepChanges"))
    if args.platform == "android" and not args.stock and tx.get("devicePixelBox"):
        rep.add("android-raster-scale-is-dpr", "1017",
                abs(tx["devicePixelBox"] - 100 * tx["dpr"]) <= 1, tx)
    rt = m.get("roundTrip") or {}
    rep.add("canvas-todataurl-roundtrip-equals-getimagedata", "1013",
            rt.get("canvasDiffs") == 0, rt, skip=not rt)
    rep.add("webgl-readpixels-equals-todataurl-roundtrip", "1013",
            rt.get("webglDiffs") == 0, rt, skip="webglDiffs" not in rt)
    rep.add("readpixels-oversize-leaves-outside-untouched", "1013",
            rt.get("readPixelsOutside") == 0, rt, skip="readPixelsOutside" not in rt)
    rp = m.get("readPixels")
    rep.add("readpixels-sentinel-untouched", "988", rp is not None and rp["sentinelTouched"] == 0,
            rp, skip=rp is None)
    wg, gpu = m.get("webgl") or {}, m.get("webgpu")
    # Every real visitor has WebGL. Since Chrome 137 a host without a usable GPU has none unless
    # SwiftShader is opted into; a persona opts in (1000).
    if not args.stock:
        rep.add("persona-has-webgl", "1000", bool(wg.get("renderer")),
                wg or m.get("webglError") or "getContext('webgl') returned null")
    if gpu and wg:
        ren = (wg.get("renderer") or "").lower()
        vend = (gpu.get("vendor") or "").lower()
        coherent = bool(vend) and (vend in ren or (vend == "apple" and "apple" in ren)
                                   or (vend == "nvidia" and "nvidia" in ren))
        rep.add("webgpu-vendor-matches-webgl", "075 983 987", coherent, (wg, gpu), persona=True)
    else:
        rep.add("webgpu-vendor-matches-webgl", "075", True, "no WebGPU adapter", skip=True)
    if args.platform == "windows" and gpu:
        rep.add("webgpu-preferred-format-windows", "970", gpu.get("preferredFormat") == "bgra8unorm",
                gpu.get("preferredFormat"))

    # --- audio --------------------------------------------------------------------
    au, au2, aub = m["audio"], a_again[h1]["audio"], b[h1]["audio"]
    rep.add("audio-same-seed-stable", "020", au.get("sum") == au2.get("sum"),
            (au.get("sum"), au2.get("sum")), skip="error" in au)
    rep.add("audio-sample-rate-plausible", "020 981", au.get("sampleRate") in (44100, 48000),
            au.get("sampleRate"), skip="error" in au)

    caps = m.get("mediaCaps") or []
    disagree = [c["type"] for c in caps if c.get("mse") is not None and c.get("caps") is not None
                and c["mse"] != c["caps"]]
    rep.add("media-capabilities-agree-with-mse", "147", caps and not disagree,
            disagree or caps, skip=not caps)

    # --- fonts (040) ----------------------------------------------------------------
    foreign = {"windows": ["Helvetica Neue", "Hiragino Sans", "DejaVu Sans", "Liberation Sans",
                           "Cantarell", "Ubuntu"],
               # Not Calibri: Linux's metric aliases map it to Carlito, so a real Linux shows it.
               "linux": ["Segoe UI", "Helvetica Neue", "Hiragino Sans"],
               "macos": ["Segoe UI", "DejaVu Sans", "Liberation Sans", "Cantarell", "Ubuntu"]}
    if args.platform in foreign and not args.stock:
        fams = m.get("fontFamilies") or {}
        shown = [f for f in foreign[args.platform] if fams.get(f)]
        rep.add("fonts-no-foreign-os-families", "040", not shown,
                f"{args.platform} persona shows {shown}" if shown else "")

    # --- devices ------------------------------------------------------------------
    # A Linux persona reports getVoices() == [] by design (Chrome on Linux without
    # speech-dispatcher; persona_profile.cc, and persona_validator.cc only requires voices for
    # Windows and macOS). Every other persona must have voices.
    n_voices = len(m.get("voices") or [])
    linux_persona = ud.get("platform") == "Linux"
    rep.add("voices-not-empty", "170", n_voices > 0 or linux_persona,
            f"{n_voices}" + (" (Linux persona: empty by design)" if linux_persona and not n_voices
                             else ""), persona=True)
    kinds = {d[0] for d in m.get("devices") or []}
    rep.add("media-devices-present", "148", {"audioinput", "audiooutput"} <= kinds, kinds,
            persona=True)
    rep.add("media-device-labels-empty", "148",
            all(d[2] == "" for d in m.get("devices") or []), "")
    bat = m.get("battery") or {}
    rep.add("battery-plausible", "150 979", 0 <= bat.get("level", -1) <= 1, bat,
            skip="batteryError" in m)
    kb = m.get("keyboard") or {}
    apis = m.get("apis") or {}
    if args.platform == "android" and not args.stock:
        desktop_only = [k for k in ("eyeDropper", "wco", "managed", "docPip", "hid") if apis.get(k)]
        rep.add("android-api-surface", "1008",
                not desktop_only and apis.get("barcode") and apis.get("bluetooth"),
                desktop_only or apis)
    if args.platform in ("windows", "macos") and not args.stock:
        rep.add("web-bluetooth-present", "1007", apis.get("bluetooth") is True, apis.get("bluetooth"))
    if args.platform == "linux" and not args.stock:
        rep.add("web-bluetooth-absent-on-linux", "1007", apis.get("bluetooth") is False,
                apis.get("bluetooth"))
    if args.platform == "windows" and not args.stock:
        rep.add("activetext-is-windows-hotlight", "1007",
                apis.get("activeText") == "rgb(0, 102, 204)", apis.get("activeText"))
    # A Chromium without Google's variations seed (the stock binary CI runs) shows all three;
    # only the patch fixes them, so they are expected failures under --stock.
    rep.add("no-seed-retired-ad-apis", "1023", apis.get("seedRetired") == 0, apis.get("seedRetired"),
            persona=True)
    rep.add("global-privacy-control-exposed", "1023", apis.get("gpc") is False, apis.get("gpc"),
            persona=True)
    rep.add("href-translate-exposed", "1023", apis.get("hrefTranslate") == "string",
            apis.get("hrefTranslate"), persona=True)
    want_eol = "\r\n" if args.platform == "windows" and not args.stock else "\n"
    rep.add("blob-native-endings-follow-os", "1024",
            m.get("blobNative") == want_eol.join("abcd"), m.get("blobNative"))
    if args.platform in ("windows", "linux", "macos") and not args.stock:
        rep.add("desktop-orientation-from-persona-screen", "1009",
                apis.get("orientation") == ("landscape-primary" if s["width"] >= s["height"]
                                            else "portrait-primary"),
                (apis.get("orientation"), s["width"], s["height"]))
    if args.platform == "android" and not args.stock:
        # Chrome for Android has no navigator.keyboard (1008).
        rep.add("android-no-keyboard-api", "1008", "keyboardError" in m,
                m.get("keyboardError") or kb)
    else:
        rep.add("keyboard-layout-map", "150", kb.get("size", 0) > 0, kb,
                skip="keyboardError" in m)
    conn = m.get("connection") or {}
    rtt_hdr = hdr.get("rtt")
    rep.add("rtt-header-equals-connection", "989",
            rtt_hdr is not None and int(rtt_hdr) == conn.get("rtt"),
            f"{rtt_hdr} vs {conn.get('rtt')}", skip=rtt_hdr is None and args.stock,
            persona=True)  # stock Chrome's wire and JS estimates can differ; 989 aligns them
    dl_hdr = hdr.get("downlink")
    rep.add("downlink-header-equals-connection", "989",
            dl_hdr is not None and float(dl_hdr) == conn.get("downlink"),
            f"{dl_hdr} vs {conn.get('downlink')}", skip=dl_hdr is None and args.stock,
            persona=True)

    # --- WebRTC ------------------------------------------------------------------
    rtc = m.get("webrtc") or {}
    leaked = []
    for c in (rtc.get("candidates") or []) + (rtc.get("sdpCandidates") or []):
        parts = c.split()
        if len(parts) > 4 and private_ip(parts[4]):
            leaked.append(parts[4])
    rep.add("webrtc-no-private-ip", "100", not leaked, sorted(set(leaked)), skip=not rtc,
            persona=True)

    # --- geolocation -------------------------------------------------------------
    if args.location:
        lat, lng = (float(v) for v in args.location.split(","))
        g = m.get("geo")
        ok = isinstance(g, list) and abs(g[0] - lat) < 1e-3 and abs(g[1] - lng) < 1e-3
        rep.add("geolocation-from-flag", "180", ok, g)

    # --- OS oracles -------------------------------------------------------------
    if args.platform == "windows":
        rep.add("url-drive-letter-is-file", "160", m.get("urlC") == "file:", m.get("urlC"))

    # --- phone behaviour (992) ---------------------------------------------------
    mob = m.get("mobile") or {}
    clicks = mob.get("input") or []
    if args.platform == "android" and not args.stock:
        rep.add("android-touch-feature-detected", "002", mob.get("touchProp") is True,
                mob.get("touchProp"))
        rep.add("android-mouse-becomes-touch", "992",
                "touchstart:" in clicks and "pointerdown:touch" in clicks, clicks)
        o = mob.get("orientation") or []
        rep.add("android-orientation-streams", "992",
                len(o) >= 2 and all(v is not None for v in o[0])
                and 20 <= (o[0][1] or 0) <= 80, o)
        mo = mob.get("motion") or []
        g = (mo[0].get("g") if mo else None) or [0, 0, 0]
        norm = sum(v * v for v in g if v is not None) ** 0.5
        rep.add("android-motion-has-gravity", "992", 9.3 <= norm <= 10.3, mo[:1])
        # 997: the content area is the phone's width, so a width=device-width page lays out
        # at exactly screen.width (it was 500 px, Chrome's desktop window minimum, next to a
        # 360-430 px persona screen); a page without the tag gets Android's 980 px default.
        vp = m.get("meta_viewport") or {}
        rep.add("android-device-width-equals-screen", "975 997",
                vp.get("inner") == vp.get("screen") == vp.get("client"), vp)
        nvp = m.get("no_meta_viewport") or {}
        rep.add("android-no-meta-layout-980", "002 997", nvp.get("client") == 980, nvp)
        # A phone shows a page without a viewport tag zoomed out to fit: the visual viewport
        # covers the 980 px layout (innerWidth 980, scale = screen / 980), not one screen's worth.
        rep.add("android-no-meta-zoomed-to-fit", "1001",
                nvp.get("inner") == 980 and (nvp.get("scale") or 1) < 1, nvp)
        f = mob.get("fonts") or {}
        rep.add("android-handset-fonts", "040 992",
                f.get("sans-serif-condensed") and not f.get("Segoe UI")
                and not f.get("Helvetica Neue") and not f.get("Calibri"), f)
    else:
        rep.add("desktop-mouse-stays-mouse", "992",
                "touchstart:" not in clicks and "pointerdown:mouse" in clicks, clicks,
                skip=not clicks)

    # --- integrity --------------------------------------------------------------
    rep.add("patched-getters-are-native", "all (C++ only)", not m["native"], m["native"])

    # --- validator --------------------------------------------------------------
    if validator is None:
        rep.add("persona-validator-clean", "974", True, "not run", skip=True)
    else:
        code, text = validator
        rep.add("persona-validator-clean", "974", code == 0, text.strip()[-200:], persona=True)
    return rep


def run_validator(binary: str, count: int, timeout: int = 120) -> tuple[int, str]:
    try:
        p = subprocess.run([binary, "--no-sandbox", f"--clearcote-validate-persona={count}"],
                           capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout + p.stderr
    except subprocess.TimeoutExpired:
        return -1, "timed out (switch not recognised: the browser started instead)"


def default_binary() -> str:
    if sys.platform == "darwin":
        return str(REPO / "build/src/out/Default/Chromium.app/Contents/MacOS/Chromium")
    return str(REPO / "build/src/out/Default/chrome")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--binary", default=default_binary())
    ap.add_argument("--seed", default="selftest-a")
    ap.add_argument("--seed-b", default="selftest-b")
    ap.add_argument("--platform", default="auto",
                    choices=["auto", "windows", "macos", "linux", "android"])
    ap.add_argument("--timezone", default="America/New_York")
    ap.add_argument("--location", default="40.7128,-74.0060")
    ap.add_argument("--cores", type=int, default=None,
                    help="also pass --fingerprint-hardware-concurrency and check it")
    ap.add_argument("--extra-flag", action="append", default=[],
                    help="extra browser switch, e.g. --extra-flag=--fingerprint-schema=2")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--validator-count", type=int, default=16)
    ap.add_argument("--stock", action="store_true",
                    help="stock Chromium: no persona flags; persona-only failures are XFAIL")
    ap.add_argument("--persona-fonts", action="store_true",
                    help="the clone fonts are installed (scripts/install-persona-fonts.sh); "
                         "run the checks that need them")
    ap.add_argument("--json", default=str(REPO / "build/fingerprint-selftest.json"))
    args = ap.parse_args()

    if not Path(args.binary).exists():
        print(f"browser not found: {args.binary}", file=sys.stderr)
        return 2

    def flags(seed: str) -> list[str]:
        f = ["--window-size=1280,800", "--lang=en-US"]
        if not args.stock:
            f += [f"--fingerprint={seed}"]
            if args.platform != "auto":
                f.append(f"--fingerprint-platform={args.platform}")
            if args.timezone:
                f.append(f"--timezone={args.timezone}")
            if args.location:
                f.append(f"--fingerprint-location={args.location}")
            if args.cores:
                f.append(f"--fingerprint-hardware-concurrency={args.cores}")
        return f + args.extra_flag

    if args.stock:
        args.timezone = None
        args.location = None
    origins = ["127.0.0.1", "localhost"]
    a = collect(args.binary, flags(args.seed), args.headless, origins)
    a["canvas_sites"] = canvas_per_site(args.binary, flags(args.seed), args.headless)
    a_again = collect(args.binary, flags(args.seed), args.headless, origins[:1])
    b = collect(args.binary, flags(args.seed_b), args.headless, origins[:1])
    validator = None if args.stock else run_validator(args.binary, args.validator_count)
    rep = evaluate(a, a_again, b, args, validator)

    width = max(len(c.id) for c in rep.checks)
    print(f"browser: {a['version'].get('Browser')}  seed={args.seed}  platform={args.platform}"
          f"{'  (stock mode)' if args.stock else ''}")
    for c in rep.checks:
        line = f"{c.status:5}  {c.id:<{width}}  [{c.patches}]"
        if c.status in ("FAIL", "XFAIL") and c.detail:
            line += f"  {c.detail}"
        print(line)
    counts = {s: sum(c.status == s for c in rep.checks) for s in ("PASS", "FAIL", "XFAIL", "SKIP")}
    print("\n" + "  ".join(f"{k} {v}" for k, v in counts.items()))

    out = Path(args.json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"browser": a["version"], "args": vars(args),
                               "checks": [c.__dict__ for c in rep.checks],
                               "raw": {"a": a, "a_again": a_again, "b": b}},
                              indent=2, default=str) + "\n")
    print(f"written to {out}")
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
