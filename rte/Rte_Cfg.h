/**
 * @file    Rte_Cfg.h
 * @brief   RTE configuration for the model<->OS integration (appKnbSwt).
 *
 * This file is the *declarative binding* between the ASW (Simulink SWC)
 * ports/parameters and the BSW (drivers + EROS OS): which driver feeds
 * each input port, which driver each output port actuates, the runnable
 * rate, and the calibration values. It contains no logic - only the
 * configuration a generator needs.
 *
 * FUTURE: this file is intended to be emitted by tools/erosgen.py from an
 * app.yaml `models:` section (the same way the Makefile / config.c / .h
 * are generated today) - see rte/README.md "Generating the RTE". Until
 * then it is hand-written, but kept template-shaped so that step is a
 * fill-in, not a redesign. Everything below is a pure table.
 *
 * BSW files (drivers/, kernel/) and codegen files (ASW) never change;
 * only this RTE configuration does.
 */

#ifndef RTE_CFG_H
#define RTE_CFG_H

/* ---- SWC identity (ASW entry points, from the generated model) ------ */
#define RTE_CFG_INIT_FN            appKnbSwt_initialize
#define RTE_CFG_RUNNABLE_FN        appKnbSwt_Runnable

/* ---- Input port: KnbVal (uint16 0..1023) <- ADC (MCAL adc.c) -------- */
#define RTE_CFG_KNB_SIGNAL         IN_KnbVal_Z
#define RTE_CFG_KNB_ADC_CH         0u          /* A0 / PC0                */

/* ---- Output port: Led1 (boolean) -> DIO (GPIO on PORTB) ------------- */
#define RTE_CFG_LED_SIGNAL         OUT_Led1_B
#define RTE_CFG_LED_DDR            DDRB
#define RTE_CFG_LED_PORT           PORTB
#define RTE_CFG_LED_BIT            5u          /* PB5 / D13 (on-board LED) */

/* ---- Calibration parameters (RTE owns configuration) --------------- */
/* Assigned into the ASW's exported parameter globals at Rte_Init(). */
#define RTE_CAL_Knb_Thresh_Pc_Pt   20u         /* switch threshold [%]    */
#define RTE_CAL_Knb_Hyst_Pc_Pt     5u          /* hysteresis span [%]     */

/* ---- Scheduling: runnable rate assigned to the OS ------------------ */
#define RTE_CFG_PERIOD_MS          10u         /* 10 ms cyclic task       */

#endif /* RTE_CFG_H */
