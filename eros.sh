#!/usr/bin/env bash
#
# eros.sh - toolchain helper for EROS (Embedded Realtime Operating System),
#           the OSEK BCC1 AVR kernel in this repository.
#
# Usage:
#   ./eros.sh [-check]     verify the AVR toolchain is installed (default)
#   ./eros.sh -install     install any missing toolchain components
#   ./eros.sh -build       build the reference demo into ./build (gitignored)
#   ./eros.sh -clean       remove ./build
#   ./eros.sh -help
#
# The build compiles out-of-tree: nothing is written outside ./build, and
# ./build is listed in .gitignore. The compiler flags below MUST stay in
# sync with the project Makefiles (the same mandated warning-free set).
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="$SCRIPT_DIR/build"

MCU=atmega328p
F_CPU=16000000UL

# Mandated flag set - keep identical to reference-demo/Makefile (the same
# warning-free set + the peripheral geometry from app.yaml).
CFLAGS=(-Wall -Wextra -Werror -std=c99 -Os -flto
        -ffunction-sections -fdata-sections -fno-common
        -mmcu="$MCU" -DF_CPU="$F_CPU"
        -DUART_BAUD=9600UL -DUART_TX_SIZE=128u -DUART_RX_SIZE=64u)
LDFLAGS=(-Wl,--gc-sections)

# Budgets (bytes), mirrored from the Makefile. flash/ram bound the
# app-agnostic KERNEL (non-LTO eros.o+config.o); image_* bound the whole
# shipped LTO image (avr-size text+data / data+bss).
FLASH_BUDGET=3072
RAM_BUDGET=128
IMAGE_FLASH_BUDGET=4096
IMAGE_RAM_BUDGET=384

# ----- pretty output (degrades gracefully when not a TTY) ---------------
if [[ -t 1 ]]; then
    C_R=$'\e[31m'; C_G=$'\e[32m'; C_Y=$'\e[33m'; C_B=$'\e[1m'; C_N=$'\e[0m'
else
    C_R=; C_G=; C_Y=; C_B=; C_N=
fi
say()   { printf '%s\n' "$*"; }
head1() { printf '\n%s%s%s\n' "$C_B" "$*" "$C_N"; }
ok()    { printf '  %s[ ok ]%s %s\n'  "$C_G" "$C_N" "$*"; }
warn()  { printf '  %s[warn]%s %s\n'  "$C_Y" "$C_N" "$*"; }
bad()   { printf '  %s[MISS]%s %s\n'  "$C_R" "$C_N" "$*"; }
die()   { printf '%serror:%s %s\n' "$C_R" "$C_N" "$*" >&2; exit 1; }

# ----- toolchain check --------------------------------------------------
# Components: name, apt package, and whether it is required for a build.
# avrdude is only needed to flash a board, so it is optional for a build.
MISSING=0        # required components missing
MISSING_OPT=0    # optional components missing

check_tool() {   # check_tool <command> <required:0|1> <hint>
    local tool=$1 required=$2 hint=$3 ver
    if command -v "$tool" >/dev/null 2>&1; then
        ver=$("$tool" --version 2>/dev/null | head -n1 || true)
        ok "$(printf '%-13s %s' "$tool" "$ver")"
    else
        if [[ "$required" -eq 1 ]]; then
            bad "$(printf '%-13s missing  (%s)' "$tool" "$hint")"
            MISSING=$((MISSING + 1))
        else
            warn "$(printf '%-13s missing  (%s)' "$tool" "$hint")"
            MISSING_OPT=$((MISSING_OPT + 1))
        fi
    fi
}

check_avrlibc() {
    # A working avr-gcc does not guarantee avr-libc headers + device
    # support; prove it by compiling a tiny program for the target MCU.
    if ! command -v avr-gcc >/dev/null 2>&1; then
        bad "avr-libc      cannot test (avr-gcc missing)"
        MISSING=$((MISSING + 1)); return
    fi
    local tmp; tmp=$(mktemp "${TMPDIR:-/tmp}/eros_XXXXXX.c")
    printf '#include <avr/io.h>\n#include <avr/interrupt.h>\nint main(void){return 0;}\n' > "$tmp"
    if avr-gcc -mmcu="$MCU" -DF_CPU="$F_CPU" -c -o /dev/null "$tmp" 2>/dev/null; then
        ok "avr-libc      headers + $MCU device support present"
    else
        bad "avr-libc      missing headers or $MCU support"
        MISSING=$((MISSING + 1))
    fi
    rm -f "$tmp"
}

do_check() {
    head1 "EROS toolchain check ($MCU @ ${F_CPU%UL})"
    check_tool make      1 "GNU make"
    check_tool avr-gcc   1 "gcc-avr"
    check_tool avr-objcopy 1 "binutils-avr"
    check_tool avr-size  1 "binutils-avr"
    check_tool avr-objdump 0 "binutils-avr, disassembly"
    check_tool avr-nm    0 "binutils-avr, symbol sizes"
    check_avrlibc
    check_tool avrdude   0 "avrdude, needed only to flash a board"

    echo
    if [[ "$MISSING" -eq 0 ]]; then
        say "${C_G}All required components present.${C_N}"
        [[ "$MISSING_OPT" -gt 0 ]] && say "Some optional tools are missing (see above)."
        say "Next: ${C_B}./eros.sh -build${C_N}"
        return 0
    else
        say "${C_R}$MISSING required component(s) missing.${C_N}"
        say "Install them with: ${C_B}./eros.sh -install${C_N}"
        return 1
    fi
}

# ----- install ----------------------------------------------------------
detect_pm() {
    local pm
    for pm in apt-get dnf yum pacman zypper apk brew; do
        command -v "$pm" >/dev/null 2>&1 && { echo "$pm"; return; }
    done
    echo ""
}

do_install() {
    head1 "EROS toolchain install"
    local pm; pm=$(detect_pm)
    [[ -z "$pm" ]] && die "no supported package manager found (apt/dnf/yum/pacman/zypper/apk/brew). Install gcc-avr, avr-libc, binutils-avr, avrdude and make manually."

    local sudo_cmd=""
    if [[ "$(id -u)" -ne 0 ]] && [[ "$pm" != "brew" ]]; then
        if command -v sudo >/dev/null 2>&1; then sudo_cmd="sudo"
        else die "not root and sudo not available; re-run as root."; fi
    fi

    say "Package manager: $pm"
    case "$pm" in
        apt-get)
            $sudo_cmd apt-get update
            $sudo_cmd apt-get install -y gcc-avr avr-libc binutils-avr avrdude make
            ;;
        dnf|yum)
            $sudo_cmd "$pm" install -y avr-gcc avr-libc avr-binutils avrdude make
            ;;
        pacman)
            $sudo_cmd pacman -Sy --needed --noconfirm avr-gcc avr-libc avr-binutils avrdude make
            ;;
        zypper)
            # openSUSE ships versioned cross compilers; try the common names.
            $sudo_cmd zypper --non-interactive install cross-avr-gcc cross-avr-binutils avr-libc avrdude make \
                || die "openSUSE package names vary by release; install a cross-avr-gcc*, cross-avr-binutils, avr-libc, avrdude and make manually."
            ;;
        apk)
            $sudo_cmd apk add gcc-avr avr-libc binutils-avr avrdude make
            ;;
        brew)
            brew tap osx-cross/avr
            brew install avr-gcc avrdude   # make ships with the Xcode CLT
            ;;
        *)
            die "internal: unhandled package manager '$pm'"
            ;;
    esac

    echo
    say "Install step finished; re-checking..."
    MISSING=0; MISSING_OPT=0
    do_check
}

# ----- build ------------------------------------------------------------
# compile <src> <objdir> <inc1> [inc2...]  -> echoes the object path
compile() {
    local src=$1 objdir=$2; shift 2
    local inc=() i
    for i in "$@"; do inc+=("-I$i"); done
    local obj="$objdir/$(basename "${src%.c}").o"
    avr-gcc "${CFLAGS[@]}" "${inc[@]}" -MMD -MP -c -o "$obj" "$src"
    printf '%s' "$obj"
}

link_hex() {     # link_hex <name> <outdir> <obj...>
    local name=$1 outdir=$2; shift 2
    avr-gcc "${CFLAGS[@]}" "${LDFLAGS[@]}" -Wl,-Map="$outdir/$name.map" \
        -o "$outdir/$name.elf" "$@"
    avr-objcopy -O ihex -R .eeprom "$outdir/$name.elf" "$outdir/$name.hex"
    say "  -> $(realpath --relative-to="$SCRIPT_DIR" "$outdir/$name.elf") / .hex / .map"
    avr-size -B "$outdir/$name.elf" | sed 's/^/     /'
}

# Budget check, mirroring the Makefile: a non-LTO KERNEL check (eros.o +
# config.o) plus a whole-image gate on the shipped LTO eros.elf.
budget_check() {
    local bdir="$BUILD_DIR/eros/budget"
    mkdir -p "$bdir"
    local nolto=()
    local f
    for f in "${CFLAGS[@]}"; do [[ "$f" == "-flto" ]] || nolto+=("$f"); done
    avr-gcc "${nolto[@]}" -I"$SCRIPT_DIR/reference-demo" -I"$SCRIPT_DIR/kernel" -c \
        -o "$bdir/eros.o"   "$SCRIPT_DIR/kernel/eros.c"
    avr-gcc "${nolto[@]}" -I"$SCRIPT_DIR/reference-demo" -I"$SCRIPT_DIR/kernel" -c \
        -o "$bdir/config.o" "$SCRIPT_DIR/reference-demo/config.c"
    avr-size -B "$bdir/eros.o" "$bdir/config.o" | awk -v fb="$FLASH_BUDGET" -v rb="$RAM_BUDGET" '
        NR==2 { kflash += $1 + $2; kram = $2 + $3 }
        NR==3 { kflash += $1 + $2; arena = $2 + $3 }
        END {
            printf("     kernel Flash %d / %d B, static RAM %d / %d B (arena %d B)\n",
                   kflash, fb, kram, rb, arena)
            if (kflash > fb || kram > rb) { print "     BUDGET EXCEEDED"; exit 1 }
            else                          { print "     budgets OK" }
        }'
    # Whole shipped LTO image (the eros.elf link_hex already produced).
    avr-size -B "$BUILD_DIR/eros/eros.elf" \
        | awk -v fb="$IMAGE_FLASH_BUDGET" -v rb="$IMAGE_RAM_BUDGET" '
        NR==2 {
            flash = $1 + $2; ram = $2 + $3
            printf("     whole image  %d / %d B Flash, %d / %d B RAM\n", flash, fb, ram, rb)
            if (flash > fb || ram > rb) { print "     IMAGE BUDGET EXCEEDED"; exit 1 }
            else                        { print "     image budgets OK" }
        }'
}

do_build() {
    command -v avr-gcc     >/dev/null 2>&1 || die "avr-gcc not found; run ./eros.sh -install"
    command -v avr-objcopy >/dev/null 2>&1 || die "avr-objcopy not found; run ./eros.sh -install"
    command -v avr-size    >/dev/null 2>&1 || die "avr-size not found; run ./eros.sh -install"

    head1 "Building EROS into ./build"

    # --- reference demo -----------------------------------------------
    # Source list mirrors reference-demo/Makefile APP_SRCS (uart.c/pwm.c
    # are the peripheral drivers selected in app.yaml).
    local rd="$SCRIPT_DIR/reference-demo"
    local od="$BUILD_DIR/eros"; mkdir -p "$od"
    say "reference demo (eros):"
    local objs=()
    local rs
    for rs in main.c actuator.c asw_signals.c asw_10ms.c asw_50ms.c \
              asw_100ms.c asw_500ms.c uart.c pwm.c config.c; do
        objs+=("$(compile "$rd/$rs" "$od" "$rd" "$SCRIPT_DIR/kernel")")
    done
    objs+=("$(compile "$SCRIPT_DIR/kernel/eros.c" "$od" "$rd" "$SCRIPT_DIR/kernel")")
    link_hex eros "$od" "${objs[@]}"
    budget_check

    echo
    say "${C_G}Build complete.${C_N} Artifacts under ./build (gitignored)."
    say "Flash with: ${C_B}./eros.sh -flash${C_N}"
}

do_clean() {
    rm -rf "$BUILD_DIR"
    say "removed ./build"
}

# ----- flash ------------------------------------------------------------
# Candidate serial ports (Linux ttyUSB/ttyACM, macOS cu.usb*). Only paths
# that actually exist are printed - literal unmatched globs are skipped.
detect_ports() {
    local p
    for p in /dev/ttyUSB* /dev/ttyACM* \
             /dev/cu.usbserial* /dev/cu.wchusbserial* /dev/cu.usbmodem* \
             /dev/tty.usbserial* /dev/tty.wchusbserial*; do
        [[ -e "$p" ]] && printf '%s\n' "$p"
    done
}

# Probe: does an ATmega328P answer on this port+baud via the arduino
# (bootloader) programmer? No -U operation -> avrdude reads the signature
# and exits; 0 means the device matched -p m328p.
probe_target() {   # probe_target <port> <baud>
    local port="$1"
    local baud="$2"
    avrdude -p m328p -c arduino -P "$port" -b "$baud" -qq >/dev/null 2>&1
}

do_flash() {
    command -v avrdude >/dev/null 2>&1 \
        || die "avrdude not found; run ./eros.sh -install (or install avrdude) to flash."

    # --- select firmware ---------------------------------------------
    local target=${1:-eros} hex is_path=0
    case "$target" in
        eros|reference|ref)          hex="$BUILD_DIR/eros/eros.hex" ;;
        *.hex)                       hex="$target"; is_path=1 ;;
        *) die "unknown flash target '$target' (use: eros | <file.hex>)" ;;
    esac
    if [[ "$is_path" -eq 0 ]] && [[ ! -f "$hex" ]]; then
        say "firmware not built yet ($hex); building..."
        do_build
        echo
    fi
    [[ -f "$hex" ]] || die "firmware not found: $hex"

    head1 "Flashing $(basename "$hex")"

    # --- identify the target (port + bootloader baud) ----------------
    local pport=${EROS_PORT:-} pbaud=${EROS_BAUD:-}
    local ports=() bauds=()
    if [[ -n "$pport" ]]; then
        ports=("$pport")
    else
        local line
        while IFS= read -r line; do ports+=("$line"); done < <(detect_ports)
        [[ "${#ports[@]}" -eq 0 ]] && die "no serial port found (looked for /dev/ttyUSB*, /dev/ttyACM*, /dev/cu.usb*). Plug in the board, or set EROS_PORT=/dev/..."
    fi
    if [[ -n "$pbaud" ]]; then bauds=("$pbaud"); else bauds=(57600 115200); fi

    say "candidate ports: ${ports[*]}"
    local port baud found_port="" found_baud=""
    for port in "${ports[@]}"; do
        for baud in "${bauds[@]}"; do
            printf '  probing %-16s @ %-6s ... ' "$port" "$baud"
            if probe_target "$port" "$baud"; then
                printf '%sok%s\n' "$C_G" "$C_N"
                found_port=$port; found_baud=$baud; break 2
            fi
            printf 'no response\n'
        done
    done
    [[ -z "$found_port" ]] && die "no ATmega328P responded (ports: ${ports[*]}, bauds: ${bauds[*]}). Check the cable/board, or force with EROS_PORT= and EROS_BAUD=."

    say "target: ${C_B}ATmega328P on $found_port @ $found_baud baud${C_N}"
    echo
    avrdude -p m328p -c arduino -P "$found_port" -b "$found_baud" \
            -U flash:w:"$hex":i
    echo
    say "${C_G}Flashed${C_N} $hex -> $found_port"
}

usage() {
    cat <<'EOF'
eros.sh - toolchain helper for EROS (Embedded Realtime Operating System),
          the OSEK BCC1 AVR kernel in this repository.

Usage:
  ./eros.sh [-check]         verify the AVR toolchain is installed (default)
  ./eros.sh -install         install any missing toolchain components
  ./eros.sh -build           build the reference demo into ./build (gitignored)
  ./eros.sh -flash [target]  auto-detect the board and flash it
                             target: eros (default) | <file.hex>
  ./eros.sh -clean           remove ./build
  ./eros.sh -help

The build compiles out-of-tree into ./build (which is gitignored); the
reference demo also gets the same kernel + whole-image budget checks the
Makefile runs.

-flash auto-detects the serial port (/dev/ttyUSB*, /dev/ttyACM*,
/dev/cu.usb* on macOS) and the bootloader baud (57600 old-bootloader
Nano, then 115200 Optiboot) by probing the ATmega328P signature.
Override with EROS_PORT and/or EROS_BAUD.
EOF
}

# ----- dispatch ---------------------------------------------------------
case "${1:--check}" in
    -check|--check|check|"") do_check ;;
    -install|--install|install) do_install ;;
    -build|--build|build) do_build ;;
    -flash|--flash|flash) shift || true; do_flash "${1:-eros}" ;;
    -clean|--clean|clean) do_clean ;;
    -h|-help|--help|help) usage ;;
    *) printf 'unknown option: %s\n\n' "$1" >&2; usage; exit 2 ;;
esac
