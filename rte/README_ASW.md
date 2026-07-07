# Authoring an ASW model for erosgen (the storage-class contract)

erosgen wires a Simulink SWC (application software, "ASW") to the EROS RTOS by
**parsing the Embedded Coder interface headers** — it never runs a C frontend on
the generated code. That only works if the model is generated with the custom
**storage classes** below, which keep the exported surface to a few regular
`extern` / `#define` lines. This file is the contract; `parse/ert.py` enforces
it and points failures here.

## What erosgen reads

For a model named `<model>`, erosgen reads exactly three files from the
`<model>_ert_rtw/` codegen directory:

| File | Contents erosgen parses | Storage class |
|---|---|---|
| `<model>_Intfc.h` | `extern <rtwtype> <IN_*\|OUT_*>[dim];` — the ports | **ExportToFile** on each root inport/outport |
| `<model>_Param.h` (optional) | `#define <NAME> <value>` and `extern <rtwtype> <NAME>;` — calibrations | **Define** (macro) or **ExportToFile** (tunable) |
| `<model>.h` | `extern void <model>_initialize(void);` and `extern void <model>_Runnable(void);` — entry points | default (ERT step/init functions) |

Everything else in `_ert_rtw/` (the `.c` sources, `rtwtypes.h`, `*_data.c`, …)
is compiled but **not parsed** for the interface.

## The two rules

**1. Ports use the `IN_` / `OUT_` prefix — it *is* the direction.**
A root-level signal named `IN_KnbVal_Z` is an input port; `OUT_Led1_B` is an
output. erosgen derives port direction purely from this prefix, and the
`app.yaml` `models:` block binds each `IN_`/`OUT_` signal to a driver
(`rte/README.md` has the schema). A signal without the prefix is ignored as a
port. The stem after the prefix (`KnbVal_Z`) must be **unique across all models
in one app** — the generated `RTE_CFG_<STEM>_*` macros share one namespace
(`PORT_STEM_COLLISION` is raised otherwise).

**2. Ports and tunable parameters use ExportToFile; constants use Define.**
ExportToFile emits `extern <type> <name>;` into the `_Intfc.h` / `_Param.h`
headers — the compilation contract erosgen keys on. Define emits
`#define <NAME> <value>`. Anything left in Simulink's default (`Auto`) storage
class is folded into the model's internal structs and is invisible to the
parser, so a port authored that way will not be found.

## Configuring it in Simulink

- Root inports/outports → **Code Mappings → Inports/Outports → Storage Class =
  `ExportToFile`**, and name them `IN_*` / `OUT_*`.
- Tunable calibrations → Storage Class `ExportToFile`; fixed constants →
  `Define`.
- Keep the model name and the `<model>_ert_rtw` directory name in sync (erosgen
  builds the header filenames from the model name).

## When the contract is violated

`parse/ert.py` raises loudly rather than silently generating a broken RTE:

- a missing `<model>_Intfc.h` or `<model>.h` → `FileNotFoundError` pointing here
  (the usual cause is a model exported without ExportToFile); `_Param.h` is
  **optional** — a model with no tunable parameters simply has no calibrations;
- a port bound in `app.yaml` that the model does not export →
  `PORT_UNKNOWN_SIGNAL`;
- a reused port stem across models → `PORT_STEM_COLLISION`.

## Multiple instances of one SWC

The same codegen SWC can be instantiated more than once — e.g. the same
controller at three rates driving three pins. Each `models:` entry gives a
distinct **instance** `name` (its OS task + RTE namespace) and points at the
shared code with **`model:`** (the ERT file prefix):

```yaml
models:
  - { name: tog10, model: appTaskRate, codegen_dir: .../appTaskRate_ert_rtw,
      rate_ms: 10, ports: { out: [{ signal: OUT_Toggler_B, driver: dio, port: D, bit: 1 }] } }
  - { name: tog20, model: appTaskRate, codegen_dir: .../appTaskRate_ert_rtw,
      rate_ms: 20, ports: { out: [{ signal: OUT_Toggler_B, driver: dio, port: D, bit: 2 }] } }
```

erosgen namespaces each instance's port `#define`s (`RTE_CFG_TOG10_*`), includes
the model once, and **context-switches each instance's state** around its
runnable — it saves/restores the SWC's exported globals in `Task_<instance>`, so
the instances run independently.

> **Scope:** this context-switch captures state that lives in the model's
> **exported I/O globals** (e.g. a `UnitDelay` feeding back through an output —
> the toggler). A model whose state lives in an internal `DWork` struct would
> still share it across instances; for that, generate the model with Embedded
> Coder's **Reusable function** interface (per-instance data), which is the
> fully-general path.

## Semantic metadata not in C (scaling, min/max)

C headers carry names, types, direction and array dimension — **not** scaling
(slope/offset) or units. If a port needs a linear calibration, declare it
explicitly in `app.yaml` (`slope` / `offset`, see `rte/README.md`); erosgen
generates the conversion in the RTE. The proprietary `codeInfo.mat` is **not**
consulted (its interface records are opaque). The C header stays authoritative.
