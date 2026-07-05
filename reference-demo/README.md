# EROS reference demo — a full application on the kernel

A complete small firmware exercising the whole **EROS** (Embedded Realtime
Operating System) **OSEK BCC1 kernel** (compiled from `../kernel/`
unchanged — only the static configuration in this folder differs per
application, the OSEK "one kernel, per-application config" model).

| Feature | Task | Period |
|---|---|---|
| scope channel PD3/D3 + debounced push button PD2/D2 (internal pull-up, 8-sample filter), IPC producer | `Task_Button` | 10 ms |
| scope channel PD4/D4 + serial monitor 9600 8N1 — `ON`/`OFF`/`STAT`, interrupt-driven ring buffers (Category-1 ISRs) — + IPC consumer | `Task_Cmd` | 50 ms |
| scope channel PD5/D5 + Timer1 fast-PWM "breathing" LED on OC1A (PB1/D9), triangle ramp | `Task_Ramp` | 100 ms |
| scope channel PD6/D6 + periodic status line (grouped under `RES_UART`), `ChainTask(TASK_REPORT)` every 4th run | `Task_Status` | 500 ms |
| on-board heartbeat LED PB5/D13 (atomic `PINB` toggle, no delay loop) | `Task_Report` | chained, ~2 s |
| boot banner + reset cause + arm alarms + deliberate `E_OS_LIMIT` demo | `Task_Startup` | once (autostart) |

EROS features on display: **rate-monotonic periodic scheduling** via
alarms (real priorities, overrun detection); a button press allocates a
**memory-pool block** and posts it through the **single-slot mailbox** to
`Task_Cmd` (pool → mailbox → free life cycle) with the handoff guarded by
the `RES_DEMO` **ISR-ceiling resource** (masks the tick); the multi-part
status line is grouped under the `RES_UART` **task-ceiling resource**; a
**`ChainTask`** releases the heartbeat; a deliberate double `ActivateTask`
hits the BCC1 activation limit → **`E_OS_LIMIT`** → `ErrorHook`; the
**watchdog** supervises all four periodic tasks; the banner prints the
**reset cause** captured in `.init3` (`os_resetCause`); the 500 ms alarm
uses **`SetAbsAlarm`** (the rest `SetRelAlarm`); and every GPIO toggle
dispatches through a **polymorphic actuator** (OOP-in-C, vtables +
instances 100 % in Flash — deviation D4).

## Scope jitter channels

Put a scope on D3/D4/D5/D6: 50 / 10 / 5 / 1 Hz square waves, one per task
rate, toggled as the *first* action of each periodic task. Edge jitter is
bounded by ≤ 1 tick activation error (alarms fire inside the tick ISR) +
queueing delay bounded by the largest task WCET (≤ 2 ms steady state — see
the budget table in `config.h`). Each toggle is a single atomic `PINx`
write (writing a 1 to a `PINx` bit toggles the pin in hardware — ATmega328P
datasheet 14.2.2), reached through the actuator's virtual dispatch with the
vtable and instance read from PROGMEM (zero RAM cost).

## File layout — one C file per task rate

The ASW is split by rate, mirroring the Simulink/Embedded Coder structure
documented in `../codegen/README.md` §4:

```
main.c            integration layer: hooks + startup task + main()
asw_10ms.c/.h     TASK_BUTTON  scope PD3 + debounced button + IPC producer
asw_50ms.c/.h     TASK_CMD     scope PD4 + serial console + IPC consumer
asw_100ms.c/.h    TASK_RAMP    scope PD5 + PWM triangle ramp (owns the ramp
                               state, exports Asw_RampReset())
asw_500ms.c/.h    TASK_STATUS  scope PD6 + status line + ChainTask, and
                               TASK_REPORT the chained heartbeat (PB5)
asw_signals.c/.h  cross-rate signals ("rate transition" layer) + the
                  shared status print + ISR-safe error telemetry
actuator.c/.h     polymorphic GPIO driver (OOP-in-C, PROGMEM vtables)
uart.c/.h pwm.c/.h  peripheral drivers   config.h/.c  static OS config
```

State that only one rate touches stays `static` inside that rate's file.
Everything that crosses a rate boundary goes through the `asw_signals`
accessors or kernel IPC — never through shared globals. No mutex/semaphore
is needed for task↔task data on this **non-preemptive run-to-completion**
kernel (tasks can never interleave); `asw_signals.h` documents the full
concurrency contract, including the `volatile`/`ATOMIC_BLOCK` rules for
ISR-shared data and where locking would attach if the kernel ever became
preemptive.

## Wiring

```
D13/PB5 : on-board LED (heartbeat, toggles every 2 s)
D9 /PB1 : LED + resistor to GND (PWM breathing, 4 s cycle)
D2 /PD2 : push button to GND (internal pull-up, active low)
D3..D6  : scope jitter channels (50 / 10 / 5 / 1 Hz square waves)
USB     : serial monitor, 9600 baud 8N1
```

## Serial protocol

Boot banner:

```
EROS reference demo
reset cause MCUSR=0x02  commands: ON | OFF | STAT
```

Every 500 ms a status line (tick counter, PWM duty in permille, ramp run
flag, ErrorHook count + last code, dropped-TX diagnostic):

```
t=12500 duty=650 run=1 err=0 lastE=00 txDrop=0
```

The **first** status line reports `err=1` — that is the intentional
boot-time double-activation arriving in `ErrorHook` as `E_OS_LIMIT`. PB5
stays the heartbeat, so the demonstrated error is visible in the serial
telemetry rather than on the LED.

Commands (CR or LF terminated): `ON` resume ramp · `OFF` freeze ramp,
duty 0 · `STAT` immediate status line. Pressing the button toggles the
ramp too and prints `BTN -> RUN` / `BTN -> HOLD`.

## Driver design notes

- **UART**: a naive polled driver (busy-waiting on `UDRE0`) blocks ~1 ms
  per character at 9600 baud — inside a scheduler that would stall every
  task. Here TX/RX go through ring buffers drained/filled by
  `USART_UDRE`/`USART_RX` interrupts (OSEK **Category 1** ISRs: they never
  call OS services; the kernel tick is the only Category 2 ISR). If the TX
  ring is full, bytes are dropped and counted (`txDrop`) — a task never
  busy-waits. The RX ring (64 B) outruns the wire: 9600 baud delivers at
  most 48 bytes per 50 ms command-task period, so even pasted input
  survives.
- **PWM**: **Timer2 belongs to the kernel tick**, so PWM uses Timer1 (fast
  PWM mode 14, `ICR1`=1999 → 1 kHz, duty in permille). At duty 0 the OC1A
  pin is disconnected from the waveform generator and driven low — in fast
  PWM, `OCR1A = 0` alone would still emit a narrow spike every period
  (ATmega328P datasheet quirk).
- **Actuator**: every heartbeat and scope toggle is one atomic `PINx`
  store, but reached through a `const PROGMEM` vtable + instance
  (`Actuator_Trigger`) — real polymorphism (PortB vs PortD "classes") at
  zero RAM cost. `PINB = (1<<PB5)` is the correct toggle idiom; the
  tempting `PINB |= (1<<PB5)` is a read-modify-write that would toggle
  *every* port pin currently reading high.

## Build & flash

From the repo root, the `eros.sh` helper builds and flashes this demo:

```sh
./eros.sh -build        # -> build/eros/eros.elf/.hex/.map
./eros.sh -flash        # auto-detect the board + baud, then flash
```

Or drive this project's Makefile directly:

```sh
make                                       # eros.elf/.hex/.map + size + budget
make flash                                 # old-bootloader Nano, 57600
make flash BAUD=115200 PORT=/dev/ttyACM0   # Optiboot
```

## Budget (avr-gcc 7.3, `-Os -flto`)

| Item | Limit | Measured |
|---|---|---|
| Kernel Flash (`eros.o`+`config.o`, non-LTO) | ≤ 3072 B | 2000 B |
| Kernel static RAM (`eros.o`, non-LTO) | ≤ 128 B | 42 B |
| Whole image (LTO + `--gc-sections`) | ≤ 4096 B / ≤ 384 B | 3566 B Flash / 295 B RAM |

RAM is dominated by the UART rings (192 B, TX 128 + RX 64) and the pool
arena (32 B). `make` **fails the build** if either gate is exceeded — the
kernel by the non-LTO `budget` target, the whole image by the `size`
target (`image_flash`/`image_ram` in `app.yaml`). UART/PWM are application
RAM, so they never move the tiny kernel figure.

> **Reset-cause caveat:** the `MCUSR=0x..` banner value is meaningful on
> old-bootloader (ATmegaBOOT) boards and bare chips. Optiboot clears
> `MCUSR` before jumping to the application, so Optiboot boards always
> report `0x00`.
