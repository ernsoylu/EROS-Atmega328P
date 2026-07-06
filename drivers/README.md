# Peripheral drivers — full ATmega328P / Arduino Nano coverage

App-agnostic, kernel-independent drivers (pure avr-libc + registers, no
`eros.h`, no `config.h`) completing the peripheral coverage started by
`reference-demo/uart.c` (USART0) and `reference-demo/pwm.c`
(Timer1 PWM). Every ISR here is OSEK **Category 1** — it only counts,
timestamps or moves bytes and never calls an OS service; tasks poll
with atomic fetch functions. Blocking calls are hardware-bounded or
timeout-capped so each has a documented WCET for the task budget table.

**MCAL naming (Phase 7, in progress).** Drivers are migrating to
AUTOSAR-MCAL-style module prefixes — `<Mod>_<Verb>` in MixedCase. The ADC
module leads: `Adc_Init` / `Adc_ReadChannel` (was `ADC_Init` / `ADC_Read`),
plus `Adc_ReadVccMillivolts` / `Adc_ReadTempRaw`; the RTE generator
(`bind.py` / `emit/rte.py`) and the MCU profiles emit these names. Semantics
are unchanged (single-channel blocking read; the AUTOSAR group/buffer API is
not adopted on this 8-bit target). The remaining modules and the physical
MCAL/Services/CDD directory topology follow in later increments; note
`reference-demo/`'s app-local `pwm.c`/`uart.c` keep their `PWM_*`/`UART_*`
names (they predate the shared drivers and are compiled into that demo).

| Driver | Peripheral | Nano pins | ISRs | WCET notes |
|---|---|---|---|---|
| `adc` | 10-bit ADC, 8 ch + Vcc/temp internal | A0–A7 | none | ~104 µs/read, ~350 µs internal |
| `eeprom` | 1 KiB data EEPROM, wear-aware update | — | none | read ~4 µs; changed byte ~3.4 ms |
| `i2c` | TWI master @ 100 kHz | A4 SDA, A5 SCL | none | ~90 µs/byte, timeout-capped |
| `spi` | SPI master, modes 0–3, /2../128 | D10–D13 | none | 1–64 µs/byte |
| `extint` | INT0/INT1 + all 3 PCINT banks | D2, D3, any | Cat 1 counters | calls ~µs; poll from a task |
| `timer0_pwm` | Timer0 fast PWM, 976.6 Hz | D6 (OC0A), D5 (OC0B) | none | ~µs |
| `icp` | Timer1 input capture: frequency/duty | D8 (ICP1) | Cat 1 capture | ~µs; ceiling ~10 kHz input |
| `acomp` | Analog comparator (+bandgap option) | D6/D7 | Cat 1 counter | ~µs |

Deliberately **not** drivers: Timer2 (kernel tick — untouchable),
watchdog & sleep (kernel supervision/idle policy), USART0 (exists in
`reference-demo/uart.c`), USART-MSPIM (niche — only useful when
hardware SPI is occupied), debugWIRE/SPM self-programming (out of
scope for application firmware).

## Resource conflicts — read before combining

- `icp` **xor** `reference-demo/pwm.c`: both own Timer1. Never
  initialise both.
- `spi` claims PB5/D13 (SCK) — the on-board LED. The demo uses PB5 as
  the heartbeat / hook indicator: move it before enabling SPI.
- `timer0_pwm` (OC0A/PD6) conflicts with `acomp` in `ACOMP_IN_AIN0`
  mode (AIN0 = PD6); with the bandgap positive input they coexist.
- `i2c` costs A4/A5 as ADC channels; `timer0_pwm` costs D5 and D6 (the
  demo's 5 Hz / 1 Hz scope channels).
- INT0/INT1 = D2/D3 are used as plain GPIOs by the demo (button on D2,
  scope channel on D3) — polling and `extint` on the same pin both work,
  just be deliberate about who owns the pin.

## Using a driver in an application

Same `VPATH` + `SRCS` + `-I` recipe as the kernel and Simulink model
code (`../codegen/README.md` §5). In the app Makefile:

```make
VPATH  := $(KERNEL_DIR) ../drivers
SRCS   += adc.c i2c.c
CFLAGS += -I../drivers
```

Init calls belong in `StartupHook()` (interrupts are still disabled
there); periodic use belongs in rate tasks with the WCETs above entered
into the `wcet_ticks` budgets. `make` in this directory is the
warning-free compile gate for all drivers (`libdrivers.a` is a
convenience artifact; apps normally compile driver sources directly so
LTO and `--gc-sections` see exactly what is used).

## Concurrency contract (same as the rest of the repo)

Tasks cannot interleave on this non-preemptive kernel, so driver calls
from *different tasks* need no locking — but a driver transaction is
only atomic within one task activation. Data shared with the Category-1
ISRs follows the kernel rule: `volatile`, single-byte naturally atomic,
anything wider under `ATOMIC_BLOCK` — already implemented inside the
fetch/get functions, so applications never touch driver internals.
