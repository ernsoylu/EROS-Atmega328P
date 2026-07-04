# Comprehensive Demo — a full application on TinyOS

A complete small firmware running on the **TinyOS OSEK BCC1 kernel**
(compiled from `../kernel/` unchanged — only the static configuration
in this folder differs from the root demo, demonstrating the OSEK
"one kernel, per-application config" model).

| Feature | Task | Period |
|---|---|---|
| heartbeat LED PB5/D13 (atomic `PINB` toggle, no delay loop) | `Task_Status` | 500 ms |
| debounced push button PD2/D2 (internal pull-up, 8-sample filter) | `Task_Button` | 10 ms |
| serial monitor 9600 8N1, `ON`/`OFF`/`STAT` commands, interrupt-driven (ring buffers, Category-1 ISRs) | `Task_Cmd` | 50 ms |
| Timer1 fast-PWM "breathing" LED on OC1A (PB1/D9), triangle ramp | `Task_Ramp` | 100 ms |
| periodic scheduling via TinyOS alarms (real priorities, overrun detection) | all | — |

TinyOS features on display: a button press allocates a **memory-pool
block**, posts it through the **single-slot mailbox** to `Task_Cmd`
(pool → mailbox → free life cycle), the multi-part status line is
grouped under the `RES_UART` **resource**, the **watchdog** supervises
all four periodic tasks, and the boot banner prints the **reset cause**
captured in `.init3` (`os_resetCause`).

## Wiring

```
D13/PB5 : on-board LED (heartbeat, 1 Hz blink)
D9 /PB1 : LED + resistor to GND (PWM breathing, 4 s cycle)
D2 /PD2 : push button to GND (internal pull-up, active low)
USB     : serial monitor, 9600 baud 8N1
```

## Serial protocol

Boot banner:

```
TinyOS comprehensive demo
reset cause MCUSR=0x02  commands: ON | OFF | STAT
```

Every 500 ms a status line (tick counter, PWM duty in permille, ramp
run flag, ErrorHook count + last code, dropped-TX diagnostic):

```
t=12500 duty=650 run=1 err=0 lastE=00 txDrop=0
```

Commands (CR or LF terminated): `ON` resume ramp · `OFF` freeze ramp,
duty 0 · `STAT` immediate status line. Pressing the button toggles the
ramp too and prints `BTN -> RUN` / `BTN -> HOLD`.

## Driver design notes

- **UART**: a naive polled driver (busy-waiting on `UDRE0`) blocks
  ~1 ms per character at 9600 baud — inside a scheduler that would
  stall every task. Here TX/RX go through ring buffers drained/filled
  by `USART_UDRE`/`USART_RX` interrupts (OSEK **Category 1** ISRs: they
  never call OS services; the kernel tick is the only Category 2 ISR).
  If the TX ring is full, bytes are dropped and counted (`txDrop`) — a
  task never busy-waits. The RX ring (64 B) outruns the wire: 9600 baud
  delivers at most 48 bytes per 50 ms command-task period, so even
  pasted input survives.
- **PWM**: **Timer2 belongs to the kernel tick**, so PWM uses Timer1
  (fast PWM mode 14, `ICR1`=1999 → 1 kHz, duty in permille). At duty 0
  the OC1A pin is disconnected from the waveform generator and driven
  low — in fast PWM, `OCR1A = 0` alone would still emit a narrow spike
  every period (ATmega328P datasheet quirk).
- **Pin toggling**: writing 1 to a `PINx` bit toggles the pin in
  hardware — one atomic store. The tempting `PINB |= (1<<PB5)` is a
  read-modify-write that would toggle *every* pin of the port that
  currently reads high; `PINB = (1<<PB5)` is the correct idiom.

## Build & flash

```sh
make                                       # demo.elf/.hex/.map + size
make flash                                 # old-bootloader Nano, 57600
make flash BAUD=115200 PORT=/dev/ttyACM0   # Optiboot
```

Image (avr-gcc 7.3, `-Os -flto`): ~3.3 KiB Flash, ~291 B RAM — kernel
~35 B, UART rings 192 B (TX 128 + RX 64), pool arena 32 B, app state
the rest.

> **Reset-cause caveat:** the `MCUSR=0x..` banner value is meaningful on
> old-bootloader (ATmegaBOOT) boards and bare chips. Optiboot clears
> `MCUSR` before jumping to the application, so Optiboot boards always
> report `0x00`.
