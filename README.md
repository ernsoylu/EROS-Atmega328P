# EROS — Embedded Realtime Operating System for the Arduino Nano

**EROS** (Embedded Realtime Operating System) is an ultra-minimalist,
statically configured, **non-preemptive run-to-completion** real-time
kernel implementing the OSEK/VDX BCC1
(Basic Conformance Class 1) task model on the bare ATmega328P
(Arduino Nano, 16 MHz, 2 KiB SRAM, 32 KiB Flash). Pure bare-metal C99 —
no Arduino framework, no heap, no runtime object creation. Just
`avr-gcc`, `<avr/io.h>` and the datasheet.

Measured with avr-gcc 7.3 (`-Os`, non-LTO reference build — the shipped
`-flto` image is smaller):

| Budget | Limit | Measured |
|---|---|---|
| Kernel Flash (`eros.o` + `config.o`) | ≤ 3072 B | **1945 B** |
| Kernel static RAM (`eros.o`) | ≤ 128 B | **35 B** |
| Pool arena (user payload, reported separately) | — | 32 B |
| Demo application RAM (all ASW objects) | — | 6 B |
| Stack + idle RAM (reported separately) | — | 1975 B |
| Whole demo image (LTO + `--gc-sections`) | — | 2132 B Flash / 72 B RAM |

`make` builds, prints `avr-size` output and **fails the build** if a
budget is exceeded (`make budget`).

## Layout

```
kernel/               app-agnostic kernel, reused by every application
  eros_types.h     types, StatusType set, config record types,
                      MISRA deviation record D1..D8
  eros.h           public API + doc comments (semantics, error codes)
  eros.c           scheduler, tick ISR, alarms, resources, mailbox,
                      pool, stack canary, watchdog, .init3 WDT fix
config.h / config.c   the reference demo's static configuration ("OIL
                      file"): tasks, priorities, alarms, resources, pool
                      geometry, hooks, aliveness mask — PROGMEM tables
main.c                integration layer: hooks + one-shot init task +
                      main() — no periodic application code
asw_10ms.c/.h         TASK_FAST — scope channel PD2 + mailbox consumer
asw_50ms.c/.h         TASK_MED — scope channel PD3 + pool/mailbox producer
asw_500ms.c/.h        TASK_SLOW (PD4, ChainTask demo) + the chained
                      TASK_REPORT 2 s heartbeat (PD5)
asw_ipc.h             producer→consumer payload protocol shared by the
                      10/50 ms rates + the concurrency rationale
actuator.c/.h         polymorphic GPIO driver (OOP-in-C, instances and
                      vtables 100% in PROGMEM)
Makefile              mandated warning-free flags, .map file, size/budget
                      targets, avrdude flash target (old-bootloader baud)
comprehensive-demo/   a second, bigger application on the same kernel:
                      interrupt-driven serial console (ON/OFF/STAT),
                      debounced button, Timer1 PWM breathing LED,
                      pool+mailbox IPC — see its README.md
drivers/              app-agnostic drivers completing the ATmega328P
                      peripheral coverage: ADC, EEPROM, I2C, SPI,
                      INT/PCINT, Timer0 PWM, Timer1 input capture,
                      analog comparator — pins, WCETs, ISR categories
                      and resource conflicts in its README.md
tools/erosgen.py      system configurator: compiles app.yaml into
                      config.h/config.c/Makefile + ASW skeletons;
                      selects which peripherals compile and sizes the
                      RAM-dominant buffers — see tools/README.md
app.yaml              the reference demo's configuration, from which
                      its config.*/Makefile are generated
codegen/              Simulink / Embedded Coder output (ASW): drop
                      <model>_ert_rtw here, kept frozen; README.md
                      documents model configuration and data types
rte/                  Runtime Environment: the single hand-written layer
                      binding a model's ports/params/runnable to the
                      drivers + OS (ASW→RTE→BSW) — see its README.md
model/                the Simulink project itself (.slx/.sldd + test
                      harness) that generates codegen/ (caches ignored)
tests/                simavr simulation tests: on-chip self-checks +
                      libsimavr host runner exercising every peripheral
                      driver and the ASW→RTE→BSW model — see its README.md
.github/workflows/    CI: build+budget, simavr peripheral/model matrix,
                      qemu boot smoke — see "Continuous integration" below
```

The kernel directory never contains a `config.h`; each application
provides its own and compiles the kernel sources against it (`-I.`
first) — the OSEK "one kernel, per-app static config" model. The
`comprehensive-demo/` application reuses the kernel unchanged with a
completely different task set.

That per-app `config.h`/`config.c`/`Makefile` are **generated from a
single `app.yaml`** by `tools/erosgen.py` (the OIL compiler): it picks
which peripheral drivers compile, sizes the RAM-dominant buffers (UART
rings, pool arena), assigns rate-monotonic priorities, and enforces
schedulability and pin/peripheral-conflict rules — an invalid system
cannot be generated. Both shipped demos regenerate to byte-identical
images from their `app.yaml`. See `tools/README.md`.

## Architecture in one page

- **Scheduling** — TaskID == static priority == bit position in an 8-bit
  `ready_mask` (max 8 tasks, uniqueness enforced by `_Static_assert`).
  Highest ready bit wins, found O(1) with a 16-entry PROGMEM nibble LUT
  (AVR has no CLZ). Tasks run to completion; termination is the return
  from the entry function. `Schedule()` is deliberately omitted
  (documented deviation — it would only grow the shared stack). The
  OSEK information services are provided too: `GetTaskID`,
  `GetTaskState`, `GetAlarm`, `GetAlarmBase`,
  `GetActiveApplicationMode` (unused ones cost nothing —
  `--gc-sections` discards them).
- **Tick & alarms** — Timer2 CTC, `TCCR2A=(1<<WGM21)`,
  `TCCR2B=(1<<CS22)` (/64 — Timer2's prescaler table differs from
  Timer0/1!), `OCR2A=249` → exactly 1 kHz. Alarms expire *inside* the
  Category-2 tick ISR (≤ 1 tick activation error) using the wrap-safe
  comparison `(int16_t)(now - expiry) >= 0`. `SetRelAlarm` rejects
  offsets > 32767 with `E_OS_VALUE`; `SetAbsAlarm` with an
  already-passed start waits for counter wraparound (implemented with a
  half-range waypoint, still wrap-safe). Cyclic re-arm is anchored
  (`expiry += cycle`) and therefore drift-free.
- **Errors** — full mandated `StatusType` set; every service failure
  runs through `ErrorHook` (re-entrancy guarded). A cyclic alarm firing
  into a task that is still READY/RUNNING reports `E_OS_LIMIT` — free
  deadline-miss detection. Per-task WCET budgets from `config.c` are
  checked at termination (±1 tick).
- **Resources (IPCP)** — non-blocking by construction; in a
  non-preemptive kernel task-level ceilings have *no scheduling effect*
  (documented), but a resource may raise its ceiling to ISR level:
  holding it masks the tick interrupt (`OCIE2A`), the hardware latches
  one pending compare match, and release restores it. LIFO order is
  enforced; violations and termination-with-held-resources raise
  `ErrorHook` (and the dispatcher force-releases).
- **IPC** — single-slot mailbox transporting handles of an O(1)
  fixed-block pool (free list threaded through the free blocks; 8-bit
  allocation mask gives O(1) double-free detection and guards the
  handle→pointer translation). Empty/full are normal polling outcomes
  and do not raise `ErrorHook`.
- **Idle & power** — `ready_mask == 0` → `SLEEP_MODE_IDLE` via the
  canonical lost-wakeup-free sequence
  `cli(); if (ready) {sei();} else {sleep_enable(); sei(); sleep_cpu();}`.
- **Supervision** — stack canary (0xC5) painted at `StartOS()` from
  `__heap_start` to just below the live stack pointer and verified at
  every scheduling point → `ShutdownOS()` on breach. Watchdog
  `WDTO_2S`: kicked only when *every* task in `OS_ALIVE_REQUIRED_MASK`
  has completed since the previous kick; the aliveness mask is cleared
  immediately after each kick.
- **Old-bootloader safety** — `.init3` naked function clears `MCUSR`
  and disables the WDT before `main()` (canonical avr-libc pattern), so
  a WDT reset can't boot-loop ATmegaBOOT Nanos from the application
  side. Caveat: a *genuine* WDT reset still re-enters the slow
  bootloader with `WDRF` set — burn Optiboot for dependable WDT
  recovery, or use WDT interrupt+reset mode. The captured reset cause
  is exported as `os_resetCause`.

## Reference demo

| Task | Prio | Period | Pin | Purpose |
|---|---|---|---|---|
| `Task_Fast` | 4 (highest) | 10 ms | PD2/D2 | scope jitter channel; mailbox consumer |
| `Task_Med` | 3 | 50 ms | PD3/D3 | scope channel; pool producer via mailbox under `RES_DEMO` |
| `Task_Slow` | 2 | 500 ms | PD4/D4 | scope channel; `ChainTask(TASK_REPORT)` every 4th run |
| `Task_Report` | 1 | chained | PD5/D5 | heartbeat (toggles every 2 s) |
| `Task_Init` | 0 | once (autostart) | — | arms alarms; deliberate double `ActivateTask` → `E_OS_LIMIT` → `ErrorHook` |

Put a scope on D2/D3/D4: you get 50/10/1 Hz square waves whose edge
jitter is bounded by ≤ 1 tick activation error + the longest task WCET
(≤ 1 ms steady-state). The on-board LED (PB5/D13) belongs exclusively
to the hooks: it turns ON right after boot — that is the *intentional*
double-activation error arriving in `ErrorHook` — and stays lit as a
visible marker (solid ON from `ShutdownHook` would signal a terminal
fault instead).

Verified in simavr (10 simulated seconds): PD2/PD3/PD4 toggle exactly
1000/200/20 times with the steady-state period accurate to the cycle,
PD5 beats 6 times, PB5 toggles exactly once at boot, and a dedicated
test confirmed `SetAbsAlarm` with a passed start value expires exactly
one counter wraparound later (tick 65536 + start), including the
start == now equality edge.

## ASW structure & shared data (why there are no mutexes)

Both applications follow the structure recommended for
Simulink/Embedded Coder output in `codegen/README.md` §4: **one C/H
pair per task rate** (`asw_10ms.c`, `asw_50ms.c`, …), a thin
integration `main.c` (hooks + init task only), and *no* application
state shared between rate files through ad-hoc globals. Data crosses a
rate boundary in exactly two sanctioned ways:

1. **Kernel IPC** — pool block + single-slot mailbox, wrapped in a
   `GetResource`/`ReleaseResource` pair marking the handoff as one
   logical unit (reference demo, `asw_ipc.h`).
2. **A signals module** — `comprehensive-demo/asw_signals.c/.h`, the
   hand-written equivalent of Simulink's Rate Transition layer: every
   cross-rate signal is accessed only through its accessor functions.

Neither path needs a mutex or semaphore, **by design, not by
omission**: the kernel is non-preemptive run-to-completion, so two
tasks can never interleave and task↔task data races cannot exist —
the same reason Embedded Coder's rate-transition buffers are never
contended here (`codegen/README.md` §4). The only real concurrency
hazard is task↔ISR sharing, and the rules for it are:

- ISR-shared objects are `volatile`; single-byte accesses are naturally
  atomic on AVR, anything wider runs under
  `ATOMIC_BLOCK(ATOMIC_RESTORESTATE)` (the kernel's own contract, see
  `kernel/eros.c`).
- A critical section against the OS tick is a resource with
  `mask_tick_isr = 1` (`RES_DEMO`) — the OSEK ISR-ceiling pattern.
- A blocking semaphore is deliberately impossible: BCC1 has no WAITING
  state, and adding one would forfeit the single-shared-stack,
  statically-bounded-depth guarantee. If the kernel ever became
  preemptive, mutual exclusion attaches at the existing seams — inside
  the resource-wrapped IPC handoffs and the `asw_signals` accessors —
  without touching any task code.

## Build & flash

The `eros.sh` helper wraps the whole toolchain — check, install, build,
flash:

```sh
./eros.sh              # check the AVR toolchain is installed
./eros.sh -install     # install anything missing (apt/dnf/pacman/brew/…)
./eros.sh -build       # build both firmwares into ./build (gitignored)
./eros.sh -flash       # auto-detect the board + baud, flash reference demo
./eros.sh -flash demo  # flash the comprehensive demo instead
```

`-flash` finds the serial port (`/dev/ttyUSB*`, `/dev/ttyACM*`,
`/dev/cu.usb*`) and the bootloader baud (57600 old-bootloader Nano,
then 115200 Optiboot) by probing the ATmega328P signature; override
either with `EROS_PORT=` / `EROS_BAUD=`.

Or drive the Makefiles directly:

```sh
make                 # build + size + budget check (fails if over budget)
make flash           # avrdude, old-bootloader Nano (57600 baud)
make flash BAUD=115200 PORT=/dev/ttyACM0   # Optiboot boards

cd comprehensive-demo && make              # the second application
```

Mandated flags (warning-free): `-Wall -Wextra -Werror -std=c99 -Os
-flto -ffunction-sections -fdata-sections -Wl,--gc-sections
-Wl,-Map=eros.map -mmcu=atmega328p -DF_CPU=16000000UL`.

## Model integration — ASW → RTE → BSW

Simulink/Embedded Coder models integrate through a layered, AUTOSAR-style
architecture — no ad-hoc glue:

```
ASW   codegen/<model>_ert_rtw/   generated algorithm: ports + runnable (frozen)
RTE   rte/                       the ONLY hand-written layer
BSW   drivers/ (MCAL) + kernel/  drivers + EROS OS (frozen)
```

The RTE owns the three integration concerns — **port data flow** (drivers
↔ model I/O), **calibration** (model parameters), and **scheduling**
(runnable → OS task/alarm). `rte/Rte_Cfg.h` is pure declarative config,
shaped so `tools/erosgen.py` can generate the RTE from an `app.yaml`
`models:` section later, the same way it already generates
`config.*`/`Makefile`. The worked example (`appKnbSwt`: ADC knob → digital
output) and the future generation schema are in `rte/README.md`.

## Continuous integration & simulation testing

Because **Renode has no AVR core**, the firmware is executed under
**simavr** (the AVR-native simulator) in CI. `.github/workflows/ci.yml`
runs three jobs on every push/PR:

1. **build** — erosgen unit tests, root demo + memory-budget gate,
   comprehensive-demo, and `-Werror` compile gates for the drivers and
   the generated model + RTE.
2. **sim** — builds self-checking test firmware and a `libsimavr` host
   runner (`tests/`) that injects stimulus (ADC voltages/ramps, GPIO
   edges, SPI loopback) and reads a UART `PASS/FAIL` sentinel. Covers
   every peripheral driver plus the ASW→RTE→BSW model swept across its
   full ADC range.
3. **smoke** — `qemu-system-avr` boot/run check.

Run the simulation matrix locally with `make -C tests test` (needs
`gcc-avr avr-libc libsimavr-dev simavr`). See `tests/README.md`.

## Conformance notes

MISRA C:2012 deviations are itemised as D1–D8 in `kernel/eros_types.h`
(hardware register access, avr-gcc attributes, the `TerminateTask()`
return macro, PROGMEM function-pointer reads, wrap-safe signed tick
arithmetic, …). OSEK deviations (no `Schedule()`, returning
`ChainTask`, no `AppModeType` argument to `StartOS`, reduced
`StatusType` set) are documented in the same header and in `eros.h`.
