# =====================================================================
# EROS peripheral driver library (ATmega328P / Arduino Nano)
#
#   make            compile every driver with the mandated flags
#                   (warning-free gate) and archive libdrivers.a
#   make clean
#
# Applications normally do NOT link the archive: they pull individual
# driver sources in via VPATH + SRCS (the same pattern used for the
# kernel and for Simulink model code - see ../codegen/README.md par.5),
# so --gc-sections and LTO see exactly what is used. This Makefile is
# the compile gate that keeps all drivers building warning-free.
# =====================================================================

MCU     := atmega328p
F_CPU   := 16000000UL

SRCS    := adc.c eeprom.c i2c.c spi.c extint.c timer0_pwm.c icp.c acomp.c
OBJS    := $(SRCS:.c=.o)
DEPS    := $(SRCS:.c=.d)

CC      := avr-gcc
AR      := avr-gcc-ar

# Same mandated flag set as the applications (LTO-capable archive).
CFLAGS  := -Wall -Wextra -Werror -std=c99 -Os -flto \
           -ffunction-sections -fdata-sections -fno-common \
           -mmcu=$(MCU) -DF_CPU=$(F_CPU) \
           -I.

.PHONY: all clean

all: libdrivers.a

libdrivers.a: $(OBJS)
	$(AR) rcs $@ $^

%.o: %.c
	$(CC) $(CFLAGS) -MMD -MP -c -o $@ $<

clean:
	rm -f $(OBJS) $(DEPS) libdrivers.a

-include $(DEPS)
