"""The parsed, validated system model: Task, Resource, System.

System.__init__ parses the YAML doc into the domain model, assigns
rate-monotonic priorities, and runs every validation gate (pin ownership,
schedulability, ...). Validation reports through a Diagnostics sink:

  * strict (default): the first error raises ConfigError - the historical CLI
    fail-fast behavior, byte-for-byte identical messages.
  * collect_diagnostics(): non-throwing, accumulates every problem for a GUI.

Guards let collect mode continue past a failed check (e.g. no periodic task,
duplicate pins) instead of crashing; collect_diagnostics also wraps the whole
build in a safety net so an un-guarded path degrades to one INTERNAL diagnostic.
"""
import difflib
import math

from .constants import MAIN_C, UART_RX_RING_DEFAULT, UART_TX_RING_DEFAULT
from .diagnostics import Diagnostic, Diagnostics
from .mcu import load_profile
from .validate import check_keys, is_pow2, normalize_pin


class Task:
    def __init__(self, d, sink):
        if not isinstance(d, dict):
            sink.error("TASK_NO_NAME", "every task needs a name", "tasks")
            d = {}
        elif "name" not in d:
            sink.error("TASK_NO_NAME", "every task needs a name", "tasks")
        self.name = str(d.get("name", "?")).upper()
        check_keys(d, "task", f"task '{self.name}'", sink)
        self.entry = d.get("entry", "Task_" + self.name.capitalize())
        self.period_ms = d.get("period_ms")     # aperiodic when omitted
        self.wcet_ms = d.get("wcet_ms", 1)
        self.autostart = bool(d.get("autostart", False))
        self.watchdog = bool(d.get("watchdog", self.period_ms is not None))
        self.runnables = d.get("runnables", [])
        # Explicit within-rate ordering: same-rate tasks tie-break by `order`
        # (higher = more urgent). None => fall back to build order, which
        # reproduces the historical "declared tasks, then model tasks" order.
        self.order = d.get("order")
        self._build_index = 0                   # set by System after building
        self.priority = None                    # assigned later
        self.period_ticks = None                # computed once tick known
        self.wcet_ticks = None
        if self.period_ms is not None and self.autostart:
            sink.error("TASK_AUTOSTART_PERIODIC",
                       f"task {self.name}: autostart tasks must be aperiodic "
                       "(they arm the alarms)", f"task {self.name}")
        if self.watchdog and self.period_ms is None:
            sink.error("TASK_WATCHDOG_NO_PERIOD",
                       f"task {self.name}: watchdog supervision requires a period",
                       f"task {self.name}")

    def compute_ticks(self, tick_ms):
        if self.period_ms is not None:
            self.period_ticks = self.period_ms // tick_ms
        # WCET rounds UP to whole ticks (never under-budget), min 1.
        self.wcet_ticks = max(1, math.ceil(self.wcet_ms / tick_ms))


class Resource:
    def __init__(self, d, tasks_by_name, sink):
        if not isinstance(d, dict):
            sink.error("RES_NO_NAME", "every resource needs a name", "resources")
            d = {}
        elif "name" not in d:
            sink.error("RES_NO_NAME", "every resource needs a name", "resources")
        self.name = str(d.get("name", "?")).upper()
        check_keys(d, "resource", f"resource '{d.get('name', self.name)}'", sink)
        users = d.get("users", [])
        if not users:
            sink.error("RES_NO_USERS",
                       f"resource {self.name}: needs a non-empty users list",
                       f"resource {self.name}")
        self.users = []
        for u in users:
            key = str(u).upper()
            if key not in tasks_by_name:
                sink.error("RES_UNKNOWN_USER",
                           f"resource {self.name}: unknown user task '{u}'",
                           f"resource {self.name}")
                continue
            self.users.append(tasks_by_name[key])
        self.mask_tick_isr = bool(d.get("mask_tick_isr", False))

    @property
    def ceiling(self):
        return max(self.users, key=lambda t: t.priority)


class System:
    def __init__(self, doc, src_path, sink=None):
        self.src = src_path
        sink = sink or Diagnostics(strict=True)
        if not isinstance(doc, dict):
            sink.error("TOP_NOT_MAPPING",
                       "top level of the YAML must be a mapping", "")
            return
        check_keys(doc, "doc", "top level", sink)
        sysd = doc.get("system", {})
        if not check_keys(sysd, "system", "system", sink):
            sysd = {}
        self.name = sysd.get("name", "app")
        # MCU profile (mcu/<name>.yaml) - the target-specific facts the emitters
        # read. Defaults to atmega328p; an unknown target is reported but falls
        # back so the rest of validation can still run.
        self.mcu = sysd.get("mcu", "atmega328p")
        try:
            self.profile = load_profile(self.mcu)
        except FileNotFoundError as e:
            sink.error("UNKNOWN_MCU", str(e), "system.mcu")
            self.profile = load_profile("atmega328p")
        self.kernel_dir = sysd.get("kernel_dir", "../kernel")
        self.drivers_dir = sysd.get("drivers_dir")
        # The kernel's Timer2 tick is hardware-fixed at 1 kHz (register
        # values in eros.c are derived for 16 MHz / 1 kHz), so 1 tick ==
        # 1 ms is a kernel invariant, not a knob. Reject anything else
        # rather than silently emitting wrong alarm periods.
        self.tick_hz = int(sysd.get("tick_hz", 1000))
        if self.tick_hz != 1000:
            sink.error("TICK_HZ",
                       "tick_hz must be 1000: the EROS kernel tick (Timer2) is "
                       "fixed at 1 kHz; change the kernel to alter it",
                       "system.tick_hz")
        self.tick_ms = 1000 // self.tick_hz if self.tick_hz else 1
        self.alarm_max_offset = int(sysd.get("alarm_max_offset", 32767))

        stack = sysd.get("stack", {})
        if not check_keys(stack, "stack", "system.stack", sink):
            stack = {}
        self.stack_canary = int(stack.get("canary", 0xC5))
        self.stack_guard = int(stack.get("guard_bytes", 8))
        self.stack_margin = int(stack.get("paint_margin", 16))

        hooks = sysd.get("hooks", {})
        if not check_keys(hooks, "hooks", "system.hooks", sink):
            hooks = {}
        self.hook_startup = int(bool(hooks.get("startup", True)))
        self.hook_error = int(bool(hooks.get("error", True)))
        self.hook_shutdown = int(bool(hooks.get("shutdown", True)))

        self.budget = sysd.get("budget")  # dict or None
        if self.budget is not None:
            check_keys(self.budget, "budget", "system.budget", sink)

        self.warnings = []  # mirrors warning diagnostics for report()

        # ---- tasks (declared + one synthesized OS task per model) ------
        # A models: entry becomes a periodic task whose body runs the RTE
        # runnable (Task_<model> in the generated Rte.c), so config.* gets
        # TASK_/ALARM_<model> - the model->OS wiring rte/README.md describes.
        self.models = self._parse_models(doc.get("models", []) or [], sink)
        # Model task bodies live in the generated Rte.c (Task_<model>), so they
        # get no asw_<rate>ms.c skeleton and aren't listed as one in the Makefile.
        self.model_task_names = {m["name"].upper() for m in self.models}
        # A declared task with a ports:/calibrations: interface is a hand-authored
        # ASW SWC: like a model its body is Task_<name> in the RTE (not an
        # asw_<rate>ms.c skeleton), and erosgen emits its <name>{,_Intfc,_Param}
        # files. Its OS task is the *declared* one, so we just retarget its entry.
        from .asw import is_asw_task
        declared = list(doc.get("tasks", []) or [])
        self.asw_tasks = [t for t in declared
                          if is_asw_task(t) and t.get("name")]
        self.asw_task_names = {str(t["name"]).upper() for t in self.asw_tasks}
        self.rte_task_names = self.model_task_names | self.asw_task_names
        task_specs = []
        for t in declared:
            if (isinstance(t, dict) and is_asw_task(t) and t.get("name")
                    and "entry" not in t):
                t = {**t, "entry": f"Task_{t['name']}"}
            task_specs.append(t)
        for m in self.models:
            task_specs.append({
                "name": m["name"],
                "period_ms": m["rate_ms"],
                "wcet_ms": m.get("wcet_ms", 1),
                "entry": f"Task_{m['name']}",
                "order": m.get("order"),
            })
        tasks = [Task(t, sink) for t in task_specs]
        for i, t in enumerate(tasks):
            t._build_index = i          # default within-rate tie-break
        if not 1 <= len(tasks) <= 8:
            sink.error("TASK_COUNT",
                       "1..8 tasks required (8-bit ready mask)", "tasks")
        names = [t.name for t in tasks]
        if len(set(names)) != len(names):
            sink.error("DUP_TASK_NAMES", "duplicate task names", "tasks")
        for t in tasks:
            t.compute_ticks(self.tick_ms)
        self.tasks = tasks
        self._assign_priorities()
        by_name = {t.name: t for t in tasks}

        # ---- alarms: one per periodic task, fastest first --------------
        self.periodic = sorted((t for t in tasks if t.period_ms is not None),
                               key=lambda t: t.period_ms)
        for t in self.periodic:
            if (t.period_ms % self.tick_ms) != 0:
                sink.error("PERIOD_NOT_MULTIPLE",
                           f"task {t.name}: period {t.period_ms} ms is not a "
                           f"multiple of the {self.tick_ms} ms tick",
                           f"task {t.name}")
            if t.period_ticks is not None and t.period_ticks > self.alarm_max_offset:
                sink.error("PERIOD_TOO_LARGE",
                           f"task {t.name}: period exceeds the "
                           f"{self.alarm_max_offset}-tick alarm range",
                           f"task {t.name}")
        if not self.periodic:
            sink.error("NO_PERIODIC",
                       "at least one periodic task required (kernel needs >= 1 alarm)",
                       "tasks")

        # ---- resources -------------------------------------------------
        self.resources = [Resource(r, by_name, sink)
                          for r in doc.get("resources", [])]
        rnames = [r.name for r in self.resources]
        if len(set(rnames)) != len(rnames):
            sink.error("DUP_RES_NAMES", "duplicate resource names", "resources")
        if not self.resources:
            sink.error("NO_RESOURCES",
                       "at least one resource required (kernel config table "
                       "cannot be empty); declare one for your main IPC/shared "
                       "section even if unused", "resources")
        if len(self.resources) > 8:
            sink.error("TOO_MANY_RESOURCES",
                       "max 8 resources (held-mask is 8-bit)", "resources")

        # ---- pool ------------------------------------------------------
        pool = doc.get("pool", {"block_size": 8, "blocks": 4})
        if not check_keys(pool, "pool", "pool", sink):
            pool = {"block_size": 8, "blocks": 4}
        self.pool_block = int(pool.get("block_size", 8))
        self.pool_blocks = int(pool.get("blocks", 4))
        if not 1 <= self.pool_blocks <= 8:
            sink.error("POOL_BLOCKS",
                       "pool blocks must be 1..8 (8-bit allocation mask)", "pool")
        if self.pool_block < 1:
            sink.error("POOL_BLOCK_SIZE",
                       "pool block_size must be >= 1 (free-list link byte)", "pool")

        # ---- peripherals ------------------------------------------------
        self.peripherals = doc.get("peripherals", {}) or {}
        if not isinstance(self.peripherals, dict):
            sink.error("BAD_MAPPING", "peripherals: expected a mapping",
                       "peripherals")
            self.peripherals = {}
        known = self.profile.known_peripherals
        for p in self.peripherals:
            if p not in known:
                hint = difflib.get_close_matches(str(p), known, n=1)
                extra = (f" (did you mean '{hint[0]}'?)" if hint else
                         f" (known: {', '.join(sorted(known))})")
                sink.error("UNKNOWN_PERIPHERAL", f"unknown peripheral '{p}'{extra}",
                           f"peripherals.{p}")
        for a, b, why in self.profile.conflicts:
            if a in self.peripherals and b in self.peripherals:
                sink.error("PERIPHERAL_CONFLICT",
                           f"peripheral conflict: {a} + {b} - {why}", "peripherals")

        uart = self.peripherals.get("uart") or {}
        if "uart" in self.peripherals:
            if not check_keys(uart, "uart", "peripherals.uart", sink):
                uart = {}
            for ring in ("tx_ring", "rx_ring"):
                default = (UART_TX_RING_DEFAULT if ring == "tx_ring"
                          else UART_RX_RING_DEFAULT)
                v = int(uart.get(ring, default))
                if not (is_pow2(v) and 2 <= v <= 256):
                    sink.error("UART_RING",
                               f"uart {ring} must be a power of two, 2..256",
                               "peripherals.uart")

        # ---- gpio + pin ownership matrix --------------------------------
        self.gpio = self._parse_gpio(doc.get("gpio", []) or [], sink)
        self._check_pins(sink)

        # ---- sources / simulink ------------------------------------------
        self.sources = list(doc.get("sources", [MAIN_C]))
        self.simulink = doc.get("simulink")
        if self.simulink is not None:
            check_keys(self.simulink, "simulink", "simulink", sink)
            if isinstance(self.simulink, dict) and "model" not in self.simulink:
                sink.error("SIMULINK_NO_MODEL",
                           "simulink section needs a 'model' name", "simulink")

        # ---- schedulability gate (codegen/README.md par.4) ---------------
        if self.periodic and all(t.period_ticks is not None for t in self.periodic):
            base = self.periodic[0].period_ticks
            load = sum(t.wcet_ticks for t in self.periodic)
            if load > base:
                sink.error("NOT_SCHEDULABLE",
                           f"not schedulable: sum of periodic WCETs ({load} ticks) "
                           f"exceeds the base period ({base} ticks); shorten WCETs, "
                           "slow a rate, or merge runnables", "tasks")
            # harmonic-rate warning (keeps shared release points aligned)
            for slow in self.periodic[1:]:
                if base and (slow.period_ticks % base) != 0:
                    msg = (f"task {slow.name}: period is not a multiple of the "
                           f"base period - release points drift apart")
                    self.warnings.append(msg)
                    sink.warning("HARMONIC", msg, f"task {slow.name}")

    def _parse_models(self, models, sink):
        """Structural check of models: entries (enough to synthesize the OS
        task). Full ERT parsing + port binding happens in models.resolve_model
        during RTE generation, not here (System stays filesystem-free)."""
        out = []
        for i, m in enumerate(models):
            where = f"model[{i}]"
            if not check_keys(m, "model", where, sink):
                continue
            name = m.get("name")
            if not name:
                sink.error("MODEL_NO_NAME", f"{where}: needs a 'name'", where)
                continue
            if m.get("rate_ms") is None:
                sink.error("MODEL_NO_RATE",
                           f"model '{name}': needs 'rate_ms'", f"model '{name}'")
                continue
            out.append(m)
        # Each model becomes one synthesized OS task, so the number of models is
        # bounded by the 8-task ready-mask limit checked below - no separate cap.
        return out

    def _parse_gpio(self, entries, sink):
        """Parse the gpio: list into normalized pin records."""
        out = []
        seen = set()
        for i, e in enumerate(entries):
            if not check_keys(e, "gpio", "gpio entry", sink):
                continue
            if "pin" not in e:
                sink.error("GPIO_NO_PIN", "gpio entry: needs a 'pin'", f"gpio[{i}]")
                continue
            pin = normalize_pin(e["pin"], self.profile, sink)
            if pin is None:
                continue
            if pin in seen:
                sink.error("GPIO_DUP_PIN", f"gpio: pin {pin} declared twice",
                           f"gpio[{i}]")
                continue
            seen.add(pin)
            direction = str(e.get("dir", "out")).lower()
            if direction not in ("in", "out"):
                sink.error("GPIO_DIR", f"gpio {pin}: dir must be 'in' or 'out'",
                           f"gpio[{i}]")
                continue
            out.append({
                "pin": pin,
                "dir": direction,
                "pullup": bool(e.get("pullup", False)),
                "name": e.get("name"),
                "init": int(bool(e.get("init", False))),  # initial out level
            })
        return out

    def _check_pins(self, sink):
        """Build a pin -> owner map across peripherals and gpio; any pin
        claimed twice is a hard error (this subsumes most pair rules)."""
        owner = {}

        def claim(pin, who):
            if pin in owner:
                sink.error("PIN_CONFLICT",
                           f"pin conflict on {pin}: '{owner[pin]}' and '{who}' "
                           "both claim it", f"pin {pin}")
                return
            owner[pin] = who

        for p in self.peripherals:
            for pin in self.profile.peripheral_pins.get(p, []):
                claim(pin, f"peripheral {p}")
            # ADC channels are only claimed if the app lists them.
            if p == "adc":
                cfg = self.peripherals.get("adc") or {}
                chans = cfg.get("channels", []) if isinstance(cfg, dict) else []
                for ch in chans or []:
                    if 0 <= int(ch) <= 5:  # A6/A7 have no port pin
                        claim(f"PC{int(ch)}", "peripheral adc")
            # acomp with an external positive input also claims AIN0/PD6.
            if p == "acomp":
                cfg = self.peripherals.get("acomp") or {}
                if isinstance(cfg, dict) and \
                        str(cfg.get("positive", "bandgap")).lower() == "ain0":
                    claim("PD6", "peripheral acomp (AIN0)")
        for g in self.gpio:
            claim(g["pin"], f"gpio {g['name'] or g['pin']}")

    def _assign_priorities(self):
        """Autostart init lowest, then aperiodic in listed order, then periodic
        rate-monotonically (fastest = highest). Same-rate tasks tie-break by
        `order` (higher = more urgent) when set - so a hand task and a codegen
        task at one rate interleave freely - else by build order (declared tasks
        before model tasks), which preserves the historical assignment."""
        def rate_key(t):
            tie = t.order if t.order is not None else t._build_index
            return (-t.period_ms, tie)     # slowest first; then less-urgent first
        auto = [t for t in self.tasks if t.autostart]
        aper = [t for t in self.tasks
                if not t.autostart and t.period_ms is None]
        peri = sorted((t for t in self.tasks if t.period_ms is not None),
                      key=rate_key)
        prio = 0
        for t in auto + aper + peri:
            t.priority = prio
            prio += 1

    # ordered helpers -----------------------------------------------------
    @property
    def tasks_by_prio(self):
        return sorted(self.tasks, key=lambda t: t.priority)

    @property
    def alive_tasks(self):
        return sorted((t for t in self.tasks if t.watchdog),
                      key=lambda t: -t.priority)


def collect_diagnostics(doc, src_path):
    """Non-throwing validation: build the model in collect mode and return every
    diagnostic (errors + warnings). Never raises - a missed guard degrades to a
    single INTERNAL diagnostic instead of propagating to the caller (GUI)."""
    sink = Diagnostics(strict=False)
    try:
        System(doc, src_path, sink=sink)
    except Exception as e:  # pragma: no cover - safety net for un-guarded paths
        sink.items.append(Diagnostic("error", "INTERNAL",
                                     f"validation aborted: {e}", ""))
    return list(sink.items)
