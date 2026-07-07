"""Resolve app.yaml `models:` entries into RTE-ready bound models.

Ties parse/ert.py (what the SWC exports) to bind.py (is each port binding
type-safe) so emit/rte.py can generate Rte_Cfg.h / Rte.c. Structural checks and
binding checks both flow through the Diagnostics sink. Schema: rte/README.md.
"""
from dataclasses import dataclass, field
from pathlib import Path

from .bind import DRIVERS, check_binding
from .parse.ert import ModelInterface, Signal, parse_model
from .validate import check_keys

PORT_PARAM_KEYS = ("channel", "port", "bit")


@dataclass
class BoundPort:
    signal: Signal
    direction: str        # "in" | "out"
    driver: str           # adc/dio/pwm, or "internal" (ASW<->ASW, no hardware)
    params: dict
    stem: str             # signal name without IN_/OUT_ (e.g. "KnbVal_Z")
    slope: object = None  # opt-in calibration: port = raw*slope + offset
    offset: object = None  # None slope => pass the raw driver value through
    source: "str | None" = None  # input only: "<SWC>.<OUT_signal>" it reads from
    source_signal: "str | None" = None  # resolved producer output C-global (set
    #                     by resolve_connections); the RHS the RTE assigns from

    @property
    def tag(self):        # #define tag, e.g. "KNBVAL_Z"
        return self.stem.upper()

    @property
    def scaled(self):
        return self.slope is not None

    @property
    def internal(self):   # ASW<->ASW: no BSW driver, no Rte_Read/Write adapter
        return self.driver == "internal"


@dataclass
class ResolvedModel:
    name: str
    init_fn: str
    runnable_fn: str
    rate_ms: int
    inputs: list          # BoundPort[]
    outputs: list         # BoundPort[]
    interface: ModelInterface
    extra_runnables: list = field(default_factory=list)  # [(runnable, rate_ms)]


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
    swc = mspec.get("swc")
    codegen_dir = mspec.get("codegen_dir")
    if not swc and not codegen_dir:
        sink.error("MODEL_NO_CODEGEN",
                   f"{where}: needs 'codegen_dir' (Embedded Coder) or 'swc' "
                   "(a hand-authored swc.yaml)", where)
        return None
    if mspec.get("rate_ms") is None:
        sink.error("MODEL_NO_RATE", f"{where}: needs 'rate_ms'", where)

    # Interchange: swc.yaml (Tier B) or ERT headers via the regex parser (default)
    # or the pycparser fallback (Tier A, `parser: c`, needs the [parse] extra).
    try:
        if swc:
            from .parse.swc import parse_swc_yaml
            iface = parse_swc_yaml(Path(app_dir) / swc, name)
        elif mspec.get("parser") == "c":
            from .parse.cparse import available, parse_model_c
            if not available():
                sink.error("PARSER_UNAVAILABLE",
                           f"{where}: parser: c needs the [parse] extra "
                           "(uv sync --extra parse)", where)
                return None
            iface = parse_model_c(Path(app_dir) / codegen_dir, name)
        else:
            iface = parse_model(Path(app_dir) / codegen_dir, name)
    except FileNotFoundError as e:
        sink.error("MODEL_NOT_FOUND", str(e), where)
        return None
    except ValueError as e:
        sink.error("MODEL_BAD_SWC", str(e), where)
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
    # extra_runnables: additional runnables of this SWC scheduled at their own
    # rates (compute-only tasks; the primary runnable does the port I/O).
    extra = []
    for er in (mspec.get("extra_runnables") or []):
        er_fn, er_rate = er.get("runnable"), er.get("rate_ms")
        if not er_fn or er_rate is None:
            continue        # already reported by System._parse_models
        if er_fn not in iface.runnable_fns and er_fn != runnable_fn:
            sink.warning("MODEL_RUNNABLE_UNKNOWN",
                         f"{where}: extra runnable '{er_fn}' is not among the "
                         f"model's entry points {iface.runnable_fns}", where)
        extra.append((er_fn, int(er_rate)))
    return ResolvedModel(name, init_fn, runnable_fn, mspec.get("rate_ms"),
                         inputs, outputs, iface, extra)


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
            source = pd.get("source")
            # ASW<->ASW internal signal: an input reads another SWC's output
            # global (resolve_connections validates the target); an output can
            # be `driver: internal` (no hardware sink, just the exported global).
            if direction == "in" and source:
                if driver:
                    sink.error("PORT_SOURCE_AND_DRIVER",
                               f"{pw}: has both 'driver' and 'source'", pw)
                    continue
                bucket.append(BoundPort(sig, direction, "internal", {},
                                        _stem(signame), source=source))
                continue
            if driver == "internal":
                if direction != "out":
                    sink.error("PORT_INTERNAL_INPUT",
                               f"{pw}: 'driver: internal' is for outputs; an "
                               "internal input uses 'source'", pw)
                    continue
                bucket.append(BoundPort(sig, direction, "internal", {},
                                        _stem(signame)))
                continue
            if not driver:
                sink.error("PORT_NO_DRIVER",
                           f"{pw}: needs a 'driver' or 'source'", pw)
                continue
            params = {k: pd[k] for k in PORT_PARAM_KEYS if k in pd}
            slope, offset = _parse_scaling(pd, driver, pw, sink)
            spec_ = check_binding(sig, direction, driver, params, sink, pw,
                                  scaled=slope is not None)
            if spec_ is not None:
                bucket.append(BoundPort(sig, direction, driver, params,
                                        _stem(signame), slope, offset))
    return inputs, outputs


def resolve_connections(resolved, priorities, sink):
    """Wire ASW<->ASW internal signals across resolved SWCs. Each input port with
    a `source` ("<SWC>.<OUT_signal>") is matched to a producer output: the target
    must exist and its type should match, and the producer should be scheduled
    *before* the consumer (else the consumer reads last cycle's value). Sets
    port.source_signal (the producer's C global the RTE assigns from).
    `priorities` is {TASKNAME_UPPER: priority} (higher = runs first); {} skips the
    ordering check."""
    by_name = {rm.name: rm for rm in resolved}
    for rm in resolved:
        for port in rm.inputs:
            if not port.source:
                continue
            where = f"'{rm.name}' input '{port.signal.name}'"
            prod_name, _, out_sig = port.source.partition(".")
            prod = by_name.get(prod_name)
            if prod is None:
                sink.error("CONN_UNKNOWN_SWC",
                           f"{where}: source SWC '{prod_name}' is not a model or "
                           "ASW task", where)
                continue
            out_port = next((p for p in prod.outputs
                             if p.signal.name == out_sig), None)
            if out_port is None:
                sink.error("CONN_UNKNOWN_SIGNAL",
                           f"{where}: '{prod_name}' has no output '{out_sig}'",
                           where)
                continue
            if (port.signal.ctype and out_port.signal.ctype
                    and port.signal.ctype != out_port.signal.ctype):
                sink.warning("CONN_TYPE_MISMATCH",
                             f"{where}: producer type {out_port.signal.ctype} != "
                             f"consumer type {port.signal.ctype}", where)
            port.source_signal = out_port.signal.name
            pp = priorities.get(prod_name.upper())
            cp = priorities.get(rm.name.upper())
            if pp is not None and cp is not None and pp <= cp:
                sink.warning("CONN_ORDER",
                             f"{where}: producer '{prod_name}' is not scheduled "
                             "before the consumer (raise its priority), so the "
                             "consumer reads last cycle's value", where)


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
