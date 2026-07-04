# Embedding Simulink-generated code on TinyOS

This folder receives C code generated from Simulink models (Embedded
Coder, `ert.tlc`) and documents the contract between a model and the
TinyOS scheduler: how to configure the model, which data types to use,
how root-level I/O binds to the GPIO/PWM/ADC drivers, and how the
generated entry points are wired to OS tasks and alarms.

## Folder layout

```
codegen/
  README.md            this guide
  <model>_ert_rtw/     generated code, copied verbatim from Simulink
                       (never edit generated files - regenerate instead)
  <model>_glue.c/.h    hand-written integration per model: task bodies,
                       I/O sampling/actuation, driver includes
```

Sources and headers are added to the application Makefile via `VPATH` +
`-I` — the complete worked example is in **§5**. The generated code must
compile warning-free under the project flags (`-Wall -Wextra -Werror
-std=c99 -Os -flto`); Embedded Coder output does when the model is
configured as described below.

## 1. Model configuration (Configuration Parameters)

### Solver
| Setting | Value | Why |
|---|---|---|
| Type / Solver | Fixed-step, `discrete (no continuous states)` | TinyOS drives steps from a 1 kHz tick; no ODE solver exists on target |
| Fixed-step size (base rate) | integer multiple of **0.001 s** | the OS tick is 1 ms (`OS_TICK_HZ = 1000`) |
| Sample times | harmonic multiples of the base rate, each ≤ 32.767 s | cyclic alarms take periods 1..32767 ticks; harmonic rates (e.g. 0.01 / 0.05 / 0.1 / 0.5) keep release points aligned |
| Tasking mode | `Treat each discrete rate as a separate task` (multitasking) for per-rate OS tasks, or single-tasking for small models (see §4) | |
| Higher priority value indicates higher task priority | **checked** | matches TinyOS (TaskID == priority, higher = more urgent) |
| Automatically handle rate transition for data | checked, `Ensure deterministic data transfer` | inserts the Rate Transition logic the scheduler mapping in §4 relies on |

### Hardware Implementation
Device vendor `Atmel` (or `Microchip`), device type `AVR` — verify the
word sizes match avr-gcc on the ATmega328P:

| C type | bits | Note |
|---|---|---|
| `char` | 8 | plain `char` is **signed** on avr-gcc |
| `short` | 16 | |
| `int` | **16** | the usual trap when porting from 32-bit targets |
| `long` | 32 | |
| `long long` | 64 | works, but very expensive - avoid |
| `float` | 32 | software-emulated (no FPU) |
| `double` | **32** | avr-gcc ≤ 7 has no 64-bit double; set it to 32 so `real_T` becomes a 32-bit float and simulation matches the target |
| pointer | 16 | |
| Byte ordering | Little Endian | |
| Signed shift right | arithmetic | |
| Integer division rounds to | Zero | |

### Code Generation / Interface
| Setting | Value | Why |
|---|---|---|
| System target file | `ert.tlc` | bare-metal footprint; `grt.tlc` drags in a main and timing engine |
| Language | C (C99 constructs allowed) | project builds `-std=c99` |
| Dynamic memory allocation | **off** | TinyOS forbids heap use (`malloc` is banned project-wide) |
| Support: continuous time / non-finite numbers / complex | off | dead flash otherwise |
| Support: absolute time | off if possible | otherwise see "Absolute time" in §4 |
| Remove error status field | checked | no `rtmGetErrorStatus` plumbing needed |
| Terminate function required | off | the OS never shuts a model down |
| MAT-file logging / external mode | off | |
| Code interface packaging | `Nonreusable function` | static singleton data - same model as the kernel's static config |
| Pass root-level I/O as | `Part of model data structure` or exported globals | glue code reads/writes them around each step (§3) |

With multitasking enabled Embedded Coder emits one entry point per
rate — `ctrl_step0()` (base rate), `ctrl_step1()`, … plus
`ctrl_initialize()` — which is exactly the granularity the scheduler
mapping in §4 consumes.

## 2. Data types

The ATmega328P is an 8-bit CPU without FPU: every widening costs real
cycles (rough per-operation costs: 8-bit ≈ 1, 16-bit ≈ 2–4, 32-bit
≈ 5–10, float ≈ 100+, 64-bit/double-64: avoid entirely).

**Rules for model interfaces and signals:**

- Prefer `uint8`/`int8`, `uint16`/`int16`; use `int32` only where the
  dynamic range demands it. `boolean` for anything two-valued.
- Prefer **fixed point** (`fixdt(1,16,N)` etc.) over floating point for
  control math; Embedded Coder generates pure integer code for it.
- `single` (32-bit float) is acceptable in slow rates (≥ 100 ms) when
  fixed point is impractical; never use `double` in the model — and set
  hardware `double` = 32 bits so any leftover `real_T` stays 32-bit.
- No `int64`/`uint64`, no complex, no variable-size signals.
- Natural interface widths for this board:

| Physical I/O | Model type | Range / unit |
|---|---|---|
| GPIO in (button, switch) | `boolean` | debounced level |
| GPIO out (LED, relay) | `boolean` | |
| PWM duty (drivers in this repo) | `uint16` | permille, 0..1000 |
| ADC channel (10-bit) | `uint16` | raw 0..1023 (scale inside the model, ideally to fixed point) |
| Tick timestamp | `uint16` | ms, wraps at 65536 (see wrap-safe arithmetic in `kernel/`) |

`rtwtypes.h` is generated to match the Hardware Implementation pane, so
`int_T` = 16 bit, `real_T` = 32-bit float on this target — another
reason the pane must be set correctly.

## 3. Connecting root Inports/Outports to GPIO, PWM, ADC

Generated code never touches hardware. The hand-written glue task
samples physical inputs **before** the step and actuates outputs
**after** it — the sample-compute-actuate pattern, one task per rate:

```c
void Task_Ctrl10ms(void)              /* released by a 10 ms alarm */
{
    /* sample: hardware -> root inports */
    ctrl_U.button   = ((PIND & (1u << PD2)) == 0u);   /* active low  */
    ctrl_U.feedback = ADC_Read(0u);                   /* A0, 0..1023 */

    ctrl_step0();                     /* compute (generated code)    */

    /* actuate: root outports -> hardware */
    PWM_SetDutyPermille(ctrl_Y.duty_permille);        /* PB1 / D9    */
    if (ctrl_Y.led) { PORTB |= (1u << PB5); }
    else            { PORTB &= (uint8_t)~(1u << PB5); }

    TerminateTask();
}
```

Available drivers and how to bind them:

- **GPIO** — read `PINx`, write `PORTx` directly in the glue (direction
  and pull-ups configured once in `StartupHook()`). For clean toggles
  use the hardware toggle `PINx = (1<<bit)` (single atomic store).
- **PWM** — `comprehensive-demo/pwm.c`: Timer1 fast PWM, 1 kHz on
  OC1A/PB1 (D9); `PWM_SetDutyPermille(uint16 0..1000)`, true-off at 0.
  OC1B/PB2 (D10) can be added the same way. **Timer2 is the OS tick —
  drivers must never touch it**; Timer0 is still free.
- **ADC** — no driver in the repo yet; the canonical blocking read
  (~110 µs at the standard 125 kHz ADC clock, fine inside a task) is:

```c
void ADC_Init(void)          /* call from StartupHook() */
{
    ADMUX  = (1u << REFS0);                /* AVcc reference        */
    ADCSRA = (1u << ADEN) | (1u << ADPS2)  /* enable, /128 presc.   */
           | (1u << ADPS1) | (1u << ADPS0);
}

uint16_t ADC_Read(uint8_t channel)         /* A0..A7 -> 0..1023 */
{
    ADMUX = (uint8_t)((ADMUX & 0xF0u) | (channel & 0x0Fu));
    ADCSRA |= (1u << ADSC);
    while ((ADCSRA & (1u << ADSC)) != 0u) { /* ~13 ADC cycles */ }
    return ADC;
}
```

- **UART** — `comprehensive-demo/uart.c` (interrupt-driven,
  non-blocking) for telemetry/tuning. Print from a *slow* housekeeping
  task, never from control rates or hooks.

Sampling in-task (not in ISRs) keeps every ISR in this system Category
1/2 compliant: generated step functions must never be called from an
interrupt.

## 4. Linking the generated top level to the OS scheduler

### Rate → task → alarm mapping (multitasking models)

One TinyOS task and one cyclic alarm per model rate, priorities
**rate-monotonic** (faster rate = higher TaskID = higher priority):

```
model rate      entry point    TinyOS task (prio)      alarm period
0.01 s (base)   ctrl_step0()   TASK_CTRL_10MS  (high)  10 ticks
0.1  s          ctrl_step1()   TASK_CTRL_100MS (low)   100 ticks
```

`config.h` / `config.c` additions follow the existing pattern — tasks
with WCET budgets, alarms bound to them:

```c
/* config.h */
#define TASK_CTRL_100MS  ((TaskType)1u)
#define TASK_CTRL_10MS   ((TaskType)2u)   /* faster => higher priority */
#define ALARM_CTRL_100MS ((AlarmType)0u)
#define ALARM_CTRL_10MS  ((AlarmType)1u)

/* config.c */
[TASK_CTRL_10MS]  = { Task_Ctrl10ms,  0u, 2u /* WCET ticks */ },
[TASK_CTRL_100MS] = { Task_Ctrl100ms, 0u, 5u },
...
[ALARM_CTRL_10MS]  = { TASK_CTRL_10MS  },
[ALARM_CTRL_100MS] = { TASK_CTRL_100MS },
```

Initialisation and alarm start belong in the autostart init task —
**aligned releases, no offsets** (see "Why alignment matters"):

```c
void Task_Init(void)
{
    ctrl_initialize();                     /* generated init          */
    (void)SetRelAlarm(ALARM_CTRL_10MS,  10u, 10u);
    (void)SetRelAlarm(ALARM_CTRL_100MS, 10u, 100u);  /* same offset! */
    TerminateTask();
}
```

### Why this mapping preserves Simulink's multitasking semantics

Embedded Coder's deterministic rate transitions assume: (a) the faster
task executes before the slower one whenever both are released at the
same instant, and (b) a step function is never interleaved with another
step of the same model. Under TinyOS both hold, with one twist:

- At a common release point the tick ISR readies both tasks in the same
  1 ms interrupt; the dispatcher then always picks the higher priority
  (= faster) one first → (a) holds.
- The kernel is **non-preemptive**: once a step starts it runs to
  completion, so steps never interleave at all → (b) holds trivially —
  the Rate Transition double-buffers are simply never contended.

The price of non-preemption is **blocking, not corruption**: a fast
task released while a slow step is running waits for it to finish.
Hence the schedulability rule for this kernel:

> **WCET(every single step) + WCET(all steps sharing a release point)
> must fit inside the base period.** In practice: keep each step's WCET
> ≤ 1–2 ms and the sum of steps released together under 10 ms for a
> 10 ms base rate.

Enter the measured WCETs (profile with `GetCounterValue()` deltas or a
scope pin) into the `wcet_ticks` config field — the dispatcher then
flags overruns via `ErrorHook(E_OS_LIMIT)`, and a cyclic alarm firing
into a still-READY/RUNNING task reports the same code: **deadline
misses are detected for free**, they are never silent.

### Single-tasking alternative (small models)

For models whose total step time is well under the base period, set
tasking to single-tasking: Embedded Coder emits one `ctrl_step()` that
internally sequences the sub-rates. Map it to **one** TinyOS task +
one base-rate alarm and skip rate transitions entirely. Simpler, and
the right default until profiling says otherwise.

### Absolute time

If any block needs absolute time (`Clock`, `Digital Clock`, timers),
either leave "support absolute time" on and accept Embedded Coder's
internal counters (they tick once per base-rate step — correct as long
as no steps are skipped), or model time yourself from
`GetCounterValue()` (uint16 ms, wraps at 65.536 s — use the wrap-safe
`(int16_t)(now - then)` idiom from the kernel for intervals).

## 5. Makefile integration — worked example

A typical Embedded Coder output for a model named `ctrl`, plus the
hand-written glue, lands in this folder like so:

```
codegen/
  ctrl_glue.c          hand-written: task bodies + I/O binding (§3, §4)
  ctrl_glue.h          task prototypes for config.h
  ctrl_ert_rtw/        generated - do not edit
    ctrl.c             ctrl_initialize() + ctrl_step0()/step1()/...
    ctrl.h             ctrl_U / ctrl_Y interface structs, entry points
    ctrl_types.h       model-scoped typedefs
    ctrl_private.h     internals
    ctrl_data.c        parameter tables (only with a separate data file)
    rtwtypes.h         target-typed typedefs (matches §1 hardware pane)
    ert_main.c         example main - MUST NOT be compiled (see below)
```

Integrating into `comprehensive-demo/Makefile` (same recipe for the
root demo — only the relative paths change). The three touch points are
`MODEL_DIR`, `VPATH`/`SRCS`, and `CFLAGS`:

```make
 KERNEL_DIR := ../kernel
+MODEL_DIR  := ../codegen/ctrl_ert_rtw
-VPATH      := $(KERNEL_DIR)
+VPATH      := $(KERNEL_DIR) ../codegen $(MODEL_DIR)

-SRCS       := main.c uart.c pwm.c config.c tiny_os.c
+SRCS       := main.c uart.c pwm.c config.c tiny_os.c \
+              ctrl_glue.c ctrl.c ctrl_data.c

 CFLAGS     := -Wall -Wextra -Werror -std=c99 -Os -flto \
               -ffunction-sections -fdata-sections -fno-common \
               -mmcu=$(MCU) -DF_CPU=$(F_CPU) \
-              -I. -I$(KERNEL_DIR)
+              -I. -I$(KERNEL_DIR) -I../codegen -I$(MODEL_DIR)
```

That is all — the existing pattern rules do the rest:

- **Sources**: `SRCS` lists bare file names; `make` finds them through
  `VPATH` (exactly how `tiny_os.c` is already pulled from the kernel
  directory). Objects and `.d` files derive automatically from
  `OBJS := $(SRCS:.c=.o)`.
- **Headers** are not listed anywhere: they are resolved through the
  `-I` paths at compile time, and the existing `-MMD -MP` flags record
  every header each object actually used into `.d` files (picked up by
  `-include $(DEPS)`) — so regenerating the model or touching
  `ctrl.h`/`rtwtypes.h` rebuilds exactly the affected objects. Keep
  `-I.` **first** so the kernel's `#include "config.h"` still resolves
  to the application's config.
- **`ert_main.c` must be excluded** — it contains Embedded Coder's
  example `main()`, which collides with the application's `main()`
  (one-definition rule) at link time. Either untick *"Generate an
  example main program"* in the model, or simply never list it in
  `SRCS`. If you prefer wildcards over explicit lists, filter it out:

```make
MODEL_SRCS := $(filter-out ert_main.c,$(notdir $(wildcard $(MODEL_DIR)/*.c)))
SRCS       := main.c uart.c pwm.c config.c tiny_os.c ctrl_glue.c $(MODEL_SRCS)
```

- **Budgets**: model + glue objects count as *application* code, so the
  root `make budget` kernel gate (≤ 3 KiB / ≤ 128 B) is unaffected;
  watch the whole-image `avr-size` line instead — the model usually
  dominates flash. `--gc-sections` + `-ffunction-sections` (already in
  the flags) discard generated functions you never call.

## 6. Checklist per integrated model

1. Rates are multiples of 1 ms, harmonic, ≤ 32767 ms.
2. Hardware Implementation pane matches the avr-gcc table in §1.
3. No heap, no `double`, no 64-bit, no continuous states.
4. One task + one cyclic alarm per rate, rate-monotonic priorities,
   aligned alarm offsets; `*_initialize()` in the init task.
5. Glue tasks sample → step → actuate; no hardware access from
   generated code; no step calls from ISRs.
6. Measured WCETs entered in `wcet_ticks`; model tasks added to
   `OS_ALIVE_REQUIRED_MASK` so the watchdog supervises them.
7. Sources added via `VPATH` + `SRCS`, headers via `-I`, `ert_main.c`
   excluded (§5).
8. `make` stays warning-free and within the flash/RAM budgets
   (`make budget` at the repo root fails the build if the kernel
   budgets break; check `avr-size` for the whole image — the model
   usually dominates).
