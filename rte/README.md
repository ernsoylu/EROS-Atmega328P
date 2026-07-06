# RTE — model ↔ OS integration layer

The **Runtime Environment**: the single hand-written layer that connects
the application software (Simulink models) to the basic software (drivers
+ the EROS OS), AUTOSAR-style.

```
ASW   codegen/<model>_ert_rtw/     generated algorithm: ports + runnable
 │                                  (frozen — never edited)
RTE   rte/                          THIS layer — the only thing that changes
 │                                  port data flow · scheduling
BSW   drivers/ (MCAL) + kernel/     hardware drivers + EROS OS
                                    (frozen — never edited)
```

## Why this split works here

- **ASW** — the model is generated with `ExportToFile` storage, so its
  ports (`IN_KnbVal_Z`, `OUT_Led1_B`) and calibration params
  (`Knb_Thresh_Pc_Pt`, …) are plain extern globals, and its step is a
  named runnable (`appKnbSwt_Runnable`). Pure algorithm, zero hardware.
- **BSW** — `drivers/` are already "app-agnostic, kernel-independent,
  pure avr-libc + registers" (drivers/README) — textbook **MCAL**. The
  EROS kernel is the **OS**. Neither knows about any model.
- **RTE** — the only code that references both. It owns **port data flow**
  and **scheduling** (and **calibration** only for SWCs that *import* their
  parameters — see the table).

Everything the RTE needs is a *binding*, not logic — so the RTE is
declarative and (see below) generatable.

## RTE responsibilities

| # | Responsibility | Where |
|---|---|---|
| 1 | **Port data flow** — read BSW sensors into ASW input ports; write ASW output ports to BSW actuators (the IoHwAb adapters `Rte_Read_*` / `Rte_Write_*`) | `Rte.c` |
| 2 | **Scheduling** — bind the runnable's rate to an EROS task + cyclic alarm | `Rte_Start()` ← `Rte_Cfg.h` |
| — | **Calibration** — *only when the SWC imports its parameters* (`ImportFromFile`). `appKnbSwt` **exports** them (`ExportToFile`), so `appKnbSwt_Param.c` owns the values and the RTE does **not** assign them (see "Tuning"). | n/a here |

`Rte_Run_appKnbSwt()` is the OS task body: *read ports → run runnable →
write ports*. EROS calls it from a cyclic alarm in production; the simavr
test (`tests/firmware/test_model_knbswt.c`) calls it directly.

## Files

```
rte/
  Rte.h        public API: Rte_Init, Rte_Run_appKnbSwt, Rte_Start
  Rte.c        port adapters + lifecycle (the per-SWC template)
  Rte_Cfg.h    the declarative binding — pure config, no logic
  README.md    this file
```

Build (see `tests/Makefile`): compile `Rte.c` + the generated model +
the bound drivers, with `-Irte -Icodegen/<model>_ert_rtw`. It builds
warning-free under the project flags (`-Wall -Wextra -Werror -std=c99
-Os -flto`).

## The stable-interface rule

"Only RTE files change" holds **absolutely** for BSW and for the
generated C. The RTE tracks the ASW **port interface** — so if a
regeneration renames a port, param, or the runnable (as happened when
`appKnbSwt_step` became `appKnbSwt_Runnable`), the RTE absorbs it; that is
its job. Freeze the interface names (the ICD) and the RTE is stable too.
Nothing below the RTE ever moves.

## Generating the RTE (`erosgen.py`) — implemented

`Rte_Cfg.h` is pure configuration, so `tools/erosgen.py` **generates the whole
RTE** — `Rte.h`, `Rte_Cfg.h`, and the `Rte.c` adapters — plus the `config.*`
task/alarm entries, from an `app.yaml` `models:` section, exactly as it
generates the `Makefile` / `config.h` / `config.c`. Schema:

```yaml
models:
  - name: appKnbSwt
    codegen_dir: codegen/appKnbSwt_ert_rtw
    init:     appKnbSwt_initialize      # ASW entry points
    runnable: appKnbSwt_Runnable
    rate_ms:  10                        # -> EROS task + cyclic alarm
    ports:
      in:
        - signal: IN_KnbVal_Z           # ASW port  <- BSW driver
          driver: adc                    #   drivers/adc.c
          channel: 0                     #   A0
      out:
        - signal: OUT_Led1_B            # ASW port  -> BSW driver
          driver: dio                    #   GPIO
          port: B
          bit: 5
    # calibration: only for SWCs that IMPORT their parameters
    # (ImportFromFile). appKnbSwt exports them, so none here.
```

**Optional scaling (`slope` / `offset`).** A port carries the **raw** driver
value by default — scaling normally lives in Simulink (as `IN_KnbVal_Z` does).
A non-boolean port may instead opt into a generated linear calibration by
declaring `slope` (and an optional `offset`, default 0):

```yaml
      in:
        - signal: IN_Volt_mV
          driver: adc
          channel: 0
          slope: 4.887586       # port = raw*slope + offset (0..1023 -> 0..5000 mV)
```

`erosgen` emits `RTE_CFG_<PORT>_SLOPE` / `_OFFSET` #defines in `Rte_Cfg.h` and
calibrates in the port adapter — a `Rte_Read_*` returning the signal's C type
(`port = raw*slope + offset`), or, on an output, a `Rte_Write_*` converting the
port back to the driver value (`permille = port*slope + offset` for pwm). The
math is integer (`int32_t`) when both constants are ints, single-precision
`float` otherwise. Boolean (`dio`) ports are rejected (`SCALING_UNSUPPORTED`) —
a linear scale is meaningless on a 1-bit signal.

From this one block, `erosgen` generates `Rte.h` / `Rte_Cfg.h` / `Rte.c` (the
per-port `Rte_Read_*/Rte_Write_*` adapters, `Rte_Init`, and a `Task_<model>`
body that runs the runnable and `TerminateTask()`s), and wires the model as
`TASK_<MODEL>` / `ALARM_<MODEL>` in `config.*` with the RTE + model sources +
bound drivers added to the Makefile — so a model is integrated by editing
`app.yaml` and running `python3 tools/erosgen.py app.yaml`. `parse/ert.py`
identifies the exported signals, `bind.py` type-checks each port against its
driver, `emit/rte.py` emits the C. The model must be authored with the
ExportToFile/Define storage classes and `IN_`/`OUT_` port naming — see
[README_ASW.md](README_ASW.md) for that contract.

```sh
python3 tools/erosgen.py tools/fixtures/model_app/app.yaml   # a complete app
```

Multiple SWCs in one app share a single combined RTE — a `Task_<model>` per SWC
and per-model `RTE_CFG_<MODEL>_*` identity defines, with port stems required
unique across models (they share the `RTE_CFG_<TAG>_*` namespace). The
hand-written `rte/` here stays as the worked reference and the simavr test's
fixture. Its CI gate compiles a generated `model_app` firmware with
`avr-gcc -Werror`. Calibration assignment (`ImportFromFile` SWCs) is not
generated yet. The GUI (`gui/`) exposes this too: **Edit → Add Codegen Task**
adds the model, then its ports are bound **inline on the model's page** — to a
driver or to another SWC's output — with conflict-aware pin/channel pickers.

## Tuning (calibration)

`appKnbSwt` exports its parameters (`Knb_Thresh_Pc_Pt`, `Knb_Hyst_Pc_Pt`)
with Embedded Coder's `ExportToFile` storage, so `appKnbSwt_Param.c` is the
single source of truth for their values — the RTE does not touch them.
Retune either by changing them in the model and regenerating, or **live in
a debugger**: they compile to plain SRAM globals, so under simavr's GDB
stub you can `print Knb_Thresh_Pc_Pt` and `set var Knb_Thresh_Pc_Pt = 40`
(and `watch OUT_Led1_B`) with no rebuild. To make the RTE own calibration
instead, switch the model's parameter storage class to `ImportFromFile`
and add a `calibration:` block (above) — a model choice, not an RTE one.

## Verified under simavr

`tests/firmware/test_model_knbswt.c` drives `Rte_Run_appKnbSwt()` while
the host sweeps A0 across the full range (1023→0→1023 over 10 s) and
watches the DO pin — confirming the ASW→RTE→BSW chain end-to-end. See
`tests/README.md`.
