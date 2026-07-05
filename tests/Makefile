# =====================================================================
# EROS simulation test matrix (simavr)
#
# Builds each test as ATmega328P firmware (avr-gcc) that self-checks on
# real registers inside simavr and reports PASS/FAIL over UART, plus a
# native host runner (gcc + libsimavr) that loads the firmware, applies
# stimulus, and turns the sentinel into an exit code.
#
#   make            build everything
#   make test       run the whole matrix (fails on first failing test)
#   make test-T     run one test, e.g. make test-eeprom  (verbose --echo)
#   make clean
#
# Requires: avr-gcc + avr-libc, and libsimavr-dev (headers in
# /usr/include/simavr, links -lsimavr -lelf). On Ubuntu:
#   sudo apt-get install gcc-avr avr-libc libsimavr-dev
# =====================================================================

AVR_CC      ?= avr-gcc
CC          ?= cc

MCU         := atmega328p
F_CPU       := 16000000UL

ROOT        := ..
DRIVERS     := $(ROOT)/drivers
REFDEMO     := $(ROOT)/reference-demo
CODEGEN     := $(ROOT)/codegen
MODEL_RTW   := $(CODEGEN)/appKnbSwt_ert_rtw
RTE         := $(ROOT)/rte
BUILD       := build

AVR_CFLAGS  := -Wall -Wextra -std=gnu99 -Os -g \
               -mmcu=$(MCU) -DF_CPU=$(F_CPU) \
               -ffunction-sections -fdata-sections \
               -I common -I $(DRIVERS) -I $(REFDEMO) \
               -I $(RTE) -I $(MODEL_RTW)
AVR_LDFLAGS := -Wl,--gc-sections

SIMAVR_INC  ?= /usr/include/simavr
HOST_CFLAGS := -Wall -Wextra -O2 -I $(SIMAVR_INC)
HOST_LIBS   := -lsimavr -lelf

RUNNER      := $(BUILD)/runtest

# --- test -> (extra driver sources, runtest stimulus flags) ----------
# Pure-firmware tests need no host stimulus; stimulus tests inject
# ADC voltages / SPI echo / GPIO edges from the host side.

DRV_uart        := $(REFDEMO)/uart.c
DRV_eeprom      := $(DRIVERS)/eeprom.c
DRV_timer0_pwm  := $(DRIVERS)/timer0_pwm.c
DRV_adc         := $(DRIVERS)/adc.c
DRV_spi         := $(DRIVERS)/spi.c
DRV_i2c         := $(DRIVERS)/i2c.c
DRV_extint      := $(DRIVERS)/extint.c
DRV_icp         := $(DRIVERS)/icp.c
DRV_acomp       := $(DRIVERS)/acomp.c

# Simulink model 'appKnbSwt' end-to-end: ASW (generated) + RTE + BSW (adc).
DRV_model_knbswt := $(RTE)/Rte.c \
                    $(MODEL_RTW)/appKnbSwt.c \
                    $(MODEL_RTW)/appKnbSwt_Intfc.c \
                    $(MODEL_RTW)/appKnbSwt_Param.c \
                    $(DRIVERS)/adc.c

# ADC: A0 -> 2500 mV, A1 -> 1100 mV, A2 -> 0 mV (assertions use ordering)
FLAGS_adc       := --adc 0:2500 --adc 1:1100 --adc 2:0
# SPI: host slave returns a constant 0x5A on MISO for every transfer.
FLAGS_spi       := --spi-slave 5A
# INT0 = PD2: idle high, fall at 1ms, rise at 2ms, fall at 3ms -> 2 falls
FLAGS_extint    := --pin D,2,500,1 --pin D,2,1000,0 --pin D,2,2000,1 --pin D,2,3000,0
# ICP1 = PB0: two rising edges 400us apart -> period ~100 ticks (4us/tick)
FLAGS_icp       := --pin B,0,500,0 --pin B,0,1000,1 --pin B,0,1400,0 --pin B,0,1900,1
# Knob on A0: 1023->0 over 5 s then 0->1023 over 5 s; watch DO on PB5.
FLAGS_model_knbswt := --adc-sweep 0:5000:0:5000 --watch-pin B,5 --timeout-ms 13000

PURE_TESTS      := uart eeprom timer0_pwm i2c
STIM_TESTS      := adc spi extint icp acomp model_knbswt
TESTS           := $(PURE_TESTS) $(STIM_TESTS)

ELVES := $(addprefix $(BUILD)/,$(addsuffix .elf,$(TESTS)))

.PHONY: all test test-pure test-stim clean $(addprefix test-,$(TESTS))

all: $(RUNNER) $(ELVES)

$(BUILD):
	mkdir -p $(BUILD)

$(RUNNER): host/runtest.c | $(BUILD)
	$(CC) $(HOST_CFLAGS) -o $@ $< $(HOST_LIBS)

# One pattern rule per test so we can attach the right driver source.
$(BUILD)/%.elf: firmware/test_%.c common/testkit.c | $(BUILD)
	$(AVR_CC) $(AVR_CFLAGS) $(AVR_LDFLAGS) -o $@ $^ $(DRV_$*)

# $(call run_group,<test list>,<fatal:1|0>)
# Uses make's $(foreach) (not a shell loop) so each test's per-test
# $(FLAGS_<name>) resolves at make-time - a shell "for t" cannot index a
# make variable by the loop value.
define run_group
	@fail=0; \
	$(foreach t,$(1), printf '%-14s ' "$(t)"; \
	  if $(RUNNER) $(BUILD)/$(t).elf $(FLAGS_$(t)) >/dev/null 2>$(BUILD)/$(t).log; \
	  then echo PASS; \
	  else echo FAIL; sed 's/^/    /' $(BUILD)/$(t).log; fail=1; fi; ) \
	if [ $(2) -eq 1 ]; then exit $$fail; else exit 0; fi
endef

# Whole matrix, fatal on any failure (local dev default).
test: all
	$(call run_group,$(TESTS),1)

# Deterministic, host-stimulus-free tests: CI gates on these.
test-pure: all
	$(call run_group,$(PURE_TESTS),1)

# Stimulus tests: reported but non-fatal until validated in CI.
test-stim: all
	$(call run_group,$(STIM_TESTS),0)

# Verbose single-test target: make test-eeprom
$(addprefix test-,$(TESTS)): test-%: $(RUNNER) $(BUILD)/%.elf
	$(RUNNER) $(BUILD)/$*.elf $(FLAGS_$*) --echo

clean:
	rm -rf $(BUILD)
