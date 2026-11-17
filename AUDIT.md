# Stealth audit result

The acceptance test for this patch set is the public
[Clearcote fingerprint audit](https://www.clearcotelabs.com/audit): 365 coherence checks
across 16 browser subsystems.

## 2026-11-17 — macOS arm64

| | |
|---|---|
| Browser | Chromium `156.0.8078.79` (ungoogled, patches 000–989) |
| Command | `tools/stealth_audit.py --seed testseed --interact` |
| Score | **100 / 100** |
| Scored checks | **237 / 237 agree** |
| Findings | **0** |
| Unscored | 116 — contextual, unknowable-by-design, or not applicable; see the audit page |

Group results from the run:

```
Identity coherence      78/78
Automation surface      79/79
Environment signals     18/18
Render coherence        62/62
```

The 18 input checks are included because `--interact` completes the audit's five optional
input steps with trusted CDP input. Without it the audit scores 219/219 and reports those 18
as "not attempted", never as passed.

Before patches 987–989 the same audit reported 16 findings (score 93): a 151 version claim
on a 156 engine, Windows Direct3D GPU strings/caps under a macOS claim, malformed ANGLE
device ids, `cpuPerformance` from the host CPU model, a DPR/layout mismatch, empty voices,
wire-vs-JS downlink, a readPixels sentinel violation, a PBO/readPixels mismatch and
`getParameter` timing. See `COVERAGE.md` for the mapping and `NOTES.md` for how each was
resolved.

Raw output from the run is kept locally at `build/stealth-audit.txt` / `.json`.
