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
  `mega_gpio` fixture proves 2560 (PORTL, PB7). Non-AVR targets are out of scope.
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
stratification and no standardized module interface. **Staged** to keep the
byte-identical `reference-demo` anchor safe (it ships app-local `pwm.c`/`uart.c`
with `PWM_*`/`UART_*` names and declares `pwm:`, so those modules are entangled
with it and are migrated separately, not by a blind repo-wide rename).

- [x] **MCAL naming — ADC leads** (increment 1): `ADC_Init`/`ADC_Read` →
      `Adc_Init`/`Adc_ReadChannel` (+ `Adc_ReadVccMillivolts`/`Adc_ReadTempRaw`),
      threaded through `bind.py` `DriverSpec`, `emit/rte.py`, both MCU profiles,
      the hand-written `rte/Rte.c` reference, and the `test_adc` simavr firmware;
      RTE goldens + `genmain/os_gen.h` regenerated. ADC is fully decoupled from
      `reference-demo` (zero ADC there), so its goldens stayed byte-identical.
      Verified: 63 engine + 37 GUI tests, ruff/mypy, `avr-gcc` builds
      reference-demo (budget gates) + fixtures, `avr-nm` confirms `Adc_*` symbols.
      Note: kept the AUTOSAR *group* API (`Adc_ReadGroup`) out — single-channel
      blocking `Adc_ReadChannel` matches the 8-bit target; documented.
- [x] **MCAL naming — Pwm + Uart** (increment 2): shared `drivers/pwm.c` →
      `Pwm_Init`/`Pwm_SetDutyCycle`/`Pwm_GetDutyCycle` (duty stays permille,
      documented); `reference-demo/uart.c` + callers → `Uart_*` (geometry macros
      `UART_TX_SIZE/RX_SIZE` unchanged). Word-boundary renames guarded Timer0's
      `T0PWM_Init` and the `-D` macros. reference-demo (heavy UART + PWM user)
      builds byte-identical with budget gates; `drivers/pwm.c` compiles with
      `Pwm_*`; `test_uart` links `Uart_*`. **Deliberately left `reference-demo`'s
      app-local `pwm.c` as `PWM_*`** — it is a near-duplicate of the shared driver,
      so renaming both trips SonarCloud's new-code duplication gate.
- [x] **Consolidate the duplicate Timer1 PWM driver** (increment 3): deleted
      `reference-demo/pwm.{c,h}`; the `pwm:` peripheral now resolves to the shared
      `drivers/pwm.c` (Pwm_*) at its 1 kHz defaults, and the demo's callers are
      `Pwm_*`. Duplication gone; PWM is `Pwm_*` repo-wide. **Image byte-identical**
      (text 3630 / data 4 / bss 291 unchanged); only the reference-demo Makefile
      golden changed (VPATH + `-I../drivers`, pwm.c local→shared). 63 tests + build
      + budget gates green.
- [x] **MCAL naming — standalone drivers** (increment 4): `SPI_*`→`Spi_*`,
      `I2C_*`→`I2c_*`, `EE_*`→`Eep_*`, `ICP_*`→`Icp_*`, `ACOMP_*`→`Acomp_*`,
      `T0PWM_*`→`T0Pwm_*`. Per-function `\b` renames kept the config macros
      (`SPI_MODE*`/`SPI_CLK_DIV*`, `ACOMP_IN_*`, etc.). Driver .c/.h + simavr
      tests + profile `driver_init` + the configured-SPI builder in `osgen.py`.
      No golden drift (no fixture/demo declares these peripherals). All 6 test
      firmwares build; drivers compile gate green. **Every driver is now
      AUTOSAR-MCAL-named.**
- [x] **AUTOSAR topology dirs** (increment 5): peripheral drivers moved to
      `drivers/mcal/`; the `mcal/` subdir is threaded through the MCU profile
      source-map + the Makefile emitter (`_layer_dir`/`_basename`: source
      basenames stay flat, the layer dir goes on VPATH + `-I`). Services = the
      EROS kernel; ComplexDeviceDriver = `reference-demo/uart.c`. Only the
      Makefile goldens changed (`../drivers` → `../drivers/mcal`); reference-demo
      image byte-identical (3630/4/291). drivers gate + all simavr firmwares +
      RTE fixtures build; 63 tests; regen is a git-diff fixed point.
- [x] **`<Mod>_MainFunction` scheduling** (increment 6): a driver declares a
      cyclic MainFunction in the profile `main_functions` map (`adc` ships
      `Adc_MainFunction`, a non-blocking sampler); `peripherals.<p>.
      main_function_ms: N` wires it into the matching-rate ASW task's regenerated
      scaffold (runs before USER CODE, survives regen via the Phase 5 merge).
      Schema field + `ALLOWED_KEYS`; validation `MAIN_FUNCTION_UNSUPPORTED`/
      `MAIN_FUNCTION_NO_TASK`. Verified: 65 tests, generated app builds and links
      `Adc_MainFunction`. No drift (feature is opt-in; existing configs unchanged).
- **Risk:** medium — renames break goldens; needs a coordinated regen. Doing it
      per-module (not all at once) contains the blast radius; ADC increment proved
      the pattern with reference-demo untouched.

### Phase 8 — RTE maturity (residuals; multi-model already done)
- [ ] Contract phase: emit per-SWC `Rte_<SWC>.h` application headers (compile a
      SWC before the full system is configured).
- [ ] Queued sender-receiver for rate transitions — today `asw_signals.c` is a
      hand-written rate-transition layer; the RTE should generate it.
- [ ] Mode management (`Rte_Mode`/`Rte_Switch`) — fits the existing chained
      `TASK_STATUS`/`TASK_REPORT` pattern.
- [ ] Explicit runnable-to-task mapping so one SWC's multiple runnables can map
      to different rates (today one task = one rate).
- [x] **RTE driver coverage — timer0_pwm** (increment 1): output ports can now
      bind to Timer0 8-bit PWM (`driver: timer0_pwm, channel: 0|1`, duty 0..255,
      opt-in scaling) — `bind.py` DriverSpec + `emit/rte.py` adapter
      (`T0Pwm_SetDuty(ch, duty)`)/init/header + a GUI channel picker. Verified:
      66 tests, and a generated app compiles/links against `drivers/mcal/
      timer0_pwm.c`. `emit/rte.py` still `#error`s unbound drivers; `acomp` (in,
      needs init args) and `icp` (multi-value) are follow-ups.

### Phase 9 — Graphical pinout view (residual GUI gap)
Conflict-aware pin/channel **dropdowns** exist; the CubeMX-style visual **pin-map
grid** does not.
- [ ] Render the MCU pins (from `mcu/*.yaml` `PERIPHERAL_PINS`/aliases) as a
      clickable grid; selecting a peripheral auto-binds and highlights conflicts
      live via the existing pin→owner check. Read-only clock-tree note (Timer2
      /64, OCR2A=249) to document the fixed 1 kHz tick invariant.

### Phase 10 — Backend protocol
`backends/avr.py` isolates AVR idioms. Generalize to a `Backend` protocol
(`pin_init/read/write`, `progmem`, `toolchain`) so the emitters read a backend
interface instead of importing `backends.avr` directly — a cleaner seam even
while AVR is the only backend.
- [ ] `Backend` protocol; emitters read it instead of importing `backends.avr`.
- **Note:** non-AVR targets (e.g. Cortex-M) are **out of scope** — they need a
      whole separate kernel port (context switch, no PROGMEM, different
      toolchain), not just a backend module. This project stays AVR-only.

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

### Phase 14 — ATmega32U4 boards (Leonardo / Micro) (last)
The 32U4 is AVR (avr-gcc, same C), so it fits the MCU-profile mechanism — but
unlike the ATmega2560 (same peripheral family, worked with just a profile) it
needs a small **kernel retarget**, so it is its own phase, not a profile drop-in.
- [ ] `mcu/atmega32u4.yaml` profile: ports B/C/D/E/F, its pin aliases (Leonardo
      vs Micro silk differ), timers (Timer0/1/3/4 — **no Timer2**), peripheral
      pins, avrdude part/programmer (Caterina bootloader, 57600).
- [ ] **Kernel tick retarget** — the 1 kHz tick is hardware-fixed on **Timer2
      CTC** (`tick_hz` invariant), which the 32U4 lacks. Move the tick to an
      available timer (Timer0 or Timer3) behind a profile-selected macro; keep the
      328P path byte-identical. This is the real cost of the port.
- [ ] **Console decision** — the 32U4's USB is native (CDC), not a USART bridge;
      `Uart_*` on USART1 works with an external USB-serial adapter, or add a USB
      CDC ComplexDeviceDriver (out of scope unless needed). Document the choice.
- **Risk:** medium — the tick retarget touches the kernel; gate it behind the
      profile so 328P/2560 stay byte-identical.

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
- **A non-AVR target is a full port, not a YAML entry** — it breaks the emitter
  layer *and* the kernel (context switch, PROGMEM, toolchain), so it is out of
  scope; the project targets AVR MCUs only (see Phase 10).
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
