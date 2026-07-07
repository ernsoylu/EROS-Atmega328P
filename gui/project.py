"""GUI-agnostic project model: the bridge between the erosgen engine and the UI.

Holds the loaded app.yaml as a ruamel round-trip document (comments/formatting
preserved on save) and exposes the engine's results as plain data plus
load/save/generate actions. No Qt here - fully unit-testable.
"""
import contextlib
import io
import re
from pathlib import Path

from ruamel.yaml import YAML

import erosgen
from erosgen.constants import (KERNEL_STATE_BYTES, UART_RX_RING_DEFAULT,
                               UART_TX_RING_DEFAULT)

_yaml = YAML()          # round-trip by default (preserves comments + key order)
_yaml.preserve_quotes = True
_yaml.indent(mapping=2, sequence=4, offset=2)  # keep indented block sequences
# NOTE: ruamel preserves comments and structure; flow-map inner spacing
# ({ a } -> {a}) may still normalize on save. Comment preservation is the point.


def _plain(x):
    """Deep-convert ruamel CommentedMap/Seq to plain dict/list for the engine."""
    if isinstance(x, dict):
        return {k: _plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    return x


class ProjectModel:
    def __init__(self, path=None):
        self.path = None
        self.doc = {}
        if path:
            self.load(path)

    def load(self, path):
        # Validate the path before touching the filesystem: resolve symlinks and
        # require an existing regular file, so a bad CLI arg / path can't reach
        # read_text() on something unexpected.
        p = Path(path).expanduser().resolve()
        if not p.is_file():
            raise FileNotFoundError(f"erosgen GUI: not a readable file: {p}")
        self.path = p
        self.doc = _yaml.load(p.read_text()) or {}

    def save(self, path=None):
        self.path = Path(path or self.path)
        with self.path.open("w") as f:
            _yaml.dump(self.doc, f)

    @property
    def plain(self):
        return _plain(self.doc)

    def _system(self):
        return self.plain.get("system") or {}

    @property
    def name(self):
        return self._system().get("name", "app")

    @property
    def mcu(self):
        return self._system().get("mcu", "atmega328p")

    def tasks(self):
        rows = []
        for t in self.plain.get("tasks", []) or []:
            if not isinstance(t, dict):
                continue
            if t.get("autostart"):
                kind = "autostart"
            elif t.get("period_ms") is not None:
                kind = f"{t['period_ms']} ms"
            else:
                kind = "aperiodic"
            rows.append({"name": t.get("name", "?"), "kind": kind,
                         "wcet_ms": t.get("wcet_ms", 1)})
        return rows

    def models(self):
        rows = []
        for m in self.plain.get("models", []) or []:
            if isinstance(m, dict):
                rows.append({"name": m.get("name", "?"),
                             "rate_ms": m.get("rate_ms"),
                             "codegen_dir": m.get("codegen_dir", "")})
        return rows

    def diagnostics(self):
        """Live, non-throwing [Diagnostic] for the current document - the GUI's
        problem list. Merges System validation (collect_diagnostics) with port
        binding validation for both codegen models (resolve_models) and hand ASW
        tasks (resolve_asw_tasks), so unbound/mistyped ports on either show up."""
        from erosgen import Diagnostics
        from erosgen.asw import resolve_asw_tasks
        from erosgen.models import resolve_connections, resolve_models
        items = list(erosgen.collect_diagnostics(self.plain,
                                                 self.path or Path("app.yaml")))
        app_dir = self.path.parent if self.path else Path(".")
        msink = Diagnostics(strict=False)
        try:
            rms = (resolve_models(self.plain, app_dir, msink)
                   + resolve_asw_tasks(self.plain, msink))
            resolve_connections(rms, self._engine_priorities(), msink)
        except Exception:  # pragma: no cover - binding is best-effort in the GUI
            pass
        seen, merged = set(), []
        for d in items + list(msink.items):
            key = (d.severity, d.code, d.location, d.message)
            if key not in seen:
                seen.add(key)
                merged.append(d)
        return merged

    def locate(self, diag):
        """Best-effort (path, line) 'jump to source' target for a diagnostic.
        Resolves an app.yaml logical location - '<section>[<i>]' (e.g. tasks[1],
        gpio[0]) or a top-level section (peripherals.uart, system) - to a 1-based
        line via ruamel's line info; line is None when it can't be pinned but the
        file still opens. (None, None) when the project is unsaved."""
        if self.path is None:
            return None, None
        return self.path, self._line_of(getattr(diag, "location", "") or "")

    def _line_of(self, location):
        doc = self.doc
        if not hasattr(doc, "lc"):          # a fresh (unsaved) plain-dict doc
            return None
        m = re.match(r"(\w+)\[(\d+)\]", location)   # "<section>[<i>]"
        if m:
            seq = doc.get(m.group(1))
            try:
                return seq.lc.data[int(m.group(2))][0] + 1
            except (AttributeError, KeyError, IndexError, TypeError):
                pass                         # fall back to the section's line
        head = re.split(r"[.\[ ]", location, maxsplit=1)[0]
        try:
            return doc.lc.data[head][0] + 1
        except (AttributeError, KeyError, TypeError):
            return None

    # ---- model / peripheral binding ------------------------------------
    def model_signals(self, codegen_dir, name=None):
        """Parse a codegen <model>_ert_rtw dir. Returns
        (model_name, [(signal, ctype, direction)], runnable_fn)."""
        base = Path(codegen_dir)
        name = name or base.name.replace("_ert_rtw", "")
        mi = erosgen.parse_model(base, name)
        sigs = [(s.name, s.ctype, s.direction) for s in mi.signals]
        runnable = mi.runnable_fns[0] if mi.runnable_fns else ""
        return name, sigs, runnable

    def add_model(self, name, codegen_dir, runnable, rate_ms=10, model=None):
        """Add a models: entry with each IN_/OUT_ signal as a port (driver TBD -
        the live diagnostics then flag each as needing a binding). `name` is the
        INSTANCE name (task/namespace); pass `model` (the ERT file prefix) to add
        the same SWC more than once - each instance then needs its own `name`,
        rate and port bindings."""
        prefix = model or name
        mi = erosgen.parse_model(Path(codegen_dir), prefix)
        ports = {"in": [], "out": []}
        for s in mi.signals:
            if s.direction in ("in", "out"):
                ports[s.direction].append({"signal": s.name})
        entry = {"name": name, "codegen_dir": str(codegen_dir),
                 "runnable": runnable, "rate_ms": int(rate_ms), "ports": ports}
        if model and model != name:
            entry["model"] = model          # ERT prefix, distinct from instance
        self.doc.setdefault("models", []).append(entry)

    def _swc_entry(self, name):
        """The mutable ruamel dict for a bound SWC: a models: entry OR a
        hand-authored ASW task (a task with ports/calibrations). Both bind ports
        identically, so the binding helpers below work on either. Edits
        round-trip through ruamel."""
        for m in self.doc.get("models", []) or []:
            if isinstance(m, dict) and m.get("name") == name:
                return m
        for t in self.doc.get("tasks", []) or []:
            if (isinstance(t, dict) and t.get("name") == name
                    and ("ports" in t or "calibrations" in t)):
                return t
        return None

    def model_port_signals(self, name):
        out = []
        e = self._swc_entry(name)
        if e:
            for direction in ("in", "out"):
                for pd in (e.get("ports", {}) or {}).get(direction, []) or []:
                    if isinstance(pd, dict) and "signal" in pd:
                        out.append((pd["signal"], direction))
        return out

    def bind_port(self, name, signal, driver, **params):
        """Bind an SWC's port signal to a peripheral driver (+ params). Stale
        binding params/source from a previous binding are cleared first, so
        re-binding adc->pwm (or source->driver) can't leave an orphan behind."""
        pd = self._port_dict(name, signal)
        if pd is None:
            return False
        for k in ("channel", "port", "bit", "slope", "offset", "source"):
            pd.pop(k, None)
        pd["driver"] = driver
        pd.update(params)
        return True

    def set_port_source(self, name, signal, source):
        """Wire an input to another SWC's output (internal ASW<->ASW signal),
        clearing any hardware driver/params. `source` is '<SWC>.<OUT_signal>'."""
        pd = self._port_dict(name, signal)
        if pd is None:
            return False
        for k in ("driver", "channel", "port", "bit", "slope", "offset"):
            pd.pop(k, None)
        pd["source"] = source
        return True

    def available_sources(self, consumer):
        """Internal-signal sources an input can read: every OTHER SWC's output as
        '<SWC>.<OUT_signal>'. Feeds the input's driver dropdown with ASW<->ASW
        wiring options."""
        out = []
        for e in self._swc_entries():
            if e.get("name") == consumer:
                continue
            for pd in (e.get("ports", {}) or {}).get("out", []) or []:
                if isinstance(pd, dict) and pd.get("signal"):
                    out.append(f"{e['name']}.{pd['signal']}")
        return out

    def _swc_entries(self):
        """Every SWC dict: models + hand ASW tasks (a task with an interface)."""
        swcs = [m for m in self.plain.get("models", []) or []
                if isinstance(m, dict) and m.get("name")]
        swcs += [t for t in self.plain.get("tasks", []) or []
                 if isinstance(t, dict) and t.get("name")
                 and ("ports" in t or "calibrations" in t)]
        return swcs

    def port_binding(self, name, signal):
        """Human-readable binding for a port ('adc channel=0', 'unbound', an
        internal '<- App1.OUT_x' source, ...)."""
        pd = self._port_dict(name, signal)
        if pd is None:
            return ""
        if pd.get("source"):
            return f"← {pd['source']}"       # <- internal signal
        drv = pd.get("driver")
        if not drv:
            return "unbound"
        if drv == "internal":
            return "internal"
        extra = " ".join(f"{k}={pd[k]}" for k in ("channel", "port", "bit")
                         if k in pd)
        return f"{drv} {extra}".strip()

    # ---- editing --------------------------------------------------------
    def available_mcus(self):
        """MCU profiles the engine can target (mcu/*.yaml stems)."""
        mcu_dir = Path(erosgen.__file__).resolve().parent / "mcu"
        return sorted(p.stem for p in mcu_dir.glob("*.yaml"))

    def set_mcu(self, name):
        """Edit the target MCU in the live document (diagnostics update next
        time they're read - that's the live-editing loop)."""
        self.doc.setdefault("system", {})["mcu"] = name

    @property
    def kernel_dir(self):
        return self._system().get("kernel_dir", "")

    @property
    def drivers_dir(self):
        return self._system().get("drivers_dir", "")

    def set_dir(self, key, value):
        """Set (or clear when blank) system.kernel_dir / system.drivers_dir - the
        paths Generate needs to find the EROS kernel + peripheral driver sources.
        `key` is 'kernel_dir' or 'drivers_dir'."""
        sysd = self.doc.setdefault("system", {})
        if value:
            sysd[key] = value
        else:
            sysd.pop(key, None)

    def detect_dirs(self):
        """Best-effort auto-detect the EROS kernel/ and drivers/ folders by
        walking up from the erosgen install to the repo root that holds both
        (identified by kernel/eros.h). {} if erosgen lives outside an EROS tree.
        Absolute paths, so they work from a project saved anywhere."""
        here = Path(erosgen.__file__).resolve()
        for base in here.parents:
            kernel, drivers = base / "kernel", base / "drivers"
            if (kernel / "eros.h").is_file() and drivers.is_dir():
                return {"kernel_dir": str(kernel), "drivers_dir": str(drivers)}
        return {}

    def autodetect_dirs(self):
        """Fill kernel_dir/drivers_dir from detect_dirs(). Returns True if found."""
        found = self.detect_dirs()
        for key, value in found.items():
            self.set_dir(key, value)
        return bool(found)

    def budget(self):
        """Pre-flash static-RAM plan (bytes) - what the tool's report prints, so
        'too much RAM' is visible before building. None if the config is invalid.
        NOTE: true flash/RAM needs a compile (avr-size); this is the deliberate,
        over-counting non-LTO estimate."""
        # Best-effort like every other live read: an incomplete config being
        # edited can make strict System() raise more than ConfigError (a
        # KeyError/TypeError on a half-typed field), so swallow broadly and show
        # "config invalid" rather than crashing the panel.
        try:
            s = erosgen.System(self.plain, self.path or Path("app.yaml"))
            uart = s.peripherals.get("uart")
            rings = 0
            if uart is not None:
                uart = uart or {}
                rings = (int(uart.get("tx_ring", UART_TX_RING_DEFAULT))
                         + int(uart.get("rx_ring", UART_RX_RING_DEFAULT)))
            b = s.budget or {}
            return {
                "kernel": KERNEL_STATE_BYTES,              # SSOT: erosgen.constants
                "arena": s.pool_block * s.pool_blocks,
                "rings": rings,
                "sram_total": int(b.get("sram_total", 2048)),
            }
        except Exception:
            return None

    # ---- new project + editing -----------------------------------------
    def new(self, name="app", mcu="atmega328p"):
        """Start a fresh, unsaved project from a minimal valid skeleton, with the
        kernel/drivers dirs auto-detected so it can generate + build out of the
        box (when erosgen runs from an EROS tree)."""
        self.path = None
        system = {"name": name, "mcu": mcu,
                  "hooks": {"startup": True, "error": True, "shutdown": True}}
        system.update(self.detect_dirs())      # kernel_dir/drivers_dir if found
        self.doc = {
            "system": system,
            "tasks": [
                {"name": "init", "autostart": True, "wcet_ms": 1},
                {"name": "main", "period_ms": 100, "wcet_ms": 1},
            ],
            "resources": [{"name": "app", "users": ["main"]}],
        }

    def set_name(self, name):
        self.doc.setdefault("system", {})["name"] = name

    def add_task(self, name, period_ms=None, wcet_ms=1, autostart=False):
        t = {"name": name, "wcet_ms": int(wcet_ms)}
        if autostart:
            t["autostart"] = True
        elif period_ms is not None:
            t["period_ms"] = int(period_ms)
        self.doc.setdefault("tasks", []).append(t)

    def remove_task(self, name):
        tasks = self.doc.get("tasks")
        if isinstance(tasks, list):
            self.doc["tasks"] = [t for t in tasks if not (
                isinstance(t, dict) and t.get("name") == name)]

    def remove_model(self, name):
        models = self.doc.get("models")
        if isinstance(models, list):
            self.doc["models"] = [m for m in models if not (
                isinstance(m, dict) and m.get("name") == name)]

    def generate(self):
        """Save, then run the generator. Returns (ok, report_text)."""
        if self.path is None:
            raise ValueError("save the project before generating")
        self.save()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = erosgen.main(["erosgen", str(self.path)])
        return rc == 0, buf.getvalue()

    # ---- unified schedule (declared tasks + one OS task per model) -------
    def schedule(self):
        """Every runnable the kernel schedules, most-urgent first.

        Each `models:` entry becomes its own periodic OS task (period = rate_ms),
        so models belong *among* the tasks, not in a separate list. Priorities
        come from the engine (SSOT: System._assign_priorities) so the order shown
        is exactly the order the kernel runs them in. Rows:
        {name, kind, is_model, period_ms, wcet_ms, autostart, priority}. `kind`
        is 'task' (plain thread), 'asw' (hand-authored SWC: has an interface) or
        'model' (codegen SWC)."""
        from erosgen.asw import is_asw_task
        prio = self._engine_priorities()
        rows = []
        for t in self.plain.get("tasks", []) or []:
            if isinstance(t, dict):
                kind = "asw" if is_asw_task(t) else "task"
                rows.append({"name": t.get("name", "?"), "kind": kind,
                             "is_model": False,
                             "period_ms": t.get("period_ms"),
                             "wcet_ms": t.get("wcet_ms", 1),
                             "autostart": bool(t.get("autostart", False)),
                             "priority": prio.get(str(t.get("name", "")).upper())})
        for m in self.plain.get("models", []) or []:
            if isinstance(m, dict):
                rows.append({"name": m.get("name", "?"), "kind": "model",
                             "is_model": True, "period_ms": m.get("rate_ms"),
                             "wcet_ms": m.get("wcet_ms", 1), "autostart": False,
                             "priority": prio.get(str(m.get("name", "")).upper())})
        # higher engine priority number = more urgent (rate-monotonic); unknown
        # priorities (config too broken to build) sort last, in listed order.
        rows.sort(key=lambda r: (r["priority"] is None, -(r["priority"] or 0)))
        return rows

    def rate_groups(self):
        """schedule() bucketed by rate for the tree: an ordered list of
        (label, [rows]) with periodic rates ascending (fastest first) then a
        trailing 'aperiodic' bucket. Within a bucket rows keep schedule() order
        (most-urgent first)."""
        groups = {}
        for r in self.schedule():
            key = r["period_ms"] if r["period_ms"] else None
            groups.setdefault(key, []).append(r)
        out = []
        for period in sorted(k for k in groups if k is not None):
            out.append((f"{period} ms", groups[period]))
        if None in groups:
            out.append(("aperiodic", groups[None]))
        return out

    def _engine_priorities(self):
        """{TASKNAME_UPPER: priority} from the engine, or {} if it can't build.
        Collect mode so an errored config (unbound ports, bad MCU) still yields
        the priority map the schedule view needs."""
        from erosgen import Diagnostics
        try:
            s = erosgen.System(self.plain, self.path or Path("app.yaml"),
                               sink=Diagnostics(strict=False))
            return {t.name: t.priority for t in s.tasks}
        except Exception:      # pragma: no cover - engine is best-effort here
            return {}

    # ---- System page: MCU config + facts --------------------------------
    def hooks(self):
        return dict(self._system().get("hooks", {}) or {})

    def set_hook(self, name, on):
        self.doc.setdefault("system", {}).setdefault("hooks", {})[name] = bool(on)

    def idle(self):
        """Kernel idle policy: 'sleep' (default) or 'busy'."""
        return str(self._system().get("idle", "sleep"))

    def set_idle(self, mode):
        """Set the idle policy. 'sleep' is the default, so drop the key then to
        keep the app.yaml minimal; 'busy' suppresses the SLEEP instruction."""
        sysd = self.doc.setdefault("system", {})
        if mode == "sleep":
            sysd.pop("idle", None)
        else:
            sysd["idle"] = mode

    def system_facts(self):
        """Read-only MCU profile facts for the System page. Best-effort: an
        unknown MCU (no mcu/<name>.yaml) returns {} rather than raising."""
        try:
            from erosgen.mcu.profile import load_profile
            pr = load_profile(self.mcu)
        except Exception:
            return {}
        return {"f_cpu": pr.f_cpu, "programmer": pr.avrdude_programmer,
                "baud": pr.avrdude_baud, "part": pr.avrdude_part,
                "peripherals": sorted(pr.known_peripherals)}

    # ---- Task page ------------------------------------------------------
    def update_task(self, name, period_ms, wcet_ms, autostart):
        """Edit a declared task in place. period_ms falsy + not autostart =>
        aperiodic (neither key set)."""
        for t in self.doc.get("tasks", []) or []:
            if isinstance(t, dict) and t.get("name") == name:
                t["wcet_ms"] = int(wcet_ms)
                t.pop("autostart", None)
                t.pop("period_ms", None)
                if autostart:
                    t["autostart"] = True
                elif period_ms:
                    t["period_ms"] = int(period_ms)
                return True
        return False

    # ---- Model page: interfaces + inline binding ------------------------
    def _model_entry(self, name):
        for m in self.doc.get("models", []) or []:
            if isinstance(m, dict) and m.get("name") == name:
                return m
        return None

    def _task_entry(self, name):
        for t in self.doc.get("tasks", []) or []:
            if isinstance(t, dict) and t.get("name") == name:
                return t
        return None

    def model_meta(self, name):
        m = self._model_entry(name)
        if not m:
            return {}
        return {"codegen_dir": m.get("codegen_dir", ""),
                "runnable": m.get("runnable", ""), "rate_ms": m.get("rate_ms")}

    def set_model_rate(self, name, rate_ms):
        m = self._model_entry(name)
        if m is not None:
            m["rate_ms"] = int(rate_ms)

    def _port_dict(self, name, signal):
        """The mutable ruamel port mapping for a signal on any SWC (model or hand
        ASW task); edits round-trip."""
        e = self._swc_entry(name)
        if e:
            for direction in ("in", "out"):
                for pd in (e.get("ports", {}) or {}).get(direction, []) or []:
                    if isinstance(pd, dict) and pd.get("signal") == signal:
                        return pd
        return None

    def available_drivers(self):
        """{driver: {directions, required}} - which drivers can serve which port
        direction and what binding params each needs (channel / port+bit / none).
        Lets the Model page offer only valid drivers per signal."""
        from erosgen.bind import DRIVERS
        return {n: {"directions": list(d.directions), "required": list(d.required)}
                for n, d in DRIVERS.items()}

    def available_signal_types(self):
        """The rtw signal types the binding layer understands - the type dropdown
        for a hand-authored ASW port or calibration."""
        from erosgen.parse.ert import RTW_TYPES
        return list(RTW_TYPES)

    def model_interfaces(self, name):
        """Every in/out interface of an SWC (codegen model or hand ASW task) for
        the binding table. Rows: {signal, direction, ctype, driver, params,
        description}. ctype is parsed from the codegen dir for a model, or read
        from the port's declared `type` for a hand task."""
        ctypes = {}
        e = self._swc_entry(name) or {}
        cg = e.get("codegen_dir")
        if cg:
            try:
                # parse by the ERT prefix (`model`), which differs from the
                # instance `name` when one SWC is added multiple times.
                _n, sigs, _r = self.model_signals(cg, name=e.get("model") or name)
                ctypes = {s: c for s, c, _d in sigs}
            except Exception:
                pass
        rows = []
        for direction in ("in", "out"):
            for pd in (e.get("ports", {}) or {}).get(direction, []) or []:
                if not (isinstance(pd, dict) and "signal" in pd):
                    continue
                sig = pd["signal"]
                rows.append({
                    "signal": sig, "direction": direction,
                    "ctype": ctypes.get(sig) or pd.get("type", "?"),
                    "driver": pd.get("driver"),
                    "source": pd.get("source"),
                    "params": {k: pd[k] for k in ("channel", "port", "bit")
                               if k in pd},
                    "description": pd.get("description", "")})
        return rows

    def unbind_port(self, name, signal):
        """Clear a port's binding (back to 'unbound')."""
        pd = self._port_dict(name, signal)
        if pd is not None:
            for k in ("driver", "channel", "port", "bit", "slope", "offset",
                      "source"):
                pd.pop(k, None)
            return True
        return False

    # ---- hand ASW task authoring: ports + calibrations ------------------
    def is_asw(self, name):
        e = self._task_entry(name)
        return bool(e and ("ports" in e or "calibrations" in e))

    def make_asw_task(self, name):
        """Give a plain task an (empty) interface so it emits the
        <name>{,_Intfc,_Param} skeleton and can bind ports. Idempotent."""
        t = self._task_entry(name)
        if t is not None:
            t.setdefault("ports", {"in": [], "out": []})
            t.setdefault("calibrations", [])

    def add_port(self, name, direction, signal, ctype="uint16_T", description=""):
        """Append an in/out port to an SWC's interface (hand ASW task)."""
        e = self._swc_entry(name) or self._task_entry(name)
        if e is None:
            return False
        bucket = e.setdefault("ports", {}).setdefault(direction, [])
        bucket.append({"signal": signal, "type": ctype,
                       "description": description})
        return True

    def remove_port(self, name, signal):
        e = self._swc_entry(name)
        if e is None:
            return False
        for direction in ("in", "out"):
            b = (e.get("ports", {}) or {}).get(direction)
            if isinstance(b, list):
                e["ports"][direction] = [pd for pd in b if not (
                    isinstance(pd, dict) and pd.get("signal") == signal)]
        return True

    def set_port_meta(self, name, signal, ctype=None, description=None):
        pd = self._port_dict(name, signal)
        if pd is None:
            return False
        if ctype is not None:
            pd["type"] = ctype
        if description is not None:
            pd["description"] = description
        return True

    def calibrations(self, name):
        e = self._swc_entry(name) or {}
        out = []
        for c in e.get("calibrations", []) or []:
            if isinstance(c, dict) and c.get("name"):
                out.append({"name": c["name"], "type": c.get("type", "uint16_T"),
                            "value": c.get("value", 0),
                            "description": c.get("description", "")})
        return out

    def add_calibration(self, name, cal, ctype="uint16_T", value=0, description=""):
        e = self._swc_entry(name) or self._task_entry(name)
        if e is None:
            return False
        e.setdefault("calibrations", []).append(
            {"name": cal, "type": ctype, "value": value,
             "description": description})
        return True

    def remove_calibration(self, name, cal):
        e = self._swc_entry(name)
        if e is None:
            return False
        cals = e.get("calibrations")
        if isinstance(cals, list):
            e["calibrations"] = [c for c in cals if not (
                isinstance(c, dict) and c.get("name") == cal)]
        return True

    def set_calibration(self, name, cal, ctype=None, value=None, description=None):
        for c in (self._swc_entry(name) or {}).get("calibrations", []) or []:
            if isinstance(c, dict) and c.get("name") == cal:
                if ctype is not None:
                    c["type"] = ctype
                if value is not None:
                    c["value"] = value
                if description is not None:
                    c["description"] = description
                return True
        return False

    def _runnable_entry(self, name):
        """The mutable dict for any scheduled runnable - a declared task OR a
        models: entry (so priority ordering can touch both kinds)."""
        return self._model_entry(name) or self._task_entry(name)

    def rate_peers(self, name):
        """Same-rate runnables (tasks AND codegen tasks) in tree order, i.e.
        most-urgent first - the peer set the priority control arranges."""
        row = next((r for r in self.schedule() if r["name"] == name), None)
        if not row or not row["period_ms"]:
            return []
        return [r["name"] for r in self.schedule()
                if r["period_ms"] == row["period_ms"]]

    def set_rate_position(self, name, tree_pos):
        """Place `name` at `tree_pos` (0 = most urgent) among its same-rate peers,
        interleaving hand tasks and codegen tasks freely. Writes an explicit
        `order:` on every peer (higher = more urgent) so the engine's tie-break
        reproduces the arrangement. Returns True if applied."""
        peers = self.rate_peers(name)
        if name not in peers:
            return False
        peers.remove(name)
        tree_pos = max(0, min(len(peers), tree_pos))
        peers.insert(tree_pos, name)            # desired most-urgent-first order
        n = len(peers)
        for i, nm in enumerate(peers):
            e = self._runnable_entry(nm)
            if e is not None:
                e["order"] = n - 1 - i          # top (i=0) => highest order
        return True

    # ---- resources (OSEK shared sections; the kernel needs >= 1) ---------
    def resources(self):
        """[{name, users}] - every declared resource. The kernel config table
        must hold at least one (NO_RESOURCES otherwise) and each needs a
        non-empty users list (RES_NO_USERS otherwise)."""
        out = []
        for r in self.plain.get("resources", []) or []:
            if isinstance(r, dict) and r.get("name"):
                out.append({"name": r["name"],
                            "users": list(r.get("users", []) or [])})
        return out

    def runnable_names(self):
        """Every task/codegen-task name a resource can list as a user."""
        return [r["name"] for r in self.schedule()]

    def add_resource(self, name, users=None):
        self.doc.setdefault("resources", []).append(
            {"name": name, "users": list(users or [])})

    def remove_resource(self, name):
        res = self.doc.get("resources")
        if isinstance(res, list):
            self.doc["resources"] = [r for r in res if not (
                isinstance(r, dict) and r.get("name") == name)]

    def set_resource_users(self, name, users):
        for r in self.doc.get("resources", []) or []:
            if isinstance(r, dict) and r.get("name") == name:
                r["users"] = list(users)
                return True
        return False

    # ---- peripherals (activate + configure; pins for conflict awareness) --
    def _profile(self):
        from erosgen.mcu.profile import load_profile
        return load_profile(self.mcu)

    def known_peripherals(self):
        """Every peripheral this MCU offers (profile order): [{name, active,
        pins}]. `pins` are the pins it owns when active (profile SSOT for
        conflict detection). [] on an unknown MCU."""
        try:
            pr = self._profile()
        except Exception:
            return []
        active = self.plain.get("peripherals", {}) or {}
        return [{"name": n, "active": n in active,
                 "pins": list(pr.peripheral_pins.get(n, []))}
                for n in pr.known_peripherals]

    def peripheral_active(self, name):
        return name in (self.plain.get("peripherals", {}) or {})

    def activate_peripheral(self, name, on=True):
        """Add/remove a peripheral in the peripherals: section. Deactivating drops
        its config too (re-activating starts from defaults)."""
        per = self.doc.setdefault("peripherals", {})
        if on:
            if name not in per:
                per[name] = {}
        else:
            per.pop(name, None)

    def peripheral_config(self, name):
        return dict((self.plain.get("peripherals", {}) or {}).get(name) or {})

    def set_peripheral_prop(self, name, key, value):
        """Set (or clear when blank) one property of an active peripheral."""
        per = self.doc.setdefault("peripherals", {})
        cfg = per.get(name)
        if not isinstance(cfg, dict):
            cfg = {}
            per[name] = cfg
        if value in (None, ""):
            cfg.pop(key, None)
        else:
            cfg[key] = value

    def pwm_achieved(self, freq_hz):
        """(actual_hz, timer_name) for a requested pwm freq_hz on this MCU, or
        None if unreachable - the live 'you'll actually get X Hz' feedback."""
        try:
            from erosgen.pwmcfg import pwm_config, pwm_timer
            pr = self._profile()
            cfg, timer = pwm_config(pr, int(freq_hz)), pwm_timer(pr)
            return (cfg[2], timer[0]) if cfg and timer else None
        except Exception:
            return None

    def timer0_pwm_achieved(self, freq_hz):
        """Nearest Timer0 PWM frequency (8-bit: prescaler-set only), or None."""
        try:
            from erosgen.pwmcfg import f_cpu_hz, pwm_timer, timer0_pwm_cs
            pr = self._profile()
            timer = pwm_timer(pr, "timer0_pwm")
            cfg = (timer0_pwm_cs(int(freq_hz), f_cpu_hz(pr), timer[1])
                   if timer else None)
            return cfg[1] if cfg else None
        except Exception:
            return None

    def _adc_alias_pins(self, pr):
        """{adc_channel: PXn} from the board's A-pin aliases (A0 -> PC0)."""
        return {int(k[1:]): v for k, v in pr.aliases.items()
                if re.fullmatch(r"A\d+", k)}

    def owned_pins(self, exclude=None):
        """Pins already claimed - by an active peripheral, a gpio entry, a dio
        port binding, or an ADC channel in use (via its A-pin) - so the pickers
        can hide them and a conflict becomes impossible to select rather than
        only flagged. `exclude` is an (swc, signal) whose own claim is skipped,
        so a port's current pin/channel stays selectable."""
        pins = set()
        try:
            pr = self._profile()
        except Exception:
            return pins
        for name in (self.plain.get("peripherals", {}) or {}):
            pins.update(pr.peripheral_pins.get(name, []))
        for g in self.plain.get("gpio", []) or []:
            if isinstance(g, dict) and g.get("pin"):
                pins.add(pr.aliases.get(str(g["pin"]).upper(), str(g["pin"])))
        adc_alias = self._adc_alias_pins(pr)
        for e in self._swc_entries():
            for direction in ("in", "out"):
                for pd in (e.get("ports", {}) or {}).get(direction, []) or []:
                    if not isinstance(pd, dict):
                        continue
                    if exclude and (e.get("name"), pd.get("signal")) == exclude:
                        continue
                    if (pd.get("driver") == "dio" and pd.get("port")
                            and pd.get("bit") is not None):
                        pins.add(f"P{pd['port']}{pd['bit']}")
                    elif (pd.get("driver") == "adc"
                          and pd.get("channel") is not None):
                        pin = adc_alias.get(int(pd["channel"]))
                        if pin:
                            pins.add(pin)
        return pins

    def pinout(self):
        """The whole-chip pin map for the pinout view: per port letter, bits 0..7
        mapped to {pin, aliases, owners, kind, conflict, usable}. `kind` is
        periph|gpio|port|free|na; `conflict` = claimed by more than one owner.
        Engine-backed (profile pins/aliases + the same ownership rules the
        conflict-aware pickers use), so the grid renders facts, not GUI logic."""
        try:
            pr = self._profile()
        except Exception:
            return {"ports": [], "cells": {}}
        silks = {}                                  # pin -> [board silk]
        for silk, pin in pr.aliases.items():
            silks.setdefault(pin, []).append(silk)
        adc_alias = self._adc_alias_pins(pr)        # channel -> pin
        owners = {}                                 # pin -> [(kind, label)]

        def claim(pin, kind, label):
            owners.setdefault(pin, []).append((kind, label))

        for name in (self.plain.get("peripherals", {}) or {}):
            for pin in pr.peripheral_pins.get(name, []):
                claim(pin, "periph", name)
        for g in self.plain.get("gpio", []) or []:
            if isinstance(g, dict) and g.get("pin"):
                pin = pr.aliases.get(str(g["pin"]).upper(), str(g["pin"]))
                claim(pin, "gpio", g.get("name") or str(g["pin"]))
        for e in self._swc_entries():
            for direction in ("in", "out"):
                for pd in (e.get("ports", {}) or {}).get(direction, []) or []:
                    if not isinstance(pd, dict):
                        continue
                    sig = pd.get("signal", "?")
                    if (pd.get("driver") == "dio" and pd.get("port")
                            and pd.get("bit") is not None):
                        claim(f"P{pd['port']}{pd['bit']}", "port", sig)
                    elif (pd.get("driver") == "adc"
                          and pd.get("channel") is not None):
                        pin = adc_alias.get(int(pd["channel"]))
                        if pin:
                            claim(pin, "port", sig)
        usable = set(silks) | set(owners)
        for pins in pr.peripheral_pins.values():
            usable.update(pins)
        ports = list(pr.ports)
        cells = {}
        for port in ports:
            for bit in range(8):
                pin = f"P{port}{bit}"
                claims = owners.get(pin, [])
                cells[(port, bit)] = {
                    "pin": pin,
                    "aliases": sorted(silks.get(pin, [])),
                    "owners": [lbl for _k, lbl in claims],
                    "kind": (claims[0][0] if claims
                             else ("free" if pin in usable else "na")),
                    "conflict": len(claims) > 1,
                    "usable": pin in usable,
                }
        return {"ports": ports, "cells": cells}

    def available_dio_pins(self, name, signal):
        """dio_pins() minus pins already owned elsewhere, always keeping this
        port's current pin so it stays selectable."""
        owned = self.owned_pins(exclude=(name, signal))
        cur = self._port_dict(name, signal) or {}
        keep = (f"P{cur['port']}{cur['bit']}"
                if cur.get("port") and cur.get("bit") is not None else None)
        return [d for d in self.dio_pins()
                if d["pin"] not in owned or d["pin"] == keep]

    def available_adc_channels(self, name, signal):
        """adc_channels() minus channels whose A-pin is already owned elsewhere,
        keeping this port's current channel."""
        try:
            owned = self.owned_pins(exclude=(name, signal))
            alias = self._adc_alias_pins(self._profile())
        except Exception:
            return self.adc_channels()
        cur = (self._port_dict(name, signal) or {}).get("channel")
        out = []
        for ch in self.adc_channels():
            pin = alias.get(ch)
            if pin is None or pin not in owned or ch == cur:
                out.append(ch)
        return out

    # ---- target: chip (MCU) + board -------------------------------------
    def available_targets(self):
        """Every MCU profile grouped by chip: {chip: [profile, ...]}. A profile
        with no `extends` is a bare chip; profiles that `extends` it are boards on
        that chip (arduino_uno -> atmega328p). So one chip can carry several board
        configs. The chip's own name sorts first in its list, boards after."""
        import yaml
        mcu_dir = Path(erosgen.__file__).resolve().parent / "mcu"
        parent = {}
        for f in mcu_dir.glob("*.yaml"):
            try:
                parent[f.stem] = (yaml.safe_load(f.read_text()) or {}).get("extends")
            except Exception:
                parent[f.stem] = None

        def root(n, seen=()):
            p = parent.get(n)
            if not p or p == n or p in seen or p not in parent:
                return n
            return root(p, seen + (n,))

        groups = {}
        for name in parent:
            groups.setdefault(root(name), []).append(name)
        return {chip: sorted(v, key=lambda x: (x != chip, x))
                for chip, v in sorted(groups.items())}

    def current_chip(self):
        """The chip family of the selected profile (its `extends` root)."""
        for chip, names in self.available_targets().items():
            if self.mcu in names:
                return chip
        return self.mcu

    def board_label(self, stem):
        """The friendly board name for a profile stem (e.g. atmega328p ->
        'Arduino Nano', arduino_uno -> 'Arduino Uno'); falls back to the stem."""
        try:
            from erosgen.mcu.profile import load_profile
            return load_profile(stem).board
        except Exception:
            return stem

    def boards_for_chip(self, chip):
        """[(stem, friendly_label)] for every board profile on a chip - what the
        Board picker lists (labels, not ECU names)."""
        return [(stem, self.board_label(stem))
                for stem in self.available_targets().get(chip, [chip])]

    # ---- driver param value sets (MCU-limited, for dropdowns) -----------
    def adc_channels(self):
        """Valid ADC channel numbers for the current MCU/board. Derived from the
        board's A-pin aliases (A0..A5 -> 0..5) so it's board-specific; falls back
        to 0..7 for a profile that declares no analog aliases."""
        try:
            from erosgen.mcu.profile import load_profile
            pr = load_profile(self.mcu)
        except Exception:
            return list(range(8))
        chans = sorted({int(k[1:]) for k in pr.aliases
                        if re.fullmatch(r"A\d+", k)})
        return chans or list(range(8))

    def dio_pins(self):
        """Usable GPIO pins for the current MCU/board as
        [{pin, port, bit, label}]. The board's pin aliases are the real broken-out
        pins (PB5 = D13), so they're the authoritative dio choices; a bare chip
        with no aliases falls back to every valid port x bit 0..7."""
        try:
            from erosgen.mcu.profile import load_profile
            pr = load_profile(self.mcu)
        except Exception:
            return []
        silks = {}
        for silk, pin in pr.aliases.items():
            silks.setdefault(pin, []).append(silk)
        pins, seen = [], set()

        def add(pin):
            m = re.fullmatch(r"P([A-L])(\d)", pin)
            if not m or pin in seen:
                return
            seen.add(pin)
            names = silks.get(pin)
            label = pin + (f" ({', '.join(sorted(names))})" if names else "")
            pins.append({"pin": pin, "port": m.group(1),
                         "bit": int(m.group(2)), "label": label})

        for pin in silks:                       # board-exposed pins
            add(pin)
        if not pins:                            # bare chip: all port x bit
            for p in pr.ports:
                for b in range(8):
                    add(f"P{p}{b}")
        pins.sort(key=lambda d: (d["port"], d["bit"]))
        return pins


class WorkspaceModel:
    """A loaded erosproject.yaml: its app list + variants, and a generate-all.

    A GUI-agnostic bridge over erosgen.workspace, mirroring ProjectModel: the UI
    lists the apps (each opened into the normal single-app editor) and runs the
    whole workspace through the engine with an optional variant overlay.
    """

    def __init__(self, path=None):
        self.path = None
        self.doc = {}
        if path is not None:
            self.load(path)

    @staticmethod
    def is_workspace_file(path):
        """True if `path` is an erosproject.yaml (a doc with an apps: list)."""
        from erosgen.workspace import is_workspace
        try:
            return is_workspace(_yaml.load(Path(path).read_text()) or {})
        except (OSError, ValueError):
            return False

    def load(self, path):
        from erosgen.workspace import is_workspace
        p = Path(path)
        doc = _yaml.load(p.read_text()) or {}
        if not is_workspace(doc):
            raise ValueError(f"{p.name}: not a workspace (needs an 'apps:' list)")
        self.path = p
        self.doc = doc

    @property
    def name(self):
        return self.doc.get("name", self.path.stem if self.path else "")

    def variants(self):
        return sorted((self.doc.get("variants") or {}).keys())

    def apps(self):
        """[(as-written, resolved absolute Path)] for each listed app."""
        out = []
        base = self.path.parent if self.path else Path(".")
        for entry in (self.doc.get("apps") or []):
            rel = entry if isinstance(entry, str) else (entry or {}).get("path")
            if rel:
                out.append((rel, (base / rel).resolve()))
        return out

    def generate(self, variant=None):
        """Run the whole workspace through the engine. Returns (ok, report)."""
        if self.path is None:
            raise ValueError("open a workspace before generating")
        argv = ["erosgen", str(self.path)]
        if variant:
            argv += ["--variant", variant]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = erosgen.main(argv)
        return rc == 0, buf.getvalue()
