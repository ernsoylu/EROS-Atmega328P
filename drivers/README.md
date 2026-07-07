# Peripheral drivers — full ATmega328P / Arduino Nano coverage

App-agnostic, kernel-independent drivers (pure avr-libc + registers, no
`eros.h`, no `config.h`) completing the peripheral coverage started by
`reference-demo/uart.c` (USART0) and `pwm.c` (Timer1 PWM — the reference
demo now shares this driver too). Every ISR here is OSEK **Category 1** — it only counts,
timestamps or moves bytes and never calls an OS service; tasks poll
with atomic fetch functions. Blocking calls are hardware-bounded or
timeout-capped so each has a documented WCET for the task budget table.

**MCAL naming (Phase 7, in progress).** Drivers are migrating to
AUTOSAR-MCAL-style module prefixes — `<Mod>_<Verb>` in MixedCase — one module
at a time. Done so far:

- **Adc** — `Adc_Init` / `Adc_ReadChannel` (was `ADC_Init` / `ADC_Read`), plus
  `Adc_ReadVccMillivolts` / `Adc_ReadTempRaw`. Single-channel blocking read; the
  AUTOSAR group/buffer API is not adopted on this 8-bit target.
- **Pwm** (shared `drivers/pwm.c`) — `Pwm_Init` / `Pwm_SetDutyCycle` /
  `Pwm_GetDutyCycle` (was `PWM_*`). Duty stays **permille (0..1000)**, not
  AUTOSAR's 0..0x8000 — documented deviation. This is the RTE-bound driver
  (`bind.py` / `emit/rte.py` emit these names). The reference demo used to ship
  a near-duplicate fixed-1 kHz `pwm.c`; it now **shares this one** (deleted its
  copy — the `pwm:` peripheral resolves to `drivers/pwm.c` at its 1 kHz
  defaults, byte-identical image), so PWM is `Pwm_*` repo-wide.
- **Uart** (`reference-demo/uart.c`) — `Uart_Init` / `Uart_PutChar` /
  `Uart_Print{,_P,U16,Hex8}` / `Uart_GetChar` / `Uart_TxDropped` (was `UART_*`).
  The `UART_TX_SIZE` / `UART_RX_SIZE` geometry macros keep their names (config,
  not interface).

- **Spi / I2c / Eep / Icp / Acomp / T0Pwm** — `SPI_*`→`Spi_*`, `I2C_*`→`I2c_*`,
  `EE_*`→`Eep_*`, `ICP_*`→`Icp_*`, `ACOMP_*`→`Acomp_*`, `T0PWM_*`→`T0Pwm_*`
  (Timer0 PWM stays a module distinct from `Pwm`). Only the **functions** are
  renamed; config macros (`SPI_MODE*` / `SPI_CLK_DIV*`, `ACOMP_IN_*` /
  `ACOMP_EVT_*`, `ADC_REF_*`, `UART_TX_SIZE` / `UART_RX_SIZE`) keep their names —
  they are configuration, not the module interface. `ExtInt_*` / `PcInt_*`
  already conformed.

Every driver now uses AUTOSAR-MCAL-style `<Mod>_<Verb>` names.

**BSW layer topology (Phase 7).** The peripheral drivers live under
**`drivers/mcal/`** (the AUTOSAR **MCAL** layer — Adc/Pwm/Spi/I2c/Icu(icp)/
Gpt(timer0)/Eep(eeprom)/Dio+Icu(extint)/Acomp). The other AUTOSAR layers are:
**Services** = the EROS kernel (`kernel/`: OS, plus EcuM-like startup via
`StartupHook`, watchdog supervision, the mailbox+pool IPC); **ComplexDevice
Driver** = `reference-demo/uart.c` (USART0 console). The generator threads the
`mcal/` subdir through the MCU profile file-map and the Makefile emitter (VPATH
+ `-I` per layer dir; source basenames stay flat), so a bound driver resolves to
`drivers/mcal/<mod>.c` automatically.

**Cyclic `<Mod>_MainFunction` scheduling.** A driver that exposes a cyclic
`<Mod>_MainFunction` (declared in the MCU profile's `main_functions` map — `adc`
ships `Adc_MainFunction`, a non-blocking channel-0 sampler) is scheduled by
setting `peripherals.<p>.main_function_ms: N` in `app.yaml`: erosgen calls it
every N ms from the matching-rate ASW task's **regenerated scaffold** (so it
runs before your USER CODE and stays wired across regeneration). A periodic task
at N ms must exist (`MAIN_FUNCTION_NO_TASK` otherwise); a driver without a
MainFunction is rejected (`MAIN_FUNCTION_UNSUPPORTED`).

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

- `icp` **xor** `pwm`: both own Timer1. Never initialise both.
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
VPATH  := $(KERNEL_DIR) ../drivers/mcal
SRCS   += adc.c i2c.c
CFLAGS += -I../drivers/mcal
```

Init calls belong in `StartupHook()` (interrupts are still disabled
there); periodic use belongs in rate tasks with the WCETs above entered
into the `wcet_ticks` budgets. `make` in this directory is the
warning-free compile gate for all drivers (`libdrivers.a` is a
convenience artifact; apps normally compile driver sources directly so
LTO and `--gc-sections` see exactly what is used).

### Multi-MCU coverage

`make check-mcus` compiles every driver `-Werror` for **atmega328p, atmega2560
and atmega32u4** (the CI driver gate runs this). The 32U4 differs in two ways the
drivers account for: it has no Timer2 (the OS tick lives on Timer3 — see
`kernel/eros_tick.h`) and only one pin-change bank (PORTB), so `extint.c` guards
the PORTC/PORTD (`PCINT1/2`) banks behind `#if defined(...)`. Two functional
notes for the 32U4 (compile-clean, but mind the hardware): `adc.c` reaches ADC
channels 0–7 (covers the Leonardo A0–A5; ADC8–13 would need `MUX5`), and `uart.c`
is USART0/console and is provided per-app (the 32U4 uses USART1 — see its
profile).

## Concurrency contract (same as the rest of the repo)

Tasks cannot interleave on this non-preemptive kernel, so driver calls
from *different tasks* need no locking — but a driver transaction is
only atomic within one task activation. Data shared with the Category-1
ISRs follows the kernel rule: `volatile`, single-byte naturally atomic,
anything wider under `ATOMIC_BLOCK` — already implemented inside the
fetch/get functions, so applications never touch driver internals.
