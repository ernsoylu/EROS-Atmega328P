# RTE — model ↔ OS integration layer

The **Runtime Environment**: the single hand-written layer that connects
the application software (Simulink models) to the basic software (drivers
+ the EROS OS), AUTOSAR-style.

```
ASW   codegen/<model>_ert_rtw/     generated algorithm: ports + runnable
 │                                  (frozen — never edited)
RTE   rte/                          THIS layer — the only thing that changes
 │                                  port data flow · calibration · scheduling
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
- **RTE** — the only code that references both. It owns the three
  responsibilities: **port data flow**, **calibration**, **scheduling**.

Everything the RTE needs is a *binding*, not logic — so the RTE is
declarative and (see below) generatable.

## The three RTE responsibilities

| # | Responsibility | Where |
|---|---|---|
| 1 | **Port data flow** — read BSW sensors into ASW input ports; write ASW output ports to BSW actuators (the IoHwAb adapters `Rte_Read_*` / `Rte_Write_*`) | `Rte.c` |
| 2 | **Calibration** — assign the ASW's exported parameter globals from a single config owner | `Rte_Init()` ← `Rte_Cfg.h` |
| 3 | **Scheduling** — bind the runnable's rate to an EROS task + cyclic alarm | `Rte_Start()` ← `Rte_Cfg.h` |

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

## Generating the RTE (future — `erosgen.py`)

`Rte_Cfg.h` is deliberately pure configuration so `tools/erosgen.py` can
emit it — and the `Rte.c` adapters and the `config.c` task/alarm entries
— from an `app.yaml` `models:` section, exactly as it already generates
the `Makefile` / `config.h` / `config.c` today. Proposed schema:

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
    calibration:                        # -> assigned in Rte_Init()
      Knb_Thresh_Pc_Pt: 20
      Knb_Hyst_Pc_Pt: 5
```

From this one block erosgen would generate: `Rte_Cfg.h` (the tables
above), the `Rte_Read_*/Rte_Write_*` adapter calls per port, and the
`TASK_APPKNBSWT` / `ALARM_APPKNBSWT` entries in `config.c` — so a model is
integrated by editing `app.yaml` and re-running the generator, with the
hand-written surface dropping to zero. The current hand-written RTE is
shaped as exactly that template so wiring it into erosgen is a fill-in,
not a redesign. (This generation step is not implemented yet — the RTE is
hand-written for now.)

## Verified under simavr

`tests/firmware/test_model_knbswt.c` drives `Rte_Run_appKnbSwt()` while
the host sweeps A0 across the full range (1023→0→1023 over 10 s) and
watches the DO pin — confirming the ASW→RTE→BSW chain end-to-end. See
`tests/README.md`.
