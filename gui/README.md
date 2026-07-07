# EROS Configurator (GUI)

A thin **PySide6** front-end over the `erosgen` engine (`tools/erosgen/`). It
holds **zero domain logic** — every fact (validation, diagnostics, RTE
resolution, code generation, MCU profiles, and the peripheral/pin-conflict
rules behind the forms below) comes from the engine via `ProjectModel`. If a
rule isn't in `erosgen`, it isn't in the GUI.

```sh
uv run --extra gui python -m gui [path/to/app.yaml]
```

## What it does

A **master-detail configurator**: pick a node in the project tree (left), edit
it on the context panel (right); the problem list and build console sit in tabs
along the bottom.

- **Project tree** (left) — grouped, and rebuilt from the engine on every edit:
  - **System** — name + MCU/board and the pre-flash **static-RAM budget**
    (kernel / pool / rings / free), the "see RAM before you flash" figure.
  - **Tasks**, grouped by rate and ordered by engine-assigned priority within a
    rate. Markers: `◆` codegen (model) task · `⬡` hand-authored ASW task (has a
    port interface) · plain task (no mark). Codegen/ASW rows expand to their
    signals.
  - **Resources** — OSEK shared sections with their user lists.
  - **Peripherals** — every peripheral the MCU offers; `●` = active, `○` =
    inactive, header shows `active/total`.
- **Context panel** (right) — swapped per selection:
  - **System** → MCU/board **retarget** (live: diagnostics + budget re-derive)
    and the memory budget.
  - **Task** → its scheduling (period, WCET, autostart, watchdog, within-rate
    priority order).
  - **Codegen / hand-ASW task** → its `in`/`out` interface with **inline port
    binding** — bind each signal to a driver (`adc`/`dio`/`pwm`) *or* to another
    SWC's output as an **ASW→ASW internal signal** (`← <SWC>.<OUT>`). The
    channel/pin pickers are **conflict-aware**: an already-owned pin/channel
    can't be selected.
  - **Resource** → editor (name, users, `mask_tick_isr`).
  - **Peripheral** → activate + configure its parameters (e.g. UART baud/rings,
    PWM/Timer0 frequency, SPI mode/clock, ADC reference/prescaler, I²C speed),
    with the same conflict-aware pin/channel pickers.
- **Problems** tab (bottom) — the engine's `collect_diagnostics()` + model
  port-binding checks as a colour-coded list that updates on every edit
  (`PIN_CONFLICT`, `UNKNOWN_MCU`, `TYPE_TOO_NARROW`, `PORT_NO_DRIVER`,
  `HARMONIC`, …); double-click a row to jump to its source location.
- **Console** tab (bottom) — streams `make` (Generate / Build) via `QProcess`.
- **Pinout** tab (bottom) — a CubeMX-style whole-chip pin map: ports × bits 0–7,
  each cell coloured by owner (blue = peripheral, green = gpio, purple = port
  binding, **red = conflict**, grey = not broken out) with per-pin tooltips, plus
  the read-only clock note (Timer2 /64, OCR2A=249 → 1 kHz tick). Read-only —
  editing stays in the conflict-aware pickers; the map is `ProjectModel.pinout()`,
  so it re-derives live on every edit/retarget.
- **Menus**
  - **File** — New Project… · Open… · **Open Workspace…** · Save · Save As… ·
    Generate · Build · Exit
  - **Edit** — Add Task… · Add **Codegen** Task… · Add **Resource…** · Remove
    Selected
  - **Help** — About

  Port binding is **not** a menu — it is inline on the selected model/ASW-task
  page. (The old read-only "Model" menu is gone.)
- **Workspaces** — *Open Workspace…* opens an `erosproject.yaml`; a second
  toolbar shows the workspace name, an **App** picker (each opens into the normal
  editor), a **Variant** selector, and **Generate All** (runs every app through
  the engine with the selected variant overlay). A plain `app.yaml` is unaffected.
- **YAML** is round-tripped with `ruamel.yaml`, so comments and key order
  survive a Save (flow-map inner spacing may normalize — a ruamel default, not
  comment loss). "Once" files are never clobbered.

## Layout & tests

```
gui/
  __init__.py     puts tools/ on the path for `import erosgen`
  project.py      ProjectModel — the pure, Qt-free bridge (load/save/edit,
                  diagnostics, budget, peripherals, model parsing + port
                  binding incl. ASW→ASW sources, resources, generate)
  main_window.py  the master-detail MainWindow (a view over ProjectModel)
  __main__.py     `python -m gui` entry point
  test_gui.py     37 tests, run headless under Qt's offscreen platform
```

```sh
QT_QPA_PLATFORM=offscreen uv run --extra gui python -m pytest gui/test_gui.py
```

The CI `gui` job runs exactly that.
