"""Phase 12 - ASAP2 / A2L description (opt-in, CLI ``--project``).

Emit a minimal, valid ASAP2 (A2L) file describing the SWC interface for a
calibration / measurement tool: one ``MEASUREMENT`` per bound port signal and
one ``CHARACTERISTIC`` per extern calibration parameter. Addresses are left at
0x0 - this is the *static* description; a downstream step patches ECU_ADDRESS
from the linker map. ``#define`` calibrations are macros (no RAM address) and are
skipped.
"""
from ..constants import GENERATED_BANNER

# rtw C type -> A2L datatype. Anything unknown falls back to UBYTE.
_A2L_TYPE = {
    "uint8_T": "UBYTE", "int8_T": "SBYTE", "boolean_T": "UBYTE",
    "uint16_T": "UWORD", "int16_T": "SWORD",
    "uint32_T": "ULONG", "int32_T": "SLONG",
    "real32_T": "FLOAT32_IEEE", "real64_T": "FLOAT64_IEEE",
    "float": "FLOAT32_IEEE", "double": "FLOAT64_IEEE",
    "unsigned char": "UBYTE", "signed char": "SBYTE", "char": "SBYTE",
    "unsigned int": "UWORD", "int": "SWORD",
    "unsigned long": "ULONG", "long": "SLONG",
}
# Physical range per A2L datatype (LOWER, UPPER) for MEASUREMENT / CHARACTERISTIC.
_A2L_RANGE = {
    "UBYTE": ("0", "255"), "SBYTE": ("-128", "127"),
    "UWORD": ("0", "65535"), "SWORD": ("-32768", "32767"),
    "ULONG": ("0", "4294967295"), "SLONG": ("-2147483648", "2147483647"),
    "FLOAT32_IEEE": ("-3.4E38", "3.4E38"),
    "FLOAT64_IEEE": ("-1.7E308", "1.7E308"),
}


def _dtype(ctype):
    return _A2L_TYPE.get((ctype or "").strip(), "UBYTE")


def _measurement(sig):
    dt = _dtype(sig.ctype)
    lo, hi = _A2L_RANGE[dt]
    desc = (sig.description or sig.name).replace('"', "'")
    return [
        f"    /begin MEASUREMENT {sig.name}",
        f'      "{desc}"',
        f"      {dt} NO_COMPU_METHOD 0 0 {lo} {hi}",
        "      ECU_ADDRESS 0x0",
        "    /end MEASUREMENT",
    ]


def _characteristic(cal):
    dt = _dtype(cal.ctype)
    lo, hi = _A2L_RANGE[dt]
    desc = (cal.description or cal.name).replace('"', "'")
    return [
        f"    /begin CHARACTERISTIC {cal.name}",
        f'      "{desc}"',
        f"      VALUE 0x0 __{dt}_1 0 NO_COMPU_METHOD {lo} {hi}",
        "    /end CHARACTERISTIC",
    ]


def emit_a2l(resolved, name, src):
    """Build the A2L text from resolved SWCs (`resolved` = models + ASW tasks).
    `name` is the system name (the PROJECT/MODULE identifier); `src` is the
    app.yaml filename (banner only). Each SWC contributes its bound port signals
    (MEASUREMENT) and its extern calibrations (CHARACTERISTIC)."""
    body = []
    for rm in resolved:
        for port in list(rm.inputs) + list(rm.outputs):
            body += _measurement(port.signal)
        for cal in getattr(rm.interface, "calibrations", ()) or ():
            if cal.kind != "define":
                body += _characteristic(cal)

    L = [
        f'/* {GENERATED_BANNER.format(src=src)} */',
        "ASAP2_VERSION 1 71",
        f'/begin PROJECT EROS_{name} ""',
        f'  /begin MODULE {name} "EROS SWC interface (static; patch ECU_ADDRESS '
        'from the .map)"',
        "    /begin MOD_COMMON \"\"",
        "      BYTE_ORDER MSB_LAST",
        "      ALIGNMENT_BYTE 1",
        "    /end MOD_COMMON",
    ]
    L += body
    L += ["  /end MODULE", "/end PROJECT", ""]
    return "\n".join(L)
