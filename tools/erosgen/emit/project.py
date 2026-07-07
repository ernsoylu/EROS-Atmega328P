"""Phase 12 - toolchain / editor project files (opt-in, CLI ``--project``).

Optional companions to the Makefile that let an IDE see exactly what avr-gcc
compiles:

  * ``compile_commands.json`` - the clangd / IntelliSense compilation database,
  * ``CMakeLists.txt``        - a CMake project for CMake-based IDEs / builds,
  * ``.vscode/tasks.json``    - build / flash / clean / size tasks,
  * ``.vscode/c_cpp_properties.json`` - points the C/C++ extension at the db.

Everything derives from :func:`build_plan`, which recomputes the *same* source /
include / define facts that :func:`emit.makefile.emit_makefile` builds with (a
guard test keeps the two in agreement) - but resolves the model codegen dirs to
real paths so a tool can consume them. Opt-in, so no golden fixture carries them.
"""
import json
from pathlib import Path

from ..constants import GENERATED_BANNER
from .makefile import (_drivers_dir_or_fail, _layer_dir, driver_sources,
                       idle_busy_def, model_driver_srcs, periph_defines,
                       tick_timer_def, uart_instance_def)

# The flags every translation unit is compiled with - kept in step with the
# CFLAGS emit_makefile writes (the guard test compares the two).
_WARN = ["-Wall", "-Wextra", "-Werror"]
_STD = ["-std=c99", "-Os", "-flto", "-ffunction-sections", "-fdata-sections",
        "-fno-common"]
_RTE_C = "Rte.c"          # the RTE both models and hand-ASW tasks emit into app_dir


def _add(lst, item):
    if item not in lst:
        lst.append(item)


def _asw_task_srcs(s, app_srcs, ext_drv):
    """Hand-authored ASW tasks contribute 3 sources + Rte.c + bound drivers."""
    for t in s.asw_tasks:
        for suffix in (".c", "_Intfc.c", "_Param.c"):
            _add(app_srcs, f"{t['name']}{suffix}")
        for fname in model_driver_srcs(t, s.profile):
            _add(ext_drv, fname)
    if s.asw_tasks:
        _add(app_srcs, _RTE_C)


def _model_dirs(s, app_srcs, ext_drv):
    """models:/simulink: contribute Rte.c + bound drivers; return codegen dirs."""
    dirs = []
    if s.simulink:
        mdir = s.simulink.get("dir", "../codegen")
        dirs.append(f"{mdir}/{s.simulink['model']}_ert_rtw")
    if s.models:
        _add(app_srcs, _RTE_C)
        for m in s.models:
            dirs.append(m["codegen_dir"])
            for fname in model_driver_srcs(m, s.profile):
                _add(ext_drv, fname)
    return dirs


def _include_dirs(s, ext_drv, model_dirs):
    incs = ["-I.", f"-I{s.kernel_dir}"] + [f"-I{d}" for d in model_dirs]
    if ext_drv:
        dd = _drivers_dir_or_fail(s, ext_drv)
        for sub in sorted({_layer_dir(f) for f in ext_drv}):
            _add(incs, f"-I{dd}/{sub}" if sub else f"-I{dd}")
    return incs


def _concrete_srcs(s, app_dir, app_srcs, ext_drv, model_dirs):
    """The source files (relative to app_dir, or absolute if a dir was given so)
    the Makefile's VPATH resolves - with the model codegen dirs globbed."""
    srcs = list(app_srcs)                                  # generated in app_dir
    if ext_drv:
        dd = _drivers_dir_or_fail(s, ext_drv)
        srcs += [f"{dd}/{f}" for f in ext_drv]
    srcs += [f"{s.kernel_dir}/eros.c", "config.c"]
    for d in model_dirs:
        md = Path(d) if Path(d).is_absolute() else Path(app_dir) / d
        for c in sorted(md.glob("*.c")):
            if c.name != "ert_main.c":
                srcs.append(str(c) if Path(d).is_absolute() else f"{d}/{c.name}")
    return srcs


def build_plan(s, app_dir):
    """Return the concrete build facts as real paths: ``{srcs, incs, defs,
    cflags, app_srcs, ext_drv, model_dirs}``. Mirrors emit_makefile's assembly;
    the Makefile expresses the model dirs as make variables, here they are
    resolved so an IDE can index them (a guard test keeps the two in step)."""
    local_drv, ext_drv = driver_sources(s, app_dir)
    app_srcs = list(s.sources)
    for f in [f"asw_{t.period_ms}ms.c" for t in s.periodic
              if t.name not in s.rte_task_names] + local_drv:
        _add(app_srcs, f)
    _asw_task_srcs(s, app_srcs, ext_drv)
    if getattr(s, "modes", None):
        _add(app_srcs, "Rte_Modes.c")
    model_dirs = _model_dirs(s, app_srcs, ext_drv)

    incs = _include_dirs(s, ext_drv, model_dirs)
    srcs = _concrete_srcs(s, app_dir, app_srcs, ext_drv, model_dirs)
    defs = periph_defines(s)
    tick = tick_timer_def(s.profile).split()          # "" or ["-DEROS_TICK_TIMER=3"]
    uart = uart_instance_def(s.profile).split()       # "" or ["-DUART_USART=1"]
    idle = idle_busy_def(s).split()                   # "" or ["-DEROS_IDLE_BUSY"]
    cflags = (_WARN + _STD + [f"-mmcu={s.profile.mcu_gcc}",
              f"-DF_CPU={s.profile.f_cpu}"] + tick + uart + idle + defs + incs)
    return {"srcs": srcs, "incs": incs, "defs": defs, "cflags": cflags,
            "app_srcs": app_srcs, "ext_drv": ext_drv, "model_dirs": model_dirs}


def emit_compile_commands(s, app_dir):
    """The clangd / IntelliSense compilation database: one entry per TU with the
    exact avr-gcc command the Makefile uses."""
    plan = build_plan(s, app_dir)
    directory = str(Path(app_dir).resolve())
    cmd = "avr-gcc " + " ".join(plan["cflags"])
    db = [{"directory": directory, "file": f, "command": f"{cmd} -c {f}"}
          for f in plan["srcs"]]
    return json.dumps(db, indent=2) + "\n"


def emit_cmakelists(s, app_dir):
    """A CMake project mirroring the Makefile. Invoke with the avr-gcc toolchain
    already set below: ``cmake -B build && cmake --build build``."""
    plan = build_plan(s, app_dir)
    incdirs = [i[2:] for i in plan["incs"]]               # strip the -I
    opts = " ".join(_WARN + _STD + ["-mmcu=${MCU}", "-DF_CPU=${F_CPU}"]
                    + plan["defs"])
    L = [f"# {GENERATED_BANNER.format(src=s.src.name)}",
         "# CMake companion to the Makefile - same sources, flags and includes.",
         "cmake_minimum_required(VERSION 3.13)",
         "",
         "# avr-gcc is a cross toolchain: skip CMake's link-based compiler probe",
         "# and declare a bare-metal target before project() runs.",
         "set(CMAKE_SYSTEM_NAME Generic)",
         "set(CMAKE_TRY_COMPILE_TARGET_TYPE STATIC_LIBRARY)",
         "set(CMAKE_C_COMPILER avr-gcc)",
         "",
         f"project({s.name} C)",
         "",
         f"set(MCU {s.profile.mcu_gcc})",
         f"set(F_CPU {s.profile.f_cpu})",
         "",
         f"add_compile_options({opts})",
         f"include_directories({' '.join(incdirs)})",
         "",
         f"add_executable({s.name}.elf"]
    L += [f"    {src}" for src in plan["srcs"]]
    L += [")",
          "",
          f"target_link_options({s.name}.elf PRIVATE "
          "-mmcu=${MCU} -Wl,--gc-sections)",
          ""]
    return "\n".join(L)


def emit_vscode_tasks():
    """VS Code build/flash/clean/size tasks that shell out to the Makefile."""
    def task(label, cmd, default=False):
        t = {"label": label, "type": "shell", "command": cmd,
             "problemMatcher": ["$gcc"]}
        if default:
            t["group"] = {"kind": "build", "isDefault": True}
        return t
    doc = {
        "version": "2.0.0",
        "tasks": [
            task("build", "make", default=True),
            task("flash", "make flash"),
            task("clean", "make clean"),
            task("size", "make size"),
        ],
    }
    return json.dumps(doc, indent=2) + "\n"


def emit_vscode_cpp_properties():
    """Point the VS Code C/C++ extension at compile_commands.json so IntelliSense
    matches the real build exactly."""
    doc = {
        "version": 4,
        "configurations": [{
            "name": "AVR (erosgen)",
            "compilerPath": "/usr/bin/avr-gcc",
            "cStandard": "c99",
            "intelliSenseMode": "linux-gcc-x64",
            "compileCommands": "${workspaceFolder}/compile_commands.json",
        }],
    }
    return json.dumps(doc, indent=2) + "\n"
