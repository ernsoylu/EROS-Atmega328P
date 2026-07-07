"""The Backend interface: the CPU-family code-gen idioms the emitters render.

A backend turns abstract register/GPIO operations into the C the target's
compiler accepts. AVR (`AvrBackend`) is the only backend; non-AVR families are
out of scope. The emitters read this interface — via the `AVR` default instance
from `for_profile()` — instead of importing a concrete module, so the idioms
live in one swappable place: the code-gen counterpart to `mcu/profile` (per-MCU
*variant* data) for per-*family* idioms.
"""
from typing import Protocol, runtime_checkable


@runtime_checkable
class Backend(Protocol):
    """Structural interface a code-gen backend implements. Bit helpers return C
    *expressions* (no trailing ';'); the caller adds indentation and the ';'."""

    name: str

    def bit_set(self, reg: str, bit: str) -> str:
        """`reg |= (1 << bit)` as a C expression."""
        ...

    def bit_clear(self, reg: str, bit: str) -> str:
        """`reg &= ~(1 << bit)` as a C expression."""
        ...

    def bit_read(self, reg: str, bit: str) -> str:
        """`(reg >> bit) & 1` as a C value expression."""
        ...

    def dio_direction_init(self, tag: str, is_output: bool) -> list:
        """Whole Rte_Init statement lines setting a dio port's direction."""
        ...
