"""ATmega328P hardware knowledge tables.

These are the target-specific facts (peripheral->source, pin ownership, hardware
conflicts, driver init/header names). Phase 2 externalizes them into
mcu/atmega328p.yaml behind an MCUProfile loader; keeping them in one module now
makes that a mechanical move.
"""

# Driver name -> source file. Resolution order: application directory
# first (app-local driver like the demo's uart.c), then drivers_dir.
KNOWN_PERIPHERALS = {
    "uart":       "uart.c",
    "pwm":        "pwm.c",        # Timer1 PWM
    "adc":        "adc.c",
    "eeprom":     "eeprom.c",
    "i2c":        "i2c.c",
    "spi":        "spi.c",
    "extint":     "extint.c",
    "timer0_pwm": "timer0_pwm.c",
    "icp":        "icp.c",
    "acomp":      "acomp.c",
}

# Pins each peripheral drives when enabled - the single source of truth
# for conflict detection (replaces hardcoded peripheral-pair lists).
# Timer2 is the kernel tick and owns no application pins. ADC channels
# A0..A5 are only claimed if the app lists them (see peripheral config),
# so plain ADC declares nothing here.
PERIPHERAL_PINS = {
    "uart":       ["PD0", "PD1"],          # RXD, TXD
    "pwm":        ["PB1"],                 # OC1A
    "i2c":        ["PC4", "PC5"],          # SDA, SCL
    "spi":        ["PB2", "PB3", "PB4", "PB5"],  # SS, MOSI, MISO, SCK
    "timer0_pwm": ["PD5", "PD6"],          # OC0B, OC0A
    "icp":        ["PB0"],                 # ICP1
    "acomp":      ["PD7"],                 # AIN1 (AIN0/PD6 added if external)
    "extint":     [],                      # pins are app-assigned
    "eeprom":     [],
    "adc":        [],
}

# Shared-resource conflicts that are not expressible as pin overlap
# (two peripherals contending for the same timer/hardware block).
CONFLICTS_HARD = [
    ("icp", "pwm", "both own Timer1 (capture vs ICR1-as-TOP)"),
]

# Driver name -> Init() call emitted into Board_ConfigurePins().
DRIVER_INIT = {
    "uart":       "UART_Init();",
    "pwm":        "PWM_Init();",
    "adc":        "ADC_Init();",
    "i2c":        "I2C_Init();",
    "spi":        "SPI_Init(SPI_MODE0, SPI_CLK_DIV16);",
    "timer0_pwm": "T0PWM_Init();",
    "icp":        "ICP_Init();",
    # eeprom needs no init; extint/acomp are enabled with app-specific
    # arguments, so they are left to hand-written setup.
}
DRIVER_HEADER = {
    "uart": "uart.h", "pwm": "pwm.h", "adc": "adc.h", "eeprom": "eeprom.h",
    "i2c": "i2c.h", "spi": "spi.h", "extint": "extint.h",
    "timer0_pwm": "timer0_pwm.h", "icp": "icp.h", "acomp": "acomp.h",
}
