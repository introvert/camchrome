# Stealth audit result

The acceptance test for this patch set is the public
[Clearcote fingerprint audit](https://www.clearcotelabs.com/audit): 365 coherence checks
across 16 browser subsystems.

## 2026-11-17 — macOS arm64, Chromium 157

| | |
|---|---|
| Browser | Chromium `157.0.8103.78` (ungoogled, patches 000–989) |
| Command | `tools/stealth_audit.py --seed testseed --interact` |
| Score | **100 / 100** |
| Scored checks | **236 / 236 agree** |
| Findings | **0** |
| Unscored | 117 — contextual, unknowable-by-design, or not applicable; see the audit page |

A second seed (`alpha`) and a launch without `--fingerprint-platform` (the host-platform
default) produced the same result. Without `--interact` the run scores
**218/218** and the audit reports the 18 input checks as "not attempted", never as passed.

Group results from the run:

```
Identity coherence      78/78
Automation surface      78/78
Environment signals     18/18
Render coherence        62/62
```

The 18 input checks are included because `--interact` completes the audit's five optional
input steps with trusted CDP input. The engine itself would fail six of those checks under
a naive driver (element-centre clicks, instantaneous press/release, no `PointerEvent.pressure`,
scripted `change`); the harness drives them the way a pointing device and keyboard do.

## History

The 156 macOS build also scored 100/100 (237/237) and is preserved at tag `v156-audit100`.
Before patches 987–989 it reported 16 findings (score 93): a 151 version claim on a 156
engine, Windows Direct3D GPU strings/caps under a macOS claim, malformed ANGLE device ids,
`cpuPerformance` from the host CPU model, a DPR/layout mismatch, empty voices, wire-vs-JS
downlink, a readPixels sentinel violation, a PBO/readPixels mismatch and `getParameter`
timing. See `COVERAGE.md` for the mapping and `NOTES.md` for how each was resolved.

Raw output from a run is written to `build/stealth-audit.txt` / `.json` (git-ignored).
