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
        problem list. Merges System validation (collect_diagnostics) with model
        port-binding validation (resolve_models needs the ERT files), so
        unbound/mistyped ports show up too."""
        from erosgen import Diagnostics
        from erosgen.models import resolve_models
        items = list(erosgen.collect_diagnostics(self.plain,
                                                 self.path or Path("app.yaml")))
        app_dir = self.path.parent if self.path else Path(".")
        msink = Diagnostics(strict=False)
        try:
            resolve_models(self.plain, app_dir, msink)
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

    def model_port_signals(self, model_name):
        out = []
        for m in self.doc.get("models", []) or []:
            if isinstance(m, dict) and m.get("name") == model_name:
                for direction in ("in", "out"):
                    for pd in (m.get("ports", {}) or {}).get(direction, []) or []:
                        if isinstance(pd, dict) and "signal" in pd:
                            out.append((pd["signal"], direction))
        return out

    def bind_port(self, model_name, signal, driver, **params):
        """Bind a model's port signal to a peripheral driver (+ params). Stale
        binding params from a previous driver are cleared first, so re-binding
        adc->pwm can't leave an orphaned 'channel' behind."""
        pd = self._port_dict(model_name, signal)
        if pd is None:
            return False
        for k in ("channel", "port", "bit", "slope", "offset"):
            pd.pop(k, None)
        pd["driver"] = driver
        pd.update(params)
        return True

    def port_binding(self, model_name, signal):
        """Human-readable binding for a port ('adc channel=0', 'unbound', ...)."""
        for m in self.doc.get("models", []) or []:
            if isinstance(m, dict) and m.get("name") == model_name:
                for direction in ("in", "out"):
                    for pd in (m.get("ports", {}) or {}).get(direction, []) or []:
                        if isinstance(pd, dict) and pd.get("signal") == signal:
                            drv = pd.get("driver")
                            if not drv:
                                return "unbound"
                            extra = " ".join(f"{k}={pd[k]}" for k in
                                             ("channel", "port", "bit") if k in pd)
                            return f"{drv} {extra}".strip()
        return ""

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
        {name, is_model, period_ms, wcet_ms, autostart, priority}."""
        prio = self._engine_priorities()
        rows = []
        for t in self.plain.get("tasks", []) or []:
            if isinstance(t, dict):
                rows.append({"name": t.get("name", "?"), "is_model": False,
                             "period_ms": t.get("period_ms"),
                             "wcet_ms": t.get("wcet_ms", 1),
                             "autostart": bool(t.get("autostart", False)),
                             "priority": prio.get(str(t.get("name", "")).upper())})
        for m in self.plain.get("models", []) or []:
            if isinstance(m, dict):
                rows.append({"name": m.get("name", "?"), "is_model": True,
                             "period_ms": m.get("rate_ms"),
                             "wcet_ms": m.get("wcet_ms", 1), "autostart": False,
                             "priority": prio.get(str(m.get("name", "")).upper())})
        # higher engine priority number = more urgent (rate-monotonic); unknown
        # priorities (config too broken to build) sort last, in listed order.
        rows.sort(key=lambda r: (r["priority"] is None, -(r["priority"] or 0)))
        return rows

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

    def _port_dict(self, model_name, signal):
        """The mutable ruamel port mapping for a signal (edits round-trip)."""
        m = self._model_entry(model_name)
        for direction in ("in", "out"):
            for pd in (m.get("ports", {}) or {}).get(direction, []) or [] if m else []:
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

    def model_interfaces(self, model_name):
        """Every in/out interface of a model for the binding table. Rows:
        {signal, direction, ctype, driver, params}. ctype comes from parsing the
        codegen dir (best-effort '?' if it's missing); driver/params are the
        current binding (driver None => unbound)."""
        ctypes = {}
        cg = (self._model_entry(model_name) or {}).get("codegen_dir")
        if cg:
            try:
                _n, sigs, _r = self.model_signals(cg, name=model_name)
                ctypes = {s: c for s, c, _d in sigs}
            except Exception:
                pass
        rows = []
        for sig, direction in self.model_port_signals(model_name):
            pd = self._port_dict(model_name, sig) or {}
            rows.append({
                "signal": sig, "direction": direction,
                "ctype": ctypes.get(sig, "?"), "driver": pd.get("driver"),
                "params": {k: pd[k] for k in ("channel", "port", "bit")
                           if k in pd}})
        return rows

    def unbind_port(self, model_name, signal):
        """Clear a port's binding (back to 'unbound')."""
        pd = self._port_dict(model_name, signal)
        if pd is not None:
            for k in ("driver", "channel", "port", "bit", "slope", "offset"):
                pd.pop(k, None)
            return True
        return False
