"""Hand-authored ASW tasks: the interface a plain task can declare in app.yaml.

A task with a `ports:` / `calibrations:` section is a *hand-written SWC*. Instead
of parsing an Embedded Coder codegen dir, erosgen synthesizes the same
ModelInterface from the YAML, binds its ports through the shared resolver
(models.bind_ports), and emits editable <name>{,_Intfc,_Param} skeletons. From
resolve_models' point of view - and the RTE's - a hand ASW task is just another
ResolvedModel, so the whole binding + RTE path is reused unchanged.
"""
from .models import ResolvedModel, bind_ports
from .parse.ert import Calibration, ModelInterface, Signal


def is_asw_task(t):
    """True for a task that declares an interface (ports/calibrations) - i.e. a
    hand-authored SWC, not a plain periodic thread (which gets an asw_<n>ms.c)."""
    return isinstance(t, dict) and ("ports" in t or "calibrations" in t)


def iface_from_task(task):
    """The ModelInterface a hand ASW task exports, built straight from its YAML
    (no C parsing). A port's `type` is its rtw type (uint16_T, boolean_T, ...);
    an untyped port is left "" and bind.py flags it. `description` rides along so
    the skeleton emitter can turn it into a C comment."""
    name = task.get("name", "?")
    sigs = []
    for direction in ("in", "out"):
        for pd in (task.get("ports") or {}).get(direction, []) or []:
            if isinstance(pd, dict) and pd.get("signal"):
                sigs.append(Signal(pd["signal"], pd.get("type", ""), direction,
                                   description=pd.get("description", "")))
    cals = []
    for cd in task.get("calibrations") or []:
        if isinstance(cd, dict) and cd.get("name"):
            cals.append(Calibration(cd["name"], cd.get("type", ""), "extern",
                                    str(cd.get("value", "0")),
                                    description=cd.get("description", "")))
    runnable = task.get("runnable") or f"{name}_Runnable"
    init = task.get("init") or f"{name}_initialize"
    return ModelInterface(name, init, (runnable,), tuple(sigs), tuple(cals))


def resolve_asw_task(task, sink):
    """Resolve a hand ASW task's port bindings into a ResolvedModel (rate = the
    task's period_ms). Reuses the model binding rules verbatim."""
    name = task.get("name", "?")
    iface = iface_from_task(task)
    inputs, outputs = bind_ports(iface, task, f"task '{name}'", sink)
    return ResolvedModel(name, iface.init_fn, iface.runnable_fns[0],
                         task.get("period_ms"), inputs, outputs, iface)


def resolve_asw_tasks(doc, sink):
    """Every hand ASW task in the doc, as ResolvedModels (empty list if none)."""
    return [resolve_asw_task(t, sink) for t in (doc.get("tasks") or [])
            if is_asw_task(t)]
