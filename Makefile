# =====================================================================
# TinyOS - OSEK BCC1 kernel for Arduino Nano (ATmega328P @ 16 MHz)
#
# Targets:
#   make / make all   build tinyos.elf + tinyos.hex + tinyos.map,
#                     print section sizes, enforce the memory budgets
#   make size         avr-size report for the final (LTO) image
#   make budget       kernel budget check on a non-LTO reference build:
#                       kernel Flash (tiny_os.o + config.o) <= 3072 B
#                       kernel static RAM (tiny_os.o)       <= 128 B
#                     Pool arena and stack are excluded from the kernel
#                     RAM budget and reported separately.
#   make flash        program via the Nano bootloader
#   make clean        remove build products
#
# Arduino Nano bootloader quirk: boards with the OLD bootloader
# (ATmegaBOOT, most clones) need -c arduino -b 57600. Boards re-burned
# with Optiboot ("new bootloader") use -b 115200 instead:
#   make flash BAUD=115200
# =====================================================================

MCU     := atmega328p
F_CPU   := 16000000UL
TARGET  := tinyos

# Kernel sources live in kernel/ (app-agnostic); this directory holds the
# application: static configuration (config.h/config.c) and the demo tasks
# (main.c). The kernel's '#include "config.h"' resolves to the application
# directory via -I. - the classic OSEK kernel + per-app OIL layout.
VPATH   := kernel

SRCS    := main.c tiny_os.c config.c
OBJS    := $(SRCS:.c=.o)
DEPS    := $(SRCS:.c=.d)

CC      := avr-gcc
OBJCOPY := avr-objcopy
SIZE    := avr-size
AVRDUDE := avrdude

PORT    ?= /dev/ttyUSB0
BAUD    ?= 57600          # old-bootloader Nano; Optiboot: 115200

# Mandated build flags - the code must compile warning-free with these.
# (-fno-common is added on top: it is the default from GCC 10 onwards and
# makes avr-size attribute zero-initialised globals to their object file,
# which the 'budget' target relies on.)
CFLAGS  := -Wall -Wextra -Werror -std=c99 -Os -flto \
           -ffunction-sections -fdata-sections -fno-common \
           -mmcu=$(MCU) -DF_CPU=$(F_CPU) \
           -I. -Ikernel
LDFLAGS := -Wl,--gc-sections -Wl,-Map=$(TARGET).map

# Budget reference build: identical flags minus LTO, so avr-size can
# attribute Flash/RAM per translation unit.
BUDGET_DIR    := build_budget
CFLAGS_NOLTO  := $(filter-out -flto,$(CFLAGS))

# Kernel memory budgets (bytes)
FLASH_BUDGET  := 3072
RAM_BUDGET    := 128

.PHONY: all size budget flash clean

all: $(TARGET).hex size budget

$(TARGET).elf: $(OBJS)
	$(CC) $(CFLAGS) $(LDFLAGS) -o $@ $^

$(TARGET).hex: $(TARGET).elf
	$(OBJCOPY) -O ihex -R .eeprom $< $@

%.o: %.c
	$(CC) $(CFLAGS) -MMD -MP -c -o $@ $<

size: $(TARGET).elf
	@echo "---- final image (LTO) --------------------------------------"
	@$(SIZE) -B $(TARGET).elf

$(BUDGET_DIR):
	mkdir -p $(BUDGET_DIR)

$(BUDGET_DIR)/%.o: %.c | $(BUDGET_DIR)
	$(CC) $(CFLAGS_NOLTO) -MMD -MP -c -o $@ $<

# 2 KiB SRAM total; whatever the statics do not use is stack headroom.
SRAM_TOTAL := 2048

budget: $(BUDGET_DIR)/tiny_os.o $(BUDGET_DIR)/config.o $(BUDGET_DIR)/main.o
	@echo "---- kernel budget check (non-LTO reference build) ----------"
	@$(SIZE) -B $(BUDGET_DIR)/tiny_os.o $(BUDGET_DIR)/config.o \
	         $(BUDGET_DIR)/main.o | awk ' \
	  NR==2 { kflash += $$1 + $$2; kram   = $$2 + $$3 } \
	  NR==3 { kflash += $$1 + $$2; arena  = $$2 + $$3 } \
	  NR==4 { appram  = $$2 + $$3 } \
	  END { \
	    printf("kernel Flash (tiny_os.o+config.o) : %4d / %d bytes\n", \
	           kflash, $(FLASH_BUDGET)); \
	    printf("kernel static RAM (tiny_os.o)     : %4d / %d bytes\n", \
	           kram, $(RAM_BUDGET)); \
	    printf("pool arena (config.o, excluded)   : %4d bytes\n", arena); \
	    printf("application RAM (main.o)          : %4d bytes\n", appram); \
	    printf("stack + idle RAM (of %d total)  : %4d bytes\n", \
	           $(SRAM_TOTAL), $(SRAM_TOTAL) - kram - arena - appram); \
	    if (kflash > $(FLASH_BUDGET) || kram > $(RAM_BUDGET)) { \
	      printf("BUDGET EXCEEDED\n"); exit 1; \
	    } else { \
	      printf("budgets OK\n"); \
	    } \
	  }'

flash: $(TARGET).hex
	$(AVRDUDE) -v -p m328p -c arduino -P $(PORT) -b $(BAUD) \
	           -U flash:w:$(TARGET).hex:i

clean:
	rm -rf $(OBJS) $(DEPS) $(BUDGET_DIR) \
	       $(TARGET).elf $(TARGET).hex $(TARGET).map

-include $(DEPS)
-include $(addprefix $(BUDGET_DIR)/,$(DEPS))
