# erosgen — the EROS system configurator

`erosgen` compiles one **`app.yaml`** (the OSEK "OIL file") into the static OS
configuration, the application Makefile, and — the first time only — per-rate
ASW skeletons and a `main.c` integration stub. It is the single place to choose
which peripherals are compiled, size the buffers that dominate RAM, wire tasks
to the scheduler, target an MCU, and — from a `models:` section — **generate the
RTE** that binds a Simulink model's ports to peripherals and schedules it.

`erosgen` is a package (`tools/erosgen/`: `model` · `validate` · `mcu` ·
`parse` · `bind` · `emit/*` · `cli`); `tools/erosgen.py` is a thin shim so the
invocation and `import erosgen` are unchanged.

```sh
python3 tools/erosgen.py app.yaml          # generate
python3 tools/erosgen.py app.yaml --check  # validate + report, write nothing
make config                                # same, from inside a generated app
```

**Environment (`uv`).** Deps are managed with [uv]; the core needs only PyYAML,
with the opt-in extra `[gui]` (PySide6, ruamel.yaml):

```sh
uv sync                                     # core env
uv run python tools/erosgen.py app.yaml     # generate under uv
uv run pytest tools/test_erosgen.py         # 53 engine tests
uv run --extra gui python -m gui [app.yaml] # the PySide6 configurator (gui/)
```

Generated Makefiles include a `config` target that reruns the generator, so
after editing `app.yaml` you run `make config` then `make` (config is *not*
auto-rebuilt, so a Python/uv-less CI can still `make` from committed output).

[uv]: https://docs.astral.sh/uv/

## Why this saves RAM

Two independent levers, both driven from the YAML:

1. **Peripherals are opt-in.** Only drivers listed under `peripherals:`
   are added to the Makefile `SRCS`. An unused driver is never compiled,
   so it costs zero flash and zero RAM. (Even a *linked* but unreferenced
   driver is stripped by `-ffunction-sections` + `--gc-sections` + LTO —
   the real, larger lever is the next one.)
2. **Buffer geometry is explicit.** UART TX/RX rings and the memory-pool
   arena are the dominant application RAM. The YAML sets them and the
   generator emits `-DUART_TX_SIZE=…` etc.; shrinking the TX ring from
   128→32 B on the reference demo drops static RAM by 96 B (295→199 B)
   with no code change.

The end-of-run report prints the static-RAM plan (kernel, arena, rings)
so "too much RAM" is a number you see *before* flashing.

## Generation & overwrite policy

This table is the single source of truth for **which files are regenerated on
every run and which are written once** — the most load-bearing behavior to know
before you hand-edit anything in a generated app.

| File | Overwrite? | Contents |
|---|---|---|
| `config.h` | always | task IDs = rate-monotonic priorities, alarms, resource ceilings, pool geometry, aliveness mask, hooks, all `OS_STATIC_ASSERT` guards |
| `config.c` | always | PROGMEM task/alarm/resource tables + pool arena |
| `Makefile` | always | `SRCS` = kernel + selected drivers + ASW (+ Simulink model, `ert_main.c` filtered out); peripheral `-D` geometry; optional budget target |
| `os_gen.h` | always* | `Board_ConfigurePins()` (gpio + driver init) and `OS_StartAlarms()` (arms every alarm) — kept in lockstep with the YAML |
| `main.c` | once | hooks + autostart init task, calling the `os_gen.h` helpers |
| `asw_<rate>ms.c` | once | one task body per periodic rate, calling its runnables |

"Once" files are created only if absent — your hand-written ASW is never
clobbered. `config.*`, `Makefile` and `os_gen.h` are derived artifacts:
edit the YAML and regenerate, never edit them directly.

\* `os_gen.h` is (re)written only for apps whose `main.c` includes it —
a freshly generated `main.c` does. Hand-written mains that manage their
own startup (the reference demo) are left untouched, so `os_gen.h`
never appears in them.

**Regeneration drift is handled**: because the alarm-arming and pin
setup live in the always-regenerated `os_gen.h`, adding a task or pin in
the YAML and running `make config` propagates into the build with **no
edit to your hand-written `main.c`** — the new alarm is armed and the
new pin configured automatically.

## Validation gates (an invalid system cannot be generated)

- ≤ 8 tasks (8-bit ready mask); unique names (same for resources).
- **`tick_hz` must be 1000** — the kernel's Timer2 tick is hardware-fixed
  at 1 kHz, so this is a kernel invariant, not a knob.
- **Unknown keys are rejected** at every level with a "did you mean"
  hint — a misspelled `period_ms` can't silently make a task aperiodic.
- Periods are multiples of the tick and ≤ the alarm range (32767 ticks).
- **Schedulability**: Σ WCET of all periodic tasks ≤ the base (fastest)
  period — the non-preemptive run-to-completion rule from
  `codegen/README.md` §4, enforced mechanically. WCET rounds up to whole
  ticks (never under-budget).
- **Pin ownership matrix**: every pin a peripheral or `gpio` entry claims
  is checked for a single owner — SPI's SCK vs an LED on PB5, `icp` vs
  `pwm` on Timer1, ADC channels vs I²C on A4/A5, etc. are hard errors.
- UART ring sizes are powers of two in 2..256.
- Resource users must be declared tasks; ceiling is computed as the
  highest-priority user (never typed by hand).
- Simulink `rate_map` steps must name real tasks.

## Priority assignment

You never write priorities. The generator assigns them:
autostart init task lowest → aperiodic (activated/chained) tasks →
periodic tasks **rate-monotonically** (fastest period = highest
priority). Alarm IDs are ordered fastest-first. This reproduces the
reference demo's hand-tuned map exactly.

**Autostart task is auto-synthesized when absent.** Cyclic alarms are
armed by `OS_StartAlarms()`, which only runs from an autostart task — so
an app with periodic/model tasks but *no* declared autostart task would
never arm its alarms (the scheduler would idle forever). When that
happens the generator synthesizes a minimal `TASK_INIT` that arms the
alarms, reports it (`note: … synthesized TASK_INIT`, plus a `SYNTH_INIT`
info diagnostic in the GUI), and moves on. Declare your own autostart
task to put boot logic there instead — it becomes the `OS_StartAlarms()`
site and nothing is synthesized.

## app.yaml reference

```yaml
system:
  name: myapp                 # TARGET (myapp.hex)
  mcu: atmega328p             # MCU profile (mcu/*.yaml): atmega328p | atmega2560
  kernel_dir: ../kernel       # path to the EROS kernel sources
  drivers_dir: ../drivers     # optional: where shared drivers resolve
  tick_hz: 1000               # must be 1000 (kernel Timer2 is fixed)
  hooks: { startup: true, error: true, shutdown: true }
  budget: { flash: 3072, ram: 128, sram_total: 2048 }   # optional gate

sources: [main.c]             # application-owned .c files; the generated
                              # asw_<rate>ms.c files are auto-added too

peripherals:                  # omit a peripheral => not compiled
  uart: { baud: 9600, tx_ring: 128, rx_ring: 64 }
  pwm: {}
  adc: {}                     # adc/eeprom/i2c/spi/extint/timer0_pwm/icp/acomp

gpio:                         # optional; expands into Board_ConfigurePins()
  - { pin: D13, dir: out, name: LED, init: false }   # PB5 or "D13"
  - { pin: D2,  dir: in,  pullup: true, name: BUTTON }

tasks:
  - { name: init,   autostart: true, wcet_ms: 2 }
  - { name: ctrl,   period_ms: 10, wcet_ms: 2, runnables: [Asw_Sample, Asw_Ctrl] }
  - { name: report, wcet_ms: 1 }         # no period => aperiodic (chained/activated)
  # watchdog: true|false  (default: true for periodic tasks)
  # entry: Task_Foo       (default: Task_<Name>)

resources:
  - { name: demo, users: [ctrl], mask_tick_isr: true }

pool: { block_size: 8, blocks: 4 }

simulink:                     # optional simple binding: calls model_step() in a
  model: ctrl                 #   task body (no RTE generated)
  dir: ../codegen
  rate_map: { step0: ctrl }   # generated glue calls ctrl_step0() in TASK_CTRL

models:                       # optional: GENERATE the RTE for a Simulink SWC.
  - name: appKnbSwt           #   erosgen parses codegen/appKnbSwt_ert_rtw/,
    codegen_dir: ../codegen/appKnbSwt_ert_rtw   # type-checks the port bindings,
    runnable: appKnbSwt_Runnable                # emits Rte.h/Rte_Cfg.h/Rte.c,
    rate_ms: 10               #   and wires it as TASK_/ALARM_APPKNBSWT.
    wcet_ms: 2                # optional (default 1)
    ports:                    # each IN_/OUT_ signal -> a driver (adc/dio/pwm)
      in:  [{ signal: IN_KnbVal_Z, driver: adc, channel: 0 }]
      out: [{ signal: OUT_Led1_B, driver: dio, port: B, bit: 5 }]
```

See `rte/README.md` for the RTE it generates and `mcu/*.yaml` for MCU profiles.
The `gui/` PySide6 app drives all of this interactively (open/create a project,
bind model ports, live diagnostics, memory budget, generate/build).

## Reference configs

`reference-demo/app.yaml` regenerates the shipped firmware to a
**byte-identical** image — it is the worked example. Regenerate and diff
to see for yourself:

```sh
python3 tools/erosgen.py reference-demo/app.yaml && make -C reference-demo
```
