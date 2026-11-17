# Fingerprint library

`library/<os>/<id>.json` holds real-device captures in the JSON that `--fingerprint-profile`
imports, one file per device. `tools/fingerprint_library.py` captures, checks and picks them.

The library ships empty: every entry has to come from a real device, and none have been
captured yet. Generated or sampled fingerprints don't belong here, because the point of the
library is that each entry is one machine that exists.

## Adding devices

1. On a machine on the same network: `tools/fingerprint_library.py capture --bind 0.0.0.0`.
2. On each device, open `http://<that machine>:8765/` in stock Chrome (same major version as
   camchrome, no fingerprint-changing extensions) and press **Capture**.
3. The tool checks the capture (real GPU, Direct3D on Windows, Metal on macOS, OpenGL ES on
   Android, touch points on phones, deviceMemory on Chrome's grid, WebGPU vendor matching
   WebGL, ...) and saves it under `library/<os>/`, or says why it refused it.
4. `tools/fingerprint_library.py check` (also run in CI) re-checks everything.

Capture only devices you own or may capture. Media device labels and ids are dropped.
A capture taken on Linux lists fontconfig aliases (e.g. Calibri served by Carlito) as present,
which is what that machine really answers.

A useful spread, matching ShardBrowser's 220: about 120 Windows (mostly Intel and NVIDIA, some
AMD), 30 macOS (Apple Silicon), 20 Linux and 50 Android phones.

## Using an entry

```bash
tools/fingerprint_library.py list
tools/fingerprint_library.py pick --os windows --gpu nvidia --seed customer-42
# --fingerprint-platform=windows --fingerprint-profile=H4sI... --window-size=1536,816
```

The same `--seed` always picks the same entry, so a customer keeps their device. Launch with
those flags and no `--fingerprint=<seed>`: the profile is authoritative, and a seed next to a
profile is a combination strict scorers flag.
