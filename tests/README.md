# EROS simulation tests

Peripheral- and kernel-level tests that **execute the real firmware in a
simulator** — the CI equivalent of putting a Nano on a bench.

## Why simavr and not Renode

Renode is an excellent whole-SoC simulator, but it has **no AVR CPU
core** — it targets ARM, RISC-V, POWER, SPARC, Xtensa, etc. The
ATmega328P is 8-bit AVR, so Renode cannot execute this firmware at all
(adding an AVR core is a long-standing unimplemented request upstream).

[**simavr**](https://github.com/buserror/simavr) is the AVR-native
equivalent: it runs the exact `.elf` we flash to hardware and models the
timers, USART, GPIO, ADC, TWI/I²C, SPI, EEPROM and the interrupt
controller — precisely the peripherals these drivers touch. That is what
lets the driver matrix be tested for real.

`qemu-system-avr -machine uno` also runs ATmega328P firmware, but models
far fewer peripherals; it is used only for an independent boot smoke test.

## How a test works

Each test is **AVR firmware that checks itself on real registers** inside
the simulator and reports a one-line verdict over USART0:

```
EROS-TEST: PASS
EROS-TEST: FAIL <tag>
```

- `common/testkit.[ch]` — on-chip `TK_ASSERT` + a polled-UART report
  channel (deliberately independent of the interrupt-driven production
  UART driver, so a test can exercise that driver without a conflict).
- `firmware/test_<peripheral>.c` — one image per peripheral, linking the
  real driver source from `../drivers` (or `../reference-demo`).
- `host/runtest.c` — links `libsimavr`, loads the ELF, applies external
  stimulus (ADC volts, GPIO edges, SPI slave echo), captures the UART
  stream, and turns the sentinel into an exit code (`0` pass, `1` fail,
  `2` timeout).

## Test matrix

| Test          | Driver              | Kind      | What it proves |
|---------------|---------------------|-----------|----------------|
| `uart`        | reference-demo/uart.c | firmware | TX ring + UDRE ISR deliver a well-formed line |
| `eeprom`      | drivers/eeprom.c    | firmware  | read/update/wear-skip/block, out-of-range |
| `timer0_pwm`  | drivers/timer0_pwm.c| firmware  | mode 3, /64 prescaler, connect/true-0%/100% |
| `i2c`         | drivers/i2c.c       | firmware  | TWI enable + bounded NACK/timeout (no hang) |
| `adc`         | drivers/adc.c       | stimulus  | mux + conversion, monotonic in injected volts |
| `spi`         | drivers/spi.c       | stimulus  | master mode + full-duplex loopback |
| `extint`      | drivers/extint.c    | stimulus  | INT0 falling-edge count + clear-on-read |
| `icp`         | drivers/icp.c       | stimulus  | Timer1 capture period/pulse from a pulse train |
| `acomp`       | drivers/acomp.c     | stimulus  | comparator register/smoke (no analog model) |
| `model_knbswt`| ASW→RTE→BSW (Simulink `appKnbSwt`) | stimulus | knob on A0 swept 1023→0→1023 over 10 s drives the model; DO pin switches once each way at the ~25 % threshold |

The `model_knbswt` test exercises the full **ASW → RTE → BSW** chain (see
`rte/README.md`): the generated Simulink model, the hand-written RTE that
binds its ports to the ADC and a digital output, and the drivers. The
host ramps ADC A0 (`--adc-sweep 0:5000:0:5000`) and watches the DO pin
(`--watch-pin B,5`); the firmware self-checks that the LED switches
exactly once on the way down and once on the way up, near raw count 256.

**Pure-firmware** tests are fully deterministic and gate CI
(`make test-pure`). **Stimulus** tests depend on simavr's modelling of
the relevant analog/bus peripheral and run non-gating (`make test-stim`)
until each is validated against a CI run, then promoted to the gate.

## Running

```bash
sudo apt-get install gcc-avr avr-libc libsimavr-dev simavr   # once
make            # build firmware + host runner
make test       # whole matrix (fatal on any failure)
make test-pure  # deterministic subset only
make test-eeprom --  # single test, verbose (UART echoed to stderr)
```

The GitHub Actions pipeline (`.github/workflows/ci.yml`) runs the build +
memory-budget gate, this simavr matrix, and the qemu boot smoke on every
push and PR.
