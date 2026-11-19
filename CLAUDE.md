# camchrome: notes for agents

- Updating Chromium or checking upstreams: start with `docs/UPSTREAM-SOURCES.md` section 0 and
  `tools/upstream_watch.py --no-reference`. A new major shows up first as an open
  "Update to Chromium N" PR in ungoogled-software/ungoogled-chromium, before any tag.
- The acceptance bar is "indistinguishable from stock Chrome": compare against Chrome for Testing
  of the same version with `tools/render_compare.py` (desktop and Android persona vs stock and
  stock phone emulation), plus `tools/fingerprint_selftest.py` and `tools/ua_selftest.py`.
- It must also work on Linux servers with no GPU (headful under Xvfb). See README "No-GPU Linux".
- Build on Linux: `ARGS_FILE=config/args.linux.gn scripts/docker-linux-build.sh x64`. The Docker
  volume keeps the patched tree; `01-apply-patches.sh` is skipped when `.camchrome-patched`
  exists, so patches added later must be applied to the tree by hand before rebuilding.
- Patch numbering, persona design and open work: `patches/README.md`, `NOTES.md`, `COVERAGE.md`.
- Never call `ungoogled::PersonaActive()`, `CurrentPersona()` or `GetPersonaPlatform()` from code
  that runs in every process at startup (e.g. `ChromeMainDelegate::BasicStartupComplete`) without
  first checking for the browser process (`!HasSwitch(switches::kProcessType)`). They cache in a
  function-local static; the zygote has no `--fingerprint` switch, caches "no persona", and every
  renderer forked from it inherits that, silently disabling renderer-side persona code.
- Verify fixes with the Clearcote audit (`tools/stealth_audit.py --interact --platform <os>
  --out build/reports/<name>.txt`), not only the self-tests: it found regressions they missed.
