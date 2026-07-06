# erosgen upgrade — plan & TODO

ECU configuration + code-generation tool for the EROS RTOS (SystemDesk-style
ASW mapping ⊕ CubeMX-style peripheral generation), built by **extending**
`tools/erosgen/` — not rebuilding it.

## Framing: extend, don't rebuild

The engine is a Python package (`tools/erosgen/`) with a thin shim entrypoint
(`tools/erosgen.py`). Logic is decoupled from I/O along a clean spine —
**model → validate → parse → bind → emit** — with a `Diagnostics` sink that
serves both the fail-fast CLI and the collect-mode GUI:

```
tools/erosgen/
  cli.py            main() + --check; write() overwrite policy
  model.py          Task/Resource/System + validation gates
  validate.py       ALLOWED_KEYS shape check + normalize_pin
  diagnostics.py    Diagnostic dataclass + strict/collect sink
  parse/ert.py      Embedded Coder header regex (signals/calibrations)
  bind.py           DriverSpec (adc/dio/pwm) + check_binding
  models.py, asw.py resolve SWCs (codegen models + hand ASW tasks)
  emit/             config, makefile, osgen, skeletons, asw, rte
  backends/avr.py   DDRx/PORTx, PROGMEM idioms
  mcu/{profile.py, atmega328p.yaml, atmega2560.yaml, arduino_uno.yaml}
gui/                PySide6 configurator over the engine (project.py + main_window.py)
```

Tests: **53 engine** (`tools/test_erosgen.py`) + **37 GUI** (`gui/test_gui.py`);
328P output is byte-identical throughout (golden fixtures under `tools/fixtures/`).

---

## Status — what's shipped (Phases 0–3, complete)

Compressed ledger; detail lives in git history. **Do not re-plan these.**

- **Refactor spine** — `erosgen.py` split into the package above; `Diagnostic`
  dataclass + strict/collect sink; golden-master net (`reference-demo`,
  `genmain`, `model_rte`, `model_app`, `mega_gpio`, `asw_task`, `model_multi`).
- **RTE end-to-end** — `parse/ert.py` (regex on the ExportToFile surface) →
  `bind.py` (adc/dio/pwm, direction + range checks) → `emit/rte.py`
  (`Rte.h`/`Rte_Cfg.h`/`Rte.c`); a `models:` SWC is synthesized as a periodic OS
  task/alarm and the Makefile builds it. Golden + `-Werror` CI build.
- **Multi-model RTE** — DONE (was "deferred"): `fixtures/model_multi/` runs two
  SWCs (`appKnbSwt`+`motor`) with per-SWC namespaced defines
  (`RTE_CFG_APPKNBSWT_*` / `RTE_CFG_MOTOR_*`); wired via `_models`/`_id_name`
  multi flag and covered by `test_erosgen.py`.
- **Hand-authored ASW tasks** — author a runnable interface (ports/calibrations)
  in `app.yaml` instead of parsing Embedded Coder; emits `<name>{,_Intfc,_Param}`
  and wires ports through the RTE like a codegen SWC. `fixtures/asw_task/`.
- **ASW↔ASW internal signals** — one SWC's output feeds another's input
  (`port.source: "<SWC>.<OUT>"`); validated + RTE-routed.
- **MCU breadth (same family)** — `MCUProfile` threaded through the tool;
  `system.mcu` selects target; `atmega2560.yaml` + `arduino_uno.yaml` added;
  `mega_gpio` fixture proves 2560 (PORTL, PB7). ESP32 remains a separate backend.
- **GUI is now an editor, not read-only** (was "deferred"): master-detail
  configurator with in-place editing — Add/Remove Task, Add Codegen Task, Add
  Resource, resource editor, hand-ASW-task authoring, within-rate priority
  dropdown; **Peripherals section** to activate + configure PWM/UART/SPI/ADC/
  I2C/Timer0; **conflict-aware pin/channel pickers** (a clash can't be picked);
  MCU/board retarget live; `ruamel.yaml` round-trip preserves comments.
  Verified headless via Qt offscreen.
- **pwm RTE adapter** — DONE. **`codeInfo.mat` cross-check** — ABANDONED (opaque
  proprietary schema; the C header stays authoritative).

---

## Phase 4 — Documentation sync — **DO THIS FIRST**

**Why first:** an external review built from `webfetch` of this repo got ~40% of
its "gaps" wrong — it read the docs, and the docs describe a repo ~15 commits
stale. Stale docs are actively misleading downstream readers and tools. Fix the
source of truth before adding features.

- [x] **`todo.md` line-number rot** — the old file cited `:157`–`:1013` line refs
      into the pre-split monolith. This rewrite drops them; keep it that way
      (reference symbols/files, not line numbers, which drift).
- [x] **`gui/README.md` is stale** — rewritten to the shipped GUI: File/Edit
      (Add Task, Add **Codegen** Task, Add **Resource**, Remove Selected)/Help
      menus (the Model menu is gone), the **Peripherals** tree section
      (● active / ○ inactive), conflict-aware pin/channel pickers, resource +
      hand-ASW-task editors, inline port binding (driver *or* ASW→ASW source),
      live retarget, 37 tests. The false "signal→signal wiring not included"
      section is removed (the engine + GUI now support it). "Zero domain logic"
      restated and confirmed engine-backed (`ProjectModel`).
- [x] **`README.md` GUI blurb** (layout section) — now mentions peripheral
      activation/config + conflict-aware pinning and driver/ASW→ASW port binding.
- [x] **Kill the "deferred" claims everywhere** — done. No stale "deferred /
      follow-ups" lists remained in `README.md`/`rte/README.md`/`tools/README.md`;
      the only cross-doc GUI staleness was `rte/README.md`'s "Model menu" (fixed
      to Edit → Add Codegen Task + inline binding). `todo.md`'s status ledger
      keeps its "(was deferred)" notes intentionally as history.
- [x] **Add a "generation & overwrite policy" doc** — `tools/README.md` now has a
      **Generation & overwrite policy** heading over the overwrite table, and
      `README.md` points to it and states `config.*`/`Makefile`/`os_gen.h`/`Rte.*`
      are overwritten while `main.c`/`asw_*.c` are once-only.
- [ ] **Docs-drift guard (optional, cheap)** — a CI check or test that asserts a
      few load-bearing doc facts against code (e.g. the peripheral list in
      `gui/README.md` ⊆ `validate.ALLOWED_KEYS`, the overwrite table matches
      `cli.py`). Prevents the next fetch-based review from being wrong.

---

## Genuinely-open gaps (phased plan)

Ordered by value ÷ risk. Every phase must keep the golden tests byte-identical
(extend goldens as needed) — that gate is the project's safety net.

### Phase 5 — Protected-region merge — **the one critical, verified gap**
The overwrite policy is strictly binary (`cli.write()`: `wrote` if regenerated,
`kept` if a "once" file already exists). There are **0 `USER CODE` markers** in
the repo: once `main.c` / `asw_*.c` exist they are frozen, so any structural
change after first generation (new task, new peripheral, changed alarm geometry)
silently strands the user-owned skeletons while `config.*`/`os_gen.h` move on.
The `os_gen.h` "regenerate only if `main.c` still references it" hack is a
workaround for exactly this.

- [x] Emit paired `/* USER CODE BEGIN <id> */` … `/* USER CODE END <id> */`
      markers in all user-facing files (`main.c`, `asw_*.c`, hand-ASW bodies),
      with **stable IDs derived from the YAML element** (`TASK_<NAME>_BODY`/
      `_STATE`, `STARTUP_HOOK`/`ERROR_HOOK`/`SHUTDOWN_HOOK`, `RUNNABLE_<NAME>_
      INIT`/`_STEP`, `INCLUDES`) so a reorder carries user code by ID, not line.
- [x] `merge.py`: three-way merge — parse the on-disk file's `BEGIN/END` block
      contents (`extract_regions`), emit the fresh skeleton with the same IDs,
      re-inject captured user code into matching regions. Malformed markers →
      keep the file untouched (`MERGE_PARSE`), never lose data.
- [x] Diagnostics: `ORPHAN_USER_BLOCK` (warning) when a region no longer maps to
      any YAML element; the code is preserved verbatim in a compile-safe,
      idempotent `#if 0` graveyard so the user can relocate it, not lose it.
- [x] Golden tests for re-injection (edit-in-region → regen → preserved), orphan
      preservation, malformed-marker fallback, and the idempotent skip; genmain
      goldens + `model_app`/`model_multi`/`asw_task` `main.c` regenerated.
- [x] **Idempotent generation (content-hash skip)** — `cli.write()` skips the
      `write_text` when the computed content matches the on-disk bytes and
      reports `unchanged` alongside `wrote`/`kept`/`merged`, so `make config` no
      longer dirties `config.*`/`os_gen.h`/`Rte.*` timestamps needlessly.
- **Migration:** a legacy marker-less once-file is still `kept` untouched (opt in
      by deleting + regenerating). Behavior change was code-only; the overwrite
      table in `tools/README.md` still needs a `merged`/USER CODE note (a
      follow-up on the docs branch, to avoid a merge conflict here).
- **Risk:** low, additive; touched `cli.write()`, the skeleton emitters, and new
      `merge.py` only.

### Phase 6 — Meta-model / schema-driven validation
Validation is code-driven: `validate.ALLOWED_KEYS` (a dict) + hand-coded checks
emitting string codes (`UNKNOWN_KEY`, `PIN_CONFLICT`, `TICK_HZ`, …). Adding a
peripheral means editing `validate.py` + `model.py` + an emitter.

- [x] Externalize the config contract into a versioned JSON Schema (draft
      2020-12): `tools/erosgen/schema/app.schema.json` (`$id` .../app/v1).
      Validated with `jsonschema` behind the opt-in `[schema]` extra + `erosgen
      --schema`; **core stays PyYAML-only** (the schema is stdlib-loadable JSON;
      generation + the dep-free key check need no extra — proven with jsonschema
      blocked). `--schema` fails rc 1 on a violation (before generating) and rc 2
      if the extra is missing (never a silent no-op).
- [x] **Single source of truth:** `validate.ALLOWED_KEYS` is now DERIVED from the
      schema (`schema.section_keys()`), so the dep-free key check and the full
      JSON-Schema validation can't drift; `test_allowed_keys_derived_from_schema_
      matches_contract` pins it. Static value constraints (`tick_hz` const,
      `spi.mode/clock`, `adc.reference/prescaler`, `uart` rings, `pool`,
      `gpio.dir`) live in the schema and map to the engine's existing codes via
      `x-eros-code` at precise dotted locations through the same `Diagnostics` sink.
- **Scope note:** the schema owns the *static* surface; the ~10 MCU/F_CPU-
      dependent and cross-field checks (pin ownership, schedulability, ceilings,
      pwm/i2c ranges, peripheral availability) are not JSON-Schema-expressible and
      stay in `model.py` (default flow unchanged → no double-reporting). Follow-ups
      (own increments): wire the schema pass into `collect_diagnostics` so the GUI
      renders schema violations too (dedupe against code checks), and thin the now-
      redundant static checks in `model.py` once the schema owns them by default.
- **Risk:** low, additive; new `schema.py`/`app.schema.json`, `ALLOWED_KEYS`
      derivation, `--schema` flag. 58 engine + 37 GUI tests, ruff + mypy green.

### Phase 7 — BSW/MCAL layering
`drivers/` is flat (`adc/eeprom/i2c/spi/timer0_pwm/…`) with no MCAL/Services
stratification and no standardized module interface.

- [ ] Restructure toward the AUTOSAR topology: MCAL (Dio/Adc/Pwm/Gpt/Icu/Spi/
      Port), Services (EcuM-like startup, Dem-like error sink, Com-like IPC over
      the existing mailbox+pool), ComplexDeviceDriver (uart/watchdog).
- [ ] Standardized interface per module: `<Mod>_Init` / `<Mod>_MainFunction_<rate>ms`
      wired to the matching OS task by the generator; update `bind.py` `DriverSpec`
      strings (`ADC_Read/ADC_Init` → `Adc_ReadGroup/Adc_Init`).
- **Risk:** medium — renames break goldens; needs a coordinated regen. Do after
      Phase 5 so user code survives the churn.

### Phase 8 — RTE maturity (residuals; multi-model already done)
- [ ] Contract phase: emit per-SWC `Rte_<SWC>.h` application headers (compile a
      SWC before the full system is configured).
- [ ] Queued sender-receiver for rate transitions — today `asw_signals.c` is a
      hand-written rate-transition layer; the RTE should generate it.
- [ ] Mode management (`Rte_Mode`/`Rte_Switch`) — fits the existing chained
      `TASK_STATUS`/`TASK_REPORT` pattern.
- [ ] Explicit runnable-to-task mapping so one SWC's multiple runnables can map
      to different rates (today one task = one rate).
- [ ] `emit/rte.py` currently `#error`s any driver beyond adc/dio/pwm — extend
      coverage as new bindings land.

### Phase 9 — Graphical pinout view (residual GUI gap)
Conflict-aware pin/channel **dropdowns** exist; the CubeMX-style visual **pin-map
grid** does not.
- [ ] Render the MCU pins (from `mcu/*.yaml` `PERIPHERAL_PINS`/aliases) as a
      clickable grid; selecting a peripheral auto-binds and highlights conflicts
      live via the existing pin→owner check. Read-only clock-tree note (Timer2
      /64, OCR2A=249) to document the fixed 1 kHz tick invariant.

### Phase 10 — Backend protocol + ESP32
`backends/avr.py` isolates AVR idioms. Generalize to a `Backend` protocol
(`pin_init/read/write`, `progmem`, `toolchain`) so `esp32.py`/`cortex_m.py`
become siblings.
- [ ] `Backend` protocol; emitters read it instead of importing `backends.avr`.
- [ ] `backends/esp32.py` — the cheap part; **the kernel port (AVR-asm context
      switch, no PROGMEM, xtensa toolchain) is the real cost** and stays a
      separate porting project.

### Phase 11 — ASW parser robustness + interchange
Regex parser is tied to the ExportToFile/Define storage-class contract.
- [ ] Tier A: `pycparser`-backed fallback (`[parse]` extra) for headers that
      don't follow the contract — keeps the data model unchanged.
- [ ] Tier B: accept a hand-authored `swc.yaml` (ports/types/runnables) as a
      first-class alternative to the Embedded Coder round-trip.
- [ ] Tier C (aspirational): import ARXML SWC descriptions; source scaling from
      `SwDataDefProps` (min/max/offset/slope) instead of the abandoned `.mat`.

### Phase 12 — Toolchain/project gen + calibration (low priority)
- [ ] `emit/` also produces `CMakeLists.txt`, VSCode `tasks.json`/
      `c_cpp_properties.json`, and `compile_commands.json` from the per-`.o` rule.
- [ ] `emit/a2l.py` (ASAP2/A2L from the `Calibration`/`Signal` dataclasses) + a
      minimal XCP-on-UART slave over the existing console, for on-target tuning.

### Phase 13 — Project/workspace + variant management (low priority)
Today one `app.yaml` = one application; there is no ECU-configuration-set or
variant posture (the SystemDesk concept that matters the moment there's a product
line). Low priority for a single-target hobby/education AVR tool, but a real
SystemDesk-class gap worth recording.
- [ ] `erosproject.yaml` aggregating multiple `app.yaml`s with shared BSW/MCAL
      config and variant postures (debug/release, feature flags) as configuration
      sets; the GUI opens a workspace, not just a single project.
- **Risk:** medium; only worth doing once BSW layering (Phase 7) gives shared
      config something to share.

---

## Reference: durable design constraints (keep — not tasks)

- **Scaling boundary (`bind.py`):** do NOT silently synthesize `Y=mX+c`.
  Default = ASW consumes **raw integer ticks** (scaling lives in Simulink);
  opt-in = `app.yaml` declares explicit slope/offset → deterministic, auditable
  generated conversion.
- **Schedulability:** keep the simple `ΣC ≤ T_base` sum gate. Liu & Layland /
  RTA recurrence model *preemptive* fixed-priority and are **wrong** for this
  **non-preemptive run-to-completion** kernel; the sound relaxation would be
  non-preemptive RTA with a blocking term `B_i = max C of lower-prio tasks`.
  Conservative is a *feature* on an 8-task AVR. Store `T_i`/`C_i` per task to
  keep future RTA an option.
- **ESP32 is a second backend, not a YAML entry** — breaks the emitter layer
  *and* the kernel (see Phase 10).
- **relpath hazard:** the generated Makefile embeds
  `python3 ../tools/erosgen.py app.yaml` (relpath app_dir→entrypoint). Moving the
  entrypoint changes that string and breaks the Makefile golden → keep the
  `tools/erosgen.py` shim. **uv stays a dev tool** — never leak `uv run` into the
  generated Makefile's `config:` target (preserves the byte-exact golden and the
  "Python-less CI can still `make` from committed output" property).
- **YAML round-trip destroys comments** with `safe_dump` — GUI save uses
  `ruamel.yaml`. Never clobber "once" files (`write(overwrite=False)`).
- **Footprint from `.c` is meaningless** under `-Os`+LTO — keep per-`.o`
  `avr-size` (two gates: non-LTO `budget`, LTO-image `size`).
- **No AVR toolchain guaranteed on the dev machine** — parse/validate/generate
  work without it; only build/size need it; the GUI degrades gracefully. The
  `avr-gcc` compile of generated firmware is CI-gated.

## Dependencies (uv) — keep core PyYAML-only

| Group | Deps | When |
|---|---|---|
| core | `pyyaml` | always |
| `[gui]` | `PySide6`, `ruamel.yaml` | GUI + comment-preserving round-trip |
| `[dev]` | `pytest` | tests (also run standalone) |
| `[schema]` (planned) | `jsonschema` | Phase 6 |
| `[parse]` (planned) | `pycparser` | Phase 11 Tier A |

Workflow: `uv sync` · `uv run python -m erosgen.cli app.yaml` · `uv run pytest` ·
GUI: `uv run --extra gui python -m gui [app.yaml]`.
