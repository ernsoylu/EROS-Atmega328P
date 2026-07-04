# =====================================================================
# Comprehensive demo - GPIO, serial console, PWM and IPC integrated on
# the TinyOS kernel (built from source out of ../kernel).
#
#   make            build demo.elf/.hex/.map + size report
#   make flash      program the Nano (old bootloader: 57600 baud;
#                   Optiboot: make flash BAUD=115200)
#   make clean
# =====================================================================

MCU        := atmega328p
F_CPU      := 16000000UL
TARGET     := demo

KERNEL_DIR := ../kernel
VPATH      := $(KERNEL_DIR)

SRCS       := main.c uart.c pwm.c config.c tiny_os.c
OBJS       := $(SRCS:.c=.o)
DEPS       := $(SRCS:.c=.d)

CC         := avr-gcc
OBJCOPY    := avr-objcopy
SIZE       := avr-size
AVRDUDE    := avrdude

PORT       ?= /dev/ttyUSB0
BAUD       ?= 57600

# Same mandated flag set as the root project. -I. must come before the
# kernel include dir so the kernel's '#include "config.h"' resolves to
# THIS application's configuration.
CFLAGS     := -Wall -Wextra -Werror -std=c99 -Os -flto \
              -ffunction-sections -fdata-sections -fno-common \
              -mmcu=$(MCU) -DF_CPU=$(F_CPU) \
              -I. -I$(KERNEL_DIR)
LDFLAGS    := -Wl,--gc-sections -Wl,-Map=$(TARGET).map

.PHONY: all size flash clean

all: $(TARGET).hex size

$(TARGET).elf: $(OBJS)
	$(CC) $(CFLAGS) $(LDFLAGS) -o $@ $^

$(TARGET).hex: $(TARGET).elf
	$(OBJCOPY) -O ihex -R .eeprom $< $@

%.o: %.c
	$(CC) $(CFLAGS) -MMD -MP -c -o $@ $<

size: $(TARGET).elf
	@$(SIZE) -B $(TARGET).elf

flash: $(TARGET).hex
	$(AVRDUDE) -v -p m328p -c arduino -P $(PORT) -b $(BAUD) \
	           -U flash:w:$(TARGET).hex:i

clean:
	rm -rf $(OBJS) $(DEPS) $(TARGET).elf $(TARGET).hex $(TARGET).map

-include $(DEPS)
