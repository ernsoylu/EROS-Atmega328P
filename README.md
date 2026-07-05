# EROS — Embedded Realtime Operating System for the Arduino Nano

**EROS** (Embedded Realtime Operating System) is an ultra-minimalist,
statically configured, **non-preemptive run-to-completion** real-time
kernel implementing the OSEK/VDX BCC1
(Basic Conformance Class 1) task model on the bare ATmega328P
(Arduino Nano, 16 MHz, 2 KiB SRAM, 32 KiB Flash). Pure bare-metal C99 —
no Arduino framework, no heap, no runtime object creation. Just
`avr-gcc`, `<avr/io.h>` and the datasheet.

Measured with avr-gcc 7.3 (`-Os`). The kernel rows are the non-LTO
reference build (stable, `--gc-sections`-independent); the whole-image row
is the shipped `-flto` + `--gc-sections` image:

| Budget | Limit | Measured |
|---|---|---|
| Kernel Flash (`eros.o` + `config.o`) | ≤ 3072 B | **2000 B** |
| Kernel static RAM (`eros.o`) | ≤ 128 B | **42 B** |
| Pool arena (user payload, reported separately) | — | 32 B |
| Application RAM (UART rings + all ASW objects) | — | ~221 B |
| Whole demo image (LTO + `--gc-sections`), **gated** | ≤ 4096 B / ≤ 384 B | **3566 B Flash / 295 B RAM** |
| Stack + idle RAM (reported separately) | — | 1753 B |

`make -C reference-demo` builds, prints `avr-size` output and **fails the
build** if a budget is exceeded — two gates: the app-agnostic kernel by the
non-LTO `budget` target (`make -C reference-demo budget`), and the whole
shipped LTO image by the `size` target (`image_flash`/`image_ram` in
`app.yaml`). UART rings and PWM are *application* RAM, so they never move
the tiny kernel figure.

## Layout

```
kernel/               app-agnostic kernel, reused by every application
  eros_types.h     types, StatusType set, config record types,
                      MISRA deviation record D1..D8
  eros.h           public API + doc comments (semantics, error codes)
  eros.c           scheduler, tick ISR, alarms, resources, mailbox,
                      pool, stack canary, watchdog, .init3 WDT fix
reference-demo/       the full reference application on the kernel:
                      `make -C reference-demo` — see its README.md
  app.yaml         its configuration ("OIL file"); the config.*/Makefile
                      below are generated from it by tools/erosgen.py
  config.h/.c      generated static config: tasks, priorities, alarms,
                      resources, pool geometry, hooks, aliveness mask
  main.c           integration layer: hooks + one-shot startup task + main()
  asw_10ms.c/.h    TASK_BUTTON — scope PD3 + debounced button, IPC producer
  asw_50ms.c/.h    TASK_CMD — scope PD4 + serial console + IPC consumer
  asw_100ms.c/.h   TASK_RAMP — scope PD5 + Timer1 PWM breathing LED
  asw_500ms.c/.h   TASK_STATUS — scope PD6 + status line + chained
                      TASK_REPORT heartbeat (PB5)
  asw_signals.c/.h cross-rate "rate transition" layer + status print
  actuator.c/.h    polymorphic GPIO driver (OOP-in-C, vtables in PROGMEM)
  uart.c/.h        interrupt-driven USART0 console (ring buffers)
  pwm.c/.h         Timer1 fast-PWM breathing LED (OC1A/PB1)
  Makefile         generated: warning-free flags, size/budget, flash target
drivers/              app-agnostic drivers completing the ATmega328P
                      peripheral coverage: ADC, EEPROM, I2C, SPI,
                      INT/PCINT, Timer0 PWM, Timer1 input capture,
                      analog comparator — pins, WCETs, ISR categories
                      and resource conflicts in its README.md
tools/erosgen.py      system configurator: compiles each app's app.yaml
                      into config.h/config.c/Makefile + ASW skeletons;
                      selects which peripherals compile and sizes the
                      RAM-dominant buffers — see tools/README.md
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
first) — the OSEK "one kernel, per-app static config" model. Any number of
applications can reuse the kernel unchanged, each with its own task set.

That per-app `config.h`/`config.c`/`Makefile` are **generated from a
single `app.yaml`** by `tools/erosgen.py` (the OIL compiler): it picks
which peripheral drivers compile, sizes the RAM-dominant buffers (UART
rings, pool arena), assigns rate-monotonic priorities, and enforces
schedulability and pin/peripheral-conflict rules — an invalid system
cannot be generated. The shipped demo regenerates to a byte-identical
image from its `app.yaml`. See `tools/README.md`.

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

One firmware exercising the whole kernel plus the two peripheral drivers
(`uart.c`, `pwm.c`). See `reference-demo/README.md` for the wiring and
serial protocol.

| Task | Prio | Release | Pins | Purpose |
|---|---|---|---|---|
| `Task_Button` | 5 (highest) | 10 ms | PD3 scope, PD2 in | scope channel; debounced button; IPC producer under `RES_DEMO` |
| `Task_Cmd` | 4 | 50 ms | PD4 scope, UART | scope channel; serial console (`ON`/`OFF`/`STAT`); button-event consumer under `RES_DEMO` |
| `Task_Ramp` | 3 | 100 ms | PD5 scope, PB1 PWM | scope channel; Timer1 PWM breathing LED |
| `Task_Status` | 2 | 500 ms | PD6 scope, UART | scope channel; status line under `RES_UART`; `ChainTask(TASK_REPORT)` every 4th run |
| `Task_Report` | 1 | chained | PB5/D13 | heartbeat (toggles every 2 s) |
| `Task_Startup` | 0 | once (autostart) | UART | banner + reset cause + arms alarms; deliberate double `ActivateTask` → `E_OS_LIMIT` → `ErrorHook` |

Put a scope on D3/D4/D5/D6: you get 50/10/5/1 Hz square waves (one per
rate) whose edge jitter is bounded by ≤ 1 tick activation error + the
longest task WCET (≤ 2 ms steady-state); each toggle is a single atomic
`PINx` write dispatched through the polymorphic actuator. The on-board LED
(PB5/D13) is the 2 s heartbeat (chained `Task_Report`); solid ON from
`ShutdownHook` marks a terminal fault. The deliberate boot-time
double-activation raises `E_OS_LIMIT` in `ErrorHook`, which the first
serial status line reports as `err=1 lastE=..` — PB5 stays a heartbeat,
not an error lamp.

The 500 ms alarm is armed with `SetAbsAlarm` (the others with
`SetRelAlarm`) to exercise the absolute-alarm path. CI builds the image
warning-free with both budget gates enforced and boots it under
`qemu-system-avr` for 3 s of guest time (the smoke job); the peripheral
drivers it links (`uart.c`) are separately proven in the simavr matrix.

## ASW structure & shared data (why there are no mutexes)

The demo follows the structure recommended for Simulink/Embedded Coder
output in `codegen/README.md` §4: **one C/H pair per task rate**
(`asw_10ms.c`, `asw_50ms.c`, …), a thin integration `main.c` (hooks +
startup task only), and *no* application state shared between rate files
through ad-hoc globals. Data crosses a rate boundary in exactly two
sanctioned ways:

1. **Kernel IPC** — pool block + single-slot mailbox, wrapped in a
   `GetResource`/`ReleaseResource` pair (`RES_DEMO`) marking the handoff
   as one logical unit (button press → command task).
2. **A signals module** — `reference-demo/asw_signals.c/.h`, the
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
./eros.sh -build       # build the reference demo into ./build (gitignored)
./eros.sh -flash       # auto-detect the board + baud, then flash
```

`-flash` finds the serial port (`/dev/ttyUSB*`, `/dev/ttyACM*`,
`/dev/cu.usb*`) and the bootloader baud (57600 old-bootloader Nano,
then 115200 Optiboot) by probing the ATmega328P signature; override
either with `EROS_PORT=` / `EROS_BAUD=`.

Or drive the Makefiles directly:

```sh
make -C reference-demo         # build + size + budget check (fails if over budget)
make -C reference-demo flash   # avrdude, old-bootloader Nano (57600 baud)
make -C reference-demo flash BAUD=115200 PORT=/dev/ttyACM0   # Optiboot boards
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

1. **build** — erosgen unit tests, reference demo + kernel/whole-image
   budget gates, and `-Werror` compile gates for the drivers and the
   generated model + RTE.
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
