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
        from erosgen.models import resolve_models
        items = list(erosgen.collect_diagnostics(self.plain,
                                                 self.path or Path("app.yaml")))
        app_dir = self.path.parent if self.path else Path(".")
        msink = Diagnostics(strict=False)
        try:
            resolve_models(self.plain, app_dir, msink)
            resolve_asw_tasks(self.plain, msink)
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

    def add_model(self, name, codegen_dir, runnable, rate_ms=10):
        """Add a models: entry with each IN_/OUT_ signal as a port (driver TBD -
        the live diagnostics then flag each as needing a binding)."""
        mi = erosgen.parse_model(Path(codegen_dir), name)
        ports = {"in": [], "out": []}
        for s in mi.signals:
            if s.direction in ("in", "out"):
                ports[s.direction].append({"signal": s.name})
        self.doc.setdefault("models", []).append({
            "name": name, "codegen_dir": str(codegen_dir),
            "runnable": runnable, "rate_ms": int(rate_ms), "ports": ports})

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
        binding params from a previous driver are cleared first, so re-binding
        adc->pwm can't leave an orphaned 'channel' behind."""
        pd = self._port_dict(name, signal)
        if pd is None:
            return False
        for k in ("channel", "port", "bit", "slope", "offset"):
            pd.pop(k, None)
        pd["driver"] = driver
        pd.update(params)
        return True

    def port_binding(self, name, signal):
        """Human-readable binding for a port ('adc channel=0', 'unbound', ...)."""
        pd = self._port_dict(name, signal)
        if pd is None:
            return ""
        drv = pd.get("driver")
        if not drv:
            return "unbound"
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

    def budget(self):
        """Pre-flash static-RAM plan (bytes) - what the tool's report prints, so
        'too much RAM' is visible before building. None if the config is invalid.
        NOTE: true flash/RAM needs a compile (avr-size); this is the deliberate,
        over-counting non-LTO estimate."""
        try:
            s = erosgen.System(self.plain, self.path or Path("app.yaml"))
        except erosgen.ConfigError:
            return None
        uart = s.peripherals.get("uart")
        rings = 0
        if uart is not None:
            uart = uart or {}
            rings = (int(uart.get("tx_ring", UART_TX_RING_DEFAULT))
                     + int(uart.get("rx_ring", UART_RX_RING_DEFAULT)))
        b = s.budget or {}
        return {
            "kernel": KERNEL_STATE_BYTES,                  # SSOT: erosgen.constants
            "arena": s.pool_block * s.pool_blocks,
            "rings": rings,
            "sram_total": int(b.get("sram_total", 2048)),
        }

    # ---- new project + editing -----------------------------------------
    def new(self, name="app", mcu="atmega328p"):
        """Start a fresh, unsaved project from a minimal valid skeleton."""
        self.path = None
        self.doc = {
            "system": {"name": name, "mcu": mcu,
                       "hooks": {"startup": True, "error": True,
                                 "shutdown": True}},
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
                _n, sigs, _r = self.model_signals(cg, name=name)
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
                    "params": {k: pd[k] for k in ("channel", "port", "bit")
                               if k in pd},
                    "description": pd.get("description", "")})
        return rows

    def unbind_port(self, name, signal):
        """Clear a port's binding (back to 'unbound')."""
        pd = self._port_dict(name, signal)
        if pd is not None:
            for k in ("driver", "channel", "port", "bit", "slope", "offset"):
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
