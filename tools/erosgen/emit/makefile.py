"""Emitter for the application Makefile (per-.o compile, LTO link, budgets).

driver_sources() resolves selected peripherals to source files; emit_makefile()
assembles SRCS/CFLAGS, the optional budget/image gates, and the ``config:``
target that reruns the generator via the stable ENTRYPOINT shim.
"""
import os

from ..constants import (GENERATED_BANNER, UART_RX_RING_DEFAULT,
                         UART_TX_RING_DEFAULT)
from ..errors import fail
from ..paths import ENTRYPOINT


def _layer_dir(fname):
    """The BSW layer subdir of a driver source, '' if flat. 'mcal/adc.c' ->
    'mcal'; 'uart.c' -> ''."""
    return fname.rsplit("/", 1)[0] if "/" in fname else ""


def _basename(fname):
    """A driver source's compiled basename: 'mcal/adc.c' -> 'adc.c'. The layer
    subdir lives on VPATH/-I, so objects stay flat."""
    return fname.rsplit("/", 1)[1] if "/" in fname else fname


def driver_sources(s, app_dir):
    """Resolve peripheral names to source files: app dir wins, then
    drivers_dir (added to VPATH)."""
    local, external = [], []
    known = s.profile.known_peripherals
    for p in sorted(s.peripherals):
        fname = known[p]
        if (app_dir / fname).exists():
            local.append(fname)
        elif s.drivers_dir and (app_dir / s.drivers_dir / fname).exists():
            external.append(fname)
        else:
            fail(f"peripheral '{p}': source {fname} not found in app dir"
                 + (f" or {s.drivers_dir}" if s.drivers_dir else
                    " (set system.drivers_dir)"))
    return local, external


def periph_defines(s):
    defs = []
    uart = s.peripherals.get("uart")
    if uart is not None:
        uart = uart or {}
        defs.append(f"-DUART_BAUD={int(uart.get('baud', 9600))}UL")
        defs.append(f"-DUART_TX_SIZE={int(uart.get('tx_ring', UART_TX_RING_DEFAULT))}u")
        defs.append(f"-DUART_RX_SIZE={int(uart.get('rx_ring', UART_RX_RING_DEFAULT))}u")
    # PWM frequency: erosgen picks the Timer1 prescaler + TOP and passes them as
    # -D overrides; pwm.c keeps its 1 kHz defaults when freq_hz is unset, so a
    # project that just activates pwm (or the reference demo) is byte-identical.
    pwm = s.peripherals.get("pwm")
    if isinstance(pwm, dict) and pwm.get("freq_hz") is not None:
        from ..pwmcfg import pwm_config
        cfg = pwm_config(s.profile, int(pwm["freq_hz"]))
        if cfg is not None:
            cs, top, _ = cfg
            defs.append(f"-DPWM_TOP={top}u")
            defs.append(f"-DPWM_CS={cs}u")
    # ADC reference + prescaler, I2C bus speed, Timer0 PWM prescaler - each keeps
    # its driver default when unset (so an activated-but-unconfigured peripheral
    # is byte-identical).
    adc = s.peripherals.get("adc")
    if isinstance(adc, dict):
        from ..pwmcfg import adc_prescaler_bits, adc_ref_symbol
        if adc.get("reference") is not None:
            sym = adc_ref_symbol(adc["reference"])
            if sym:
                defs.append(f"-DADC_REF={sym}")
        if adc.get("prescaler") is not None:
            bits = adc_prescaler_bits(int(adc["prescaler"]))
            if bits is not None:
                defs.append(f"-DADC_PRESCALER={bits}u")
    i2c = s.peripherals.get("i2c")
    if isinstance(i2c, dict) and i2c.get("speed_hz") is not None:
        from ..pwmcfg import f_cpu_hz, i2c_twbr
        twbr = i2c_twbr(int(i2c["speed_hz"]), f_cpu_hz(s.profile))
        if twbr is not None:
            defs.append(f"-DI2C_TWBR={twbr}u")
    t0 = s.peripherals.get("timer0_pwm")
    if isinstance(t0, dict) and t0.get("freq_hz") is not None:
        from ..pwmcfg import f_cpu_hz, pwm_timer, timer0_pwm_cs
        timer = pwm_timer(s.profile, "timer0_pwm")
        cfg = (timer0_pwm_cs(int(t0["freq_hz"]), f_cpu_hz(s.profile), timer[1])
               if timer else None)
        if cfg is not None:
            defs.append(f"-DT0PWM_CS={cfg[0]}u")
    return defs


def _drivers_dir_or_fail(s, ext_drv):
    """system.drivers_dir, or a clear ConfigError if port-bound driver sources
    are needed but it isn't set (rather than emitting a Makefile with a None in
    VPATH - the crash a GUI project with unset drivers_dir used to hit)."""
    if not s.drivers_dir:
        fail("driver sources (" + ", ".join(sorted(set(ext_drv))) + ") are "
             "needed but system.drivers_dir is not set")
    return s.drivers_dir


def model_driver_srcs(m, profile):
    """Source files for the drivers a model's ports bind to (adc.c, ...);
    dio binds to raw registers and needs no source."""
    out = []
    ports = m.get("ports", {}) or {}
    for direction in ("in", "out"):
        for pd in ports.get(direction, []) or []:
            fname = profile.known_peripherals.get(pd.get("driver"))
            if fname and fname not in out:
                out.append(fname)
    return out


def emit_makefile(s, app_dir):
    local_drv, ext_drv = driver_sources(s, app_dir)
    # Auto-include the generated per-rate ASW files so a fresh project
    # builds without hand-editing sources; dedup against any the user
    # listed explicitly (the reference demo lists them for readability).
    asw_files = [f"asw_{t.period_ms}ms.c" for t in s.periodic
                 if t.name not in s.rte_task_names]
    app_srcs = list(s.sources)
    for f in asw_files + local_drv:
        if f not in app_srcs:
            app_srcs.append(f)
    # Hand-authored ASW tasks contribute their three sources + Rte.c + the
    # sources of the drivers their ports bind to (same resolution as a model).
    for t in s.asw_tasks:
        for suffix in (".c", "_Intfc.c", "_Param.c"):
            f = f"{t['name']}{suffix}"
            if f not in app_srcs:
                app_srcs.append(f)
        for fname in model_driver_srcs(t, s.profile):
            if fname not in ext_drv:
                ext_drv.append(fname)
    if s.asw_tasks and "Rte.c" not in app_srcs:
        app_srcs.append("Rte.c")
    if getattr(s, "modes", None) and "Rte_Modes.c" not in app_srcs:
        app_srcs.append("Rte_Modes.c")
    vpath = [s.kernel_dir]
    incs = ["-I.", f"-I{s.kernel_dir}"]
    # External driver VPATH/-I are added once, after every ext_drv is collected
    # (below), because a driver source may live in a layer subdir (mcal/...).

    model_block = []
    if s.simulink:
        mdir = s.simulink.get("dir", "../codegen")
        model = s.simulink["model"]
        model_block = [
            f"MODEL_DIR  := {mdir}/{model}_ert_rtw",
            "MODEL_SRCS := $(filter-out ert_main.c,"
            "$(notdir $(wildcard $(MODEL_DIR)/*.c)))",
        ]
        vpath.append(f"{mdir} $(MODEL_DIR)")
        incs.append(f"-I{mdir} -I$(MODEL_DIR)")

    # RTE-generated model (models: section). The RTE (Rte.c) is generated into
    # the app dir; the model ERT sources and any port-bound drivers are added.
    if s.models:
        if len(s.models) == 1:
            m = s.models[0]
            model_block = [
                f"MODEL_DIR  := {m['codegen_dir']}",
                "MODEL_SRCS := $(filter-out ert_main.c,"
                "$(notdir $(wildcard $(MODEL_DIR)/*.c)))",
            ]
            vpath.append("$(MODEL_DIR)")
            incs.append("-I$(MODEL_DIR)")
        else:
            # One MODEL_DIR<n> per SWC; $(sort) dedups shared rtw runtime
            # sources (rt_nonfinite.c, ...) that several models emit.
            model_block, wilds = [], []
            for i, m in enumerate(s.models, 1):
                model_block.append(f"MODEL_DIR{i}  := {m['codegen_dir']}")
                vpath.append(f"$(MODEL_DIR{i})")
                incs.append(f"-I$(MODEL_DIR{i})")
                wilds.append(f"$(wildcard $(MODEL_DIR{i})/*.c)")
            model_block.append(
                "MODEL_SRCS := $(filter-out ert_main.c,"
                f"$(sort $(notdir {' '.join(wilds)})))")
        if "Rte.c" not in app_srcs:
            app_srcs.append("Rte.c")
        for m in s.models:
            for fname in model_driver_srcs(m, s.profile):
                if fname not in ext_drv:
                    ext_drv.append(fname)

    # Resolve the external-driver dirs once. A source may sit in a layer subdir
    # (drivers/mcal/adc.c -> fname 'mcal/adc.c'); add <drivers_dir>/<subdir> to
    # VPATH + -I so the basename in SRCS resolves and its header is found.
    if ext_drv:
        dd = _drivers_dir_or_fail(s, ext_drv)
        for sub in sorted({_layer_dir(f) for f in ext_drv}):
            d = f"{dd}/{sub}" if sub else dd
            if d not in vpath:
                vpath.append(d)
            if f"-I{d}" not in incs:
                incs.append(f"-I{d}")

    defs = periph_defines(s)

    # Whole-image budget gate (real LTO elf, in the size target). The
    # kernel budget below stays a separate, non-LTO check: UART/PWM rings
    # are application RAM and never touch eros.o/config.o.
    image_flash = s.budget.get("image_flash") if s.budget else None
    image_ram = s.budget.get("image_ram") if s.budget else None
    has_image_gate = image_flash is not None and image_ram is not None

    L = []
    L.append("# =====================================================================")
    L.append(f"# {GENERATED_BANNER.format(src=s.src.name)}")
    L.append("#")
    L.append(f"# Application '{s.name}' on the EROS kernel. Only the peripherals")
    L.append("# selected in the YAML are compiled; everything else costs 0 bytes.")
    L.append("# =====================================================================")
    L.append("")
    L.append(f"MCU     := {s.profile.mcu_gcc}")
    L.append(f"F_CPU   := {s.profile.f_cpu}")
    L.append(f"TARGET  := {s.name}")
    L.append("")
    L.extend(model_block)
    L.append(f"VPATH   := {' '.join(vpath)}")
    L.append("")
    L.append(f"APP_SRCS := {' '.join(app_srcs)}")
    srcs = "$(APP_SRCS) " + " ".join([_basename(f) for f in ext_drv]
                                     + ["eros.c", "config.c"])
    if s.simulink or s.models:
        srcs += " $(MODEL_SRCS)"
    L.append(f"SRCS     := {srcs}")
    L.append("OBJS     := $(SRCS:.c=.o)")
    L.append("DEPS     := $(SRCS:.c=.d)")
    L.append("")
    L.append("CC      := avr-gcc")
    L.append("OBJCOPY := avr-objcopy")
    L.append("SIZE    := avr-size")
    L.append("AVRDUDE := avrdude")
    L.append("")
    L.append("PORT    ?= /dev/ttyUSB0")
    L.append(f"BAUD    ?= {s.profile.avrdude_baud}          "
             f"# {s.profile.avrdude_baud_note}")
    L.append("")
    if defs:
        L.append("# Peripheral geometry from app.yaml (overrides driver defaults)")
        L.append(f"PERIPH_DEFS := {' '.join(defs)}")
        defs_ref = " $(PERIPH_DEFS)"
    else:
        defs_ref = ""
    L.append("CFLAGS  := -Wall -Wextra -Werror -std=c99 -Os -flto \\")
    L.append("           -ffunction-sections -fdata-sections -fno-common \\")
    L.append(f"           -mmcu=$(MCU) -DF_CPU=$(F_CPU){defs_ref} \\")
    L.append(f"           {' '.join(incs)}")
    L.append("LDFLAGS := -Wl,--gc-sections -Wl,-Map=$(TARGET).map")
    L.append("")
    if s.budget:
        L.append("BUDGET_DIR    := build_budget")
        L.append("CFLAGS_NOLTO  := $(filter-out -flto,$(CFLAGS))")
        L.append(f"FLASH_BUDGET  := {int(s.budget.get('flash', 3072))}")
        L.append(f"RAM_BUDGET    := {int(s.budget.get('ram', 128))}")
        L.append(f"SRAM_TOTAL    := {int(s.budget.get('sram_total', 2048))}")
        if has_image_gate:
            L.append(f"IMAGE_FLASH_BUDGET := {int(image_flash)}")
            L.append(f"IMAGE_RAM_BUDGET   := {int(image_ram)}")
        L.append("")
        L.append(".PHONY: all size budget flash clean config")
        L.append("")
        L.append("all: $(TARGET).hex size budget")
    else:
        L.append(".PHONY: all size flash clean config")
        L.append("")
        L.append("all: $(TARGET).hex size")
    L.append("")
    L.append("$(TARGET).elf: $(OBJS)")
    L.append("\t$(CC) $(CFLAGS) $(LDFLAGS) -o $@ $^")
    L.append("")
    L.append("$(TARGET).hex: $(TARGET).elf")
    L.append("\t$(OBJCOPY) -O ihex -R .eeprom $< $@")
    L.append("")
    L.append("%.o: %.c")
    L.append("\t$(CC) $(CFLAGS) -MMD -MP -c -o $@ $<")
    L.append("")
    L.append("size: $(TARGET).elf")
    L.append('\t@echo "---- final image (LTO) --------------------------------------"')
    L.append("\t@$(SIZE) -B $(TARGET).elf")
    if has_image_gate:
        L.append("\t@$(SIZE) -B $(TARGET).elf | awk ' \\")
        L.append("\t  NR==2 { flash = $$1 + $$2; ram = $$2 + $$3; \\")
        L.append('\t    printf("whole image : %d / %d B Flash, %d / %d B RAM\\n", \\')
        L.append("\t           flash, $(IMAGE_FLASH_BUDGET), ram, $(IMAGE_RAM_BUDGET)); \\")
        L.append("\t    if (flash > $(IMAGE_FLASH_BUDGET) || ram > $(IMAGE_RAM_BUDGET)) { \\")
        L.append('\t      printf("IMAGE BUDGET EXCEEDED\\n"); exit 1; \\')
        L.append("\t    } else { \\")
        L.append('\t      printf("image budgets OK\\n"); \\')
        L.append("\t    } }'")
    L.append("")
    if s.budget:
        L.append("$(BUDGET_DIR):")
        L.append("\tmkdir -p $(BUDGET_DIR)")
        L.append("")
        L.append("$(BUDGET_DIR)/%.o: %.c | $(BUDGET_DIR)")
        L.append("\t$(CC) $(CFLAGS_NOLTO) -MMD -MP -c -o $@ $<")
        L.append("")
        L.append("APP_BUDGET_OBJS := $(addprefix $(BUDGET_DIR)/,$(APP_SRCS:.c=.o))")
        L.append("")
        L.append("budget: $(BUDGET_DIR)/eros.o $(BUDGET_DIR)/config.o $(APP_BUDGET_OBJS)")
        L.append('\t@echo "---- kernel budget check (non-LTO reference build) ----------"')
        L.append("\t@$(SIZE) -B $(BUDGET_DIR)/eros.o $(BUDGET_DIR)/config.o \\")
        L.append("\t         $(APP_BUDGET_OBJS) | awk ' \\")
        L.append("\t  NR==2 { kflash += $$1 + $$2; kram   = $$2 + $$3 } \\")
        L.append("\t  NR==3 { kflash += $$1 + $$2; arena  = $$2 + $$3 } \\")
        L.append("\t  NR>=4 { appram += $$2 + $$3 } \\")
        L.append("\t  END { \\")
        L.append('\t    printf("kernel Flash (eros.o+config.o) : %4d / %d bytes\\n", \\')
        L.append("\t           kflash, $(FLASH_BUDGET)); \\")
        L.append('\t    printf("kernel static RAM (eros.o)     : %4d / %d bytes\\n", \\')
        L.append("\t           kram, $(RAM_BUDGET)); \\")
        L.append('\t    printf("pool arena (config.o, excluded)   : %4d bytes\\n", arena); \\')
        L.append('\t    printf("application RAM (app objects)     : %4d bytes\\n", appram); \\')
        L.append('\t    printf("stack + idle RAM (of %d total)  : %4d bytes\\n", \\')
        L.append("\t           $(SRAM_TOTAL), $(SRAM_TOTAL) - kram - arena - appram); \\")
        L.append("\t    if (kflash > $(FLASH_BUDGET) || kram > $(RAM_BUDGET)) { \\")
        L.append('\t      printf("BUDGET EXCEEDED\\n"); exit 1; \\')
        L.append("\t    } else { \\")
        L.append('\t      printf("budgets OK\\n"); \\')
        L.append("\t    } \\")
        L.append("\t  }'")
        L.append("")
    L.append("flash: $(TARGET).hex")
    L.append(f"\t$(AVRDUDE) -v -p {s.profile.avrdude_part} "
             f"-c {s.profile.avrdude_programmer} -P $(PORT) -b $(BAUD) \\")
    L.append("\t           -U flash:w:$(TARGET).hex:i")
    L.append("")
    erosgen_rel = os.path.relpath(ENTRYPOINT, str(app_dir))
    L.append("# Regenerate config.h/config.c/Makefile/os_gen.h from the YAML.")
    L.append("# Run this after editing app.yaml (config is NOT auto-rebuilt,")
    L.append("# so a Python-less CI can still 'make' from committed output).")
    L.append("config:")
    L.append(f"\tpython3 {erosgen_rel} {s.src.name}")
    L.append("")
    L.append("clean:")
    if s.budget:
        L.append("\trm -rf $(OBJS) $(DEPS) $(BUDGET_DIR) \\")
        L.append("\t       $(TARGET).elf $(TARGET).hex $(TARGET).map")
    else:
        L.append("\trm -f $(OBJS) $(DEPS) $(TARGET).elf $(TARGET).hex $(TARGET).map")
    L.append("")
    L.append("-include $(DEPS)")
    if s.budget:
        L.append("-include $(addprefix $(BUDGET_DIR)/,$(DEPS))")
    return "\n".join(L) + "\n"
