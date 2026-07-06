"""Resolve app.yaml `models:` entries into RTE-ready bound models.

Ties parse/ert.py (what the SWC exports) to bind.py (is each port binding
type-safe) so emit/rte.py can generate Rte_Cfg.h / Rte.c. Structural checks and
binding checks both flow through the Diagnostics sink. Schema: rte/README.md.
"""
from dataclasses import dataclass
from pathlib import Path

from .bind import DRIVERS, check_binding
from .parse.ert import ModelInterface, Signal, parse_model
from .validate import check_keys

PORT_PARAM_KEYS = ("channel", "port", "bit")


@dataclass
class BoundPort:
    signal: Signal
    direction: str        # "in" | "out"
    driver: str
    params: dict
    stem: str             # signal name without IN_/OUT_ (e.g. "KnbVal_Z")
    slope: object = None  # opt-in calibration: port = raw*slope + offset
    offset: object = None  # None slope => pass the raw driver value through

    @property
    def tag(self):        # #define tag, e.g. "KNBVAL_Z"
        return self.stem.upper()

    @property
    def scaled(self):
        return self.slope is not None


@dataclass
class ResolvedModel:
    name: str
    init_fn: str
    runnable_fn: str
    rate_ms: int
    inputs: list          # BoundPort[]
    outputs: list         # BoundPort[]
    interface: ModelInterface


def _stem(signal_name):
    for p in ("IN_", "OUT_"):
        if signal_name.startswith(p):
            return signal_name[len(p):]
    return signal_name


def _parse_scaling(pd, driver, where, sink):
    """Read a port's opt-in slope/offset calibration. Returns (slope, offset),
    or (None, None) when the port declares none. The adapter computes
    out = in*slope + offset in its own dataflow direction (input port: value
    from the raw reading; output port: driver value from the port). Rejected on
    boolean drivers (dio), where a linear scale is meaningless."""
    if "slope" not in pd and "offset" not in pd:
        return None, None
    slope, offset = pd.get("slope", 1), pd.get("offset", 0)
    for key, v in (("slope", slope), ("offset", offset)):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            sink.error("SCALING_NOT_NUMBER",
                       f"{where}: {key} must be a number", where)
            return None, None
    drv = DRIVERS.get(driver)
    if drv is not None and drv.boolean:
        sink.error("SCALING_UNSUPPORTED",
                   f"{where}: slope/offset scaling is not supported on the "
                   f"boolean '{driver}' driver", where)
        return None, None
    return slope, offset


def resolve_model(mspec, app_dir, sink):
    """Parse the model's ERT interface and validate its port bindings. Returns a
    ResolvedModel, or None if it can't be resolved (missing dir / no name)."""
    where = (f"model '{mspec.get('name', '?')}'"
             if isinstance(mspec, dict) else "model")
    if not check_keys(mspec, "model", where, sink):
        return None
    name = mspec.get("name")
    if not name:
        sink.error("MODEL_NO_NAME", "model: needs a 'name'", "models")
        return None
    codegen_dir = mspec.get("codegen_dir")
    if not codegen_dir:
        sink.error("MODEL_NO_CODEGEN", f"{where}: needs 'codegen_dir'", where)
        return None
    if mspec.get("rate_ms") is None:
        sink.error("MODEL_NO_RATE", f"{where}: needs 'rate_ms'", where)

    try:
        iface = parse_model(Path(app_dir) / codegen_dir, name)
    except FileNotFoundError as e:
        sink.error("MODEL_NOT_FOUND", str(e), where)
        return None

    init_fn = mspec.get("init", iface.init_fn)
    runnable_fn = mspec.get("runnable")
    if runnable_fn is None:
        runnable_fn = iface.runnable_fns[0] if iface.runnable_fns else ""
        if not runnable_fn:
            sink.error("MODEL_NO_RUNNABLE",
                       f"{where}: no runnable entry point found; set 'runnable'",
                       where)
    elif runnable_fn not in iface.runnable_fns:
        sink.warning("MODEL_RUNNABLE_UNKNOWN",
                     f"{where}: runnable '{runnable_fn}' is not among the "
                     f"model's entry points {iface.runnable_fns}", where)

    inputs, outputs = bind_ports(iface, mspec, where, sink)
    return ResolvedModel(name, init_fn, runnable_fn, mspec.get("rate_ms"),
                         inputs, outputs, iface)


def bind_ports(iface, spec, where, sink):
    """Bind every port in spec['ports'] against `iface` (the SWC's exported
    signals). Shared by codegen models (iface parsed from the ert dir) and
    hand-authored ASW tasks (iface synthesized from the YAML) - the binding
    rules are identical. Returns (inputs, outputs) as BoundPort lists."""
    ports = spec.get("ports", {}) or {}
    check_keys(ports, "ports", f"{where} ports", sink)
    inputs, outputs = [], []
    for direction, bucket in (("in", inputs), ("out", outputs)):
        for i, pd in enumerate(ports.get(direction, []) or []):
            pw = f"{where} {direction}[{i}]"
            if not check_keys(pd, "port", pw, sink):
                continue
            signame = pd.get("signal")
            if not signame:
                sink.error("PORT_NO_SIGNAL", f"{pw}: needs a 'signal'", pw)
                continue
            sig = iface.signal(signame)
            if sig is None:
                sink.error("PORT_UNKNOWN_SIGNAL",
                           f"{pw}: signal '{signame}' is not exported by "
                           f"'{iface.name}'", pw)
                continue
            driver = pd.get("driver")
            if not driver:
                sink.error("PORT_NO_DRIVER", f"{pw}: needs a 'driver'", pw)
                continue
            params = {k: pd[k] for k in PORT_PARAM_KEYS if k in pd}
            slope, offset = _parse_scaling(pd, driver, pw, sink)
            spec_ = check_binding(sig, direction, driver, params, sink, pw,
                                  scaled=slope is not None)
            if spec_ is not None:
                bucket.append(BoundPort(sig, direction, driver, params,
                                        _stem(signame), slope, offset))
    return inputs, outputs


def resolve_models(doc, app_dir, sink):
    """Resolve every models: entry in the doc (empty list if none)."""
    out = []
    for m in (doc.get("models") or []):
        rm = resolve_model(m, app_dir, sink)
        if rm is not None:
            out.append(rm)
    # Port #defines (RTE_CFG_<TAG>_*) share one Rte_Cfg.h namespace, so a port
    # stem reused across models would collide - flag it before it miscompiles.
    if len(out) > 1:
        owner = {}
        for rm in out:
            for port in rm.inputs + rm.outputs:
                if port.tag in owner:
                    sink.error("PORT_STEM_COLLISION",
                               f"port stem '{port.stem}' is used by both "
                               f"'{owner[port.tag]}' and '{rm.name}'; stems must "
                               "be unique across models (shared Rte_Cfg namespace)",
                               f"model '{rm.name}'")
                else:
                    owner[port.tag] = rm.name
    return out
