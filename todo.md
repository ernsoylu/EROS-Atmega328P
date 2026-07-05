# erosgen upgrade — plan & TODO

ECU configuration + code-generation tool for the EROS RTOS (SystemDesk-style
ASW mapping ⊕ CubeMX-style peripheral generation), built by **extending**
`tools/erosgen.py` — not rebuilding it.

## Framing: extend, don't rebuild

`tools/erosgen.py` (1061 lines) already implements most of the "Smart Engine",
and its logic is already decoupled from I/O: model classes
(`Task`/`Resource`/`System`, `:157`–`:417`) → pure emitters (`System → str`,
`:423`–`:961`) → thin CLI (`main`, `:1013`). No UI yet, and the core fails fast.

### Already done
- [x] Pin/peripheral conflict resolution — `_check_pins()` pin→owner map (`:367`), non-pin hardware conflicts `CONFLICTS_HARD` (`:78`)
- [x] Rate-monotonic priority assignment (`:395`) + schedulability gate (`:329`)
- [x] Memory/flash budgeting — per-`.o` non-LTO budget + LTO image gate, real `avr-size`, pre-flash `report()` (`:726`, `:968`)
- [x] Per-`.o` compile → link Makefile strategy (`:703`–`:756`)
- [x] Project YAML single source of truth; headless CLI/CI (`--check`, `test_erosgen.py`)
- [x] ASW→task binding via `simulink.rate_map`

### Genuinely missing (the real work)
- [ ] **MCU abstraction** — 328P hardcoded across ~6 tables + emitters
- [ ] **Model-interface parsing** — tool never reads Simulink `.h` for signals/calibrations
- [ ] **RTE generation** — `rte/Rte_Cfg.h:15` already names this as the next step
- [ ] **GUI** — and the core must stop failing fast first

## Direction (CONFIRMED by two peer reviews)
Build **Phase 0 + Phase 1 headless first**. GUI before non-throwing `validate.py`
and `parse/ert.py` = a crash-prone wrapper. The `[Diagnostic]` array is exactly
the data structure the GUI's "Problems" tree will render later.

## Python tooling & dependencies (uv)

`uv` (0.9.28 present) is the standard for this project's Python env — replaces the
README's `pip install pyyaml`. `pyproject.toml` + `uv.lock` become the source of
truth for deps; land them in Phase 0 alongside the package split.

- [x] `pyproject.toml` (`requires-python >=3.9`, core=`pyyaml`, extras `[mat]`/`[gui]`, dev `pytest`, `package=false`) + `uv.lock` created
- [x] `.python-version` = 3.12 (uv resolved system CPython 3.12.3; env has pyyaml+pytest)
- Workflow: `uv sync` · `uv run python -m erosgen.cli app.yaml` · `uv run pytest` · `uv add <dep>`

**Dependency matrix — keep core PyYAML-only; everything else is an opt-in extra:**

| Group | Deps | When |
|---|---|---|
| core (runtime) | `pyyaml` | always (erosgen today) |
| `[mat]` extra | `scipy` (+`h5py` guard) | Phase 1, **only if** pulling scaling/dims from `codeInfo.mat`; header regex needs no deps |
| `[gui]` extra | `PySide6`, `ruamel.yaml` | Phase 3 (GUI + comment-preserving YAML round-trip) |
| `[dev]` | `pytest` | optional; `test_erosgen.py` also runs standalone |

**uv stays a *dev* tool — do NOT leak it into generated artifacts.** The emitted
Makefile's `config:` target must stay plain `python3 ../tools/erosgen.py app.yaml`,
not `uv run`: (1) preserves byte-exact Makefile golden, (2) keeps the
"Python-less/uv-less CI can still `make` from committed output" property.

## Target architecture

New value is only 3 files (`parse/ert.py`, `bind.py`, `emit/rte.py`); the rest is
refactor/externalize.

```
tools/erosgen.py    # KEEP as shim entrypoint (see relpath hazard) -> re-exports package
tools/erosgen/
  cli.py            # today's main() + --check
  model.py          # Task/Resource/System — construction only, no validation
  validate.py       # NEW collect_diagnostics(System) -> [Diagnostic] (non-throwing)
  diagnostics.py    # NEW Diagnostic dataclass
  mcu/{profile.py, atmega328p.yaml, atmega2560.yaml}
  parse/ert.py      # NEW header regex primary + codeInfo.mat cross-check
  bind.py           # NEW signal<->peripheral type/range compatibility
  emit/{config,makefile,skeletons,osgen}.py
  emit/rte.py       # NEW Rte_Cfg.h + Rte.c from a models: section
  backends/avr.py   # DDRx/PORTx, PROGMEM, avr-gcc idioms
gui/                # separate; imports tools.erosgen; ZERO logic
```

`Diagnostic` shape (UI-agnostic but UI-friendly — from peer review):
```python
@dataclass
class Diagnostic:
    severity: str   # "error" | "warning" | "info"
    code: str       # machine-readable, e.g. "PIN_CONFLICT", "MEM_OVERFLOW" (assert on this in tests, not message text)
    message: str    # "Pin PB5 claimed by peripheral spi and gpio LED"
    location: str   # "app.yaml:tasks[1]" / "appKnbSwt_Intfc.h:26" — lets the GUI jump to source
```

## Roadmap

### Phase 0 — Refactor, no new features (de-risks everything)
- [x] Scaffold uv: `pyproject.toml` + `uv.lock` + `.python-version`; `.gitignore` += `.venv/`
- [x] **Extend the golden-master net BEFORE splitting** — added `test_demos_makefile_golden` (reference-demo Makefile) + `test_genmain_skeleton_goldens` via `tools/fixtures/genmain/` (app.yaml + regen.py + 3 `.golden`). **All 6 emitters now pinned; 8/8 tests green** (`uv run pytest` + standalone).
- [x] Split `erosgen.py` into `tools/erosgen/` (errors/constants/paths/mcu/validate/model/emit/*/report/cli); `tools/erosgen.py` kept as shim (package shadows the module for `import erosgen`). `emit_makefile` now uses `paths.ENTRYPOINT` not `__file__` → Makefile byte-identical. Verified: 8/8 golden + zero git drift on a full reference-demo regen + exact CI cmd green.
- [x] `Diagnostic` dataclass + `Diagnostics` sink (strict raises / collect accumulates) + `collect_diagnostics()` (never raises). Sink threaded through model/validate with per-check codes + locations + collect-mode guards. cli keeps strict fail-fast (exits on first error); GUI path gets all diagnostics at once. 10/10 tests, byte-identical emitters, zero drift.
- [x] Externalize 328P tables into `mcu/atmega328p.yaml` behind `mcu/profile.py` (`MCUProfile.load`/`load_profile`); `mcu/__init__.py` loads the default profile and re-exposes `KNOWN_PERIPHERALS`/`PERIPHERAL_PINS`/`CONFLICTS_HARD`/`DRIVER_INIT`/`DRIVER_HEADER` so consumers are untouched. Profile tables verified equal to the originals (values + order). _Deferred to Phase 2: `NANO_ALIASES`, valid-ports, MCU/F_CPU/avrdude strings, and threading an `MCUProfile` object (needed for atmega2560)._
- [x] **Gate:** all golden tests byte-identical ✅ — 11/11 (`uv run pytest` + `python3 tools/test_erosgen.py`), zero reference-demo drift at every step.

**Phase 0 complete.** Next: Phase 1 (RTE + model parsing) or Phase 2 (`atmega2560.yaml`).

### Phase 1 — RTE + model parsing, headless (highest value/effort)
- [x] `parse/ert.py` — signals (`IN_`/`OUT_`, ctype, dim) + calibrations (extern/define) + entry points, dependency-free regex on the ExportToFile surface. Tested vs the real `codegen/appKnbSwt_ert_rtw/`.
- [x] `bind.py` — `DriverSpec` (adc/dio/pwm) + `check_binding()` (direction, required keys, value-range fit) via the sink: `TYPE_TOO_NARROW`, `DRIVER_DIRECTION`, `RANGE_TRUNCATION`, ...
- [x] `models.py` + `emit/rte.py` — resolve an `app.yaml models:` block (parse ERT × bind ports) → `Rte_Cfg.h` + `Rte.c` for adc(in)/dio(in,out), mirroring the hand-written `rte/` template. Golden-tested via `fixtures/model_rte/`.
- [x] **End-to-end**: `erosgen app.yaml` with `models:` emits `Rte.h/Rte_Cfg.h/Rte.c` next to `config.*`, synthesizes the model as a periodic OS task (`TASK_/ALARM_<model>`), and the Makefile builds `Rte.c` + model sources + bound drivers. os_gen calls `Rte_Init`; the alarm activates `Task_<model>` (Rte_Run + TerminateTask, which also satisfies the watchdog). Full app pinned by `fixtures/model_app/` golden (15/15); **new CI job regenerates (no-drift) + builds it `-Werror`**.
- **Verified locally:** generation, goldens, zero drift (reference-demo/genmain/model_rte), Python tests, runtime logic trace, `-Werror` cleanliness by inspection. **NOT verified locally:** the avr-gcc compile itself (no local toolchain) — the new CI gate is the proof; must push to run it.
- **Deferred follow-ups:** `codeInfo.mat` cross-check (scaling/dims) behind `[mat]`; pwm emit adapter; multi-model RTE; per-signal `static inline` accessors.

**Phase 1 complete end-to-end** (parse → bind → emit → schedule → build).

### Phase 2 — MCU breadth ✅
- [x] Threaded `MCUProfile` through the tool: `system.mcu` selects the target (default `atmega328p`); the profile now also holds valid ports, board aliases, and toolchain strings (mmcu/F_CPU/avrdude). `model`, `validate.normalize_pin`, and the makefile/osgen emitters read `s.profile` (module globals removed).
- [x] `mcu/atmega2560.yaml` — second same-family target (ports A..L, Mega `D13`→`PB7`, PORTE UART, `m2560`/`wiring`). `mega_gpio` fixture proves it: 2560 Makefile + `os_gen.h` driving `PORTL` (2560-only) and `PB7`; `PL7` rejected on 328P, accepted on 2560. 328P byte-identical (17/17, zero drift).
- Confirmed the standing risk: **ESP32 is still a separate backend** — this proved *same-family* breadth on the AVR backend; ESP32 breaks the emitter idioms and needs a kernel port.

### Phase 3 — PySide6 thin client ✅
- [x] `gui/` — `ProjectModel` bridge (pure, no Qt) + two-pane `MainWindow` (project tree | live diagnostics table | build console) + `File`/`Help` menus. Zero domain logic; every fact from the engine (`collect_diagnostics`, summaries) and generate = save + run the generator.
- [x] YAML persistence via `ruamel.yaml` round-trip (comments + key order preserved; flow-map inner spacing may normalize — a ruamel default, not comment loss). `app.yaml` stays the single source of truth.
- [x] Verified headless with Qt **offscreen**: 5 tests (ProjectModel + MainWindow smoke), plus a rendered screenshot. New CI `gui` job runs the offscreen tests.
- Run: `uv run --extra gui python -m gui [app.yaml]`.

---

## Status: Phases 0–3 complete
17 engine tests + 5 GUI tests; 328P byte-identical throughout; RTE end-to-end + `atmega2560` proven; thin GUI over the engine. **Unverified locally (CI-gated):** the `avr-gcc` compile of the generated `model_app` firmware, and the `gui` CI job's environment (Qt offscreen libs). Deferred follow-ups: `codeInfo.mat` scaling cross-check, pwm RTE adapter, multi-model RTE, GUI editing (currently read/generate).

## Parsing strategy (Phase 1) — verified against the real ERT output
Files probed in `codegen/appKnbSwt_ert_rtw/`:
- `codedescriptor.dmr` = **SQLite 3 DB (UTF-16LE)**, *not* XML → stdlib `sqlite3`, but proprietary/version-coupled schema. **Skip it.**
- `codeInfo.mat` = **MAT v5** → `scipy.io.loadmat` works (guard v7.3/HDF5 → needs `h5py`).

**Each source owns the facts it actually contains:**
1. **Topology (names, C types, direction, array dims): parse the constrained
   `_Intfc.h`/`_Param.h` with regex** — zero deps, it *is* the compilation
   contract, and the ExportToFile/Define storage-class convention keeps the
   surface to a few `extern <type> <NAME>;` lines (`appKnbSwt_Intfc.h:26`).
   Extend regex for `[N]` array dimensions.
2. **Semantic metadata NOT in C (scaling slope/offset, min/max, units): the header
   physically can't carry it** → take from `codeInfo.mat` (scipy, `[mat]` extra)
   or from explicit `app.yaml`. Only pull this in when `bind.py` scaling needs it.
3. Cross-check names/types between (1) and (2); **fail loudly** if the model
   violates the storage-class convention (point the error at a to-be-written
   `README_ASW.md` documenting the required storage classes + `IN_`/`OUT_` naming).
4. **Never** run a general C frontend (pycparser/clang) on raw `_ert_rtw` output.

## Risks / decisions to remember
- **Scaling boundary (`bind.py`):** do NOT silently synthesize `Y=mX+c` in `Rte.c`.
  Contract: default = ASW consumes **raw integer ticks** (scaling lives in Simulink,
  as `IN_KnbVal_Z` already does per `Rte_Cfg.h:33`); opt-in = `app.yaml` declares
  explicit slope/offset → deterministic, audit-friendly generated conversion.
- **Schedulability — the peer suggestion to use Liu & Layland `U≤n(2^{1/n}-1)` or the
  standard RTA recurrence is WRONG for this kernel.** Those model *preemptive*
  fixed-priority; EROS's documented model is **non-preemptive run-to-completion**
  (`:329`, `codegen/README §4`), so the sound relaxation would be non-preemptive
  RTA *with a blocking term* `B_i = max C of lower-prio tasks`. Keep the simple
  `ΣC ≤ T_base` sum gate — conservative is a *feature* on an 8-task AVR. Do store
  `T_i` and `C_i` per task in the model (cheap) to keep future RTA an option.
- **ESP32 is a second backend, not a YAML entry** — breaks the emitter layer
  (`gpio_set_direction` not `DDRB|=`, no PROGMEM, xtensa toolchain) *and* the kernel
  (AVR-asm context switch). 328P→2560 cheap; 328P→ESP32 = separate porting project.
- **relpath hazard:** the generated Makefile embeds `python3 ../tools/erosgen.py app.yaml`
  (relpath app_dir→entrypoint, `:762`). Moving the entrypoint changes that string and
  breaks the Makefile golden → keep the `tools/erosgen.py` shim.
- **YAML round-trip destroys comments** — `app.yaml` is densely commented; `safe_dump`
  strips them. GUI save uses `ruamel.yaml`. Never clobber "once" files (`write(overwrite=False)`, `:1004`).
- **Drop "estimate footprint from .c"** — meaningless under `-Os`+LTO. Keep per-`.o`
  `avr-size`; pre-build bar is "estimated (non-LTO)", over-counts vs LTO image (two gates, `app.yaml:14`).
- **No AVR toolchain on this machine** — parse/validate/generate work without it;
  only build/size need it. GUI must degrade gracefully.
