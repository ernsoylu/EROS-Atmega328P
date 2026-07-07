"""AVR code-generation backend: the register/GPIO idioms the emitters render for
the AVR family (DDRx / PORTx / PINx bit manipulation).

mcu/profile owns data that varies by MCU *variant* (ports, board aliases,
toolchain strings); this module owns the *family's* code-gen idioms, so a future
non-AVR target - e.g. ESP32, which uses gpio_set_level(...) instead of register
writes and also needs its own kernel port - has one place to reimplement.

bit_set / bit_clear / bit_read return C *expressions* (no trailing ';'); the
caller adds indentation and the ';'. dio_direction_init returns whole statement
lines (its DDR/PORT column alignment is part of the idiom).
"""


def bit_set(reg, bit):
    """`reg |= (1 << bit)`, masked to uint8_t. Expression, no ';'."""
    return f"{reg} |= (uint8_t)(1u << {bit})"


def bit_clear(reg, bit):
    """`reg &= ~(1 << bit)`, masked to uint8_t. Expression, no ';'."""
    return f"{reg} &= (uint8_t)~(1u << {bit})"


def bit_read(reg, bit):
    """`(reg >> bit) & 1` as a uint8_t value. Expression, no ';'."""
    return f"(uint8_t)(({reg} >> {bit}) & 1u)"


def dio_direction_init(tag, is_output):
    """Rte_Init lines that set a dio port's direction from its RTE_CFG_<tag>_*
    macros (driving an output low). DDR/PORT operators are column-aligned; whole
    statements, no leading indent."""
    ddr, prt, bit = (f"RTE_CFG_{tag}_DDR", f"RTE_CFG_{tag}_PORT",
                     f"RTE_CFG_{tag}_BIT")
    if is_output:
        return [f"{ddr}  |= (uint8_t)(1u << {bit});",
                f"{prt} &= (uint8_t)~(1u << {bit});"]
    return [f"{ddr}  &= (uint8_t)~(1u << {bit});"]


class AvrBackend:
    """The AVR family backend (DDRx/PORTx/PINx idioms) — implements `Backend`.
    Methods delegate to the module functions above, which stay available for
    direct import."""

    name = "avr"

    def bit_set(self, reg, bit):
        return bit_set(reg, bit)

    def bit_clear(self, reg, bit):
        return bit_clear(reg, bit)

    def bit_read(self, reg, bit):
        return bit_read(reg, bit)

    def dio_direction_init(self, tag, is_output):
        return dio_direction_init(tag, is_output)
