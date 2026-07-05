/**
 * @file    Rte.h
 * @brief   Runtime Environment (RTE) - the single integration layer
 *          connecting the ASW (Simulink model) to the BSW (drivers + OS).
 *
 * Layering (AUTOSAR-style):
 *
 *     ASW   codegen/appKnbSwt_ert_rtw/   pure algorithm, ports + runnable
 *      |                                  (generated - never edited)
 *     RTE   rte/                          THIS layer: port data flow,
 *      |                                  calibration, runnable->task
 *     BSW   drivers/ (MCAL) + kernel/     hardware + EROS OS
 *                                         (never edited)
 *
 * The RTE is the only place these three meet: it reads BSW sensors into
 * ASW input ports, runs the runnable, writes ASW output ports to BSW
 * actuators, assigns the ASW's calibration, and binds the runnable rate
 * to the OS scheduler. Binding details live in Rte_Cfg.h.
 */

#ifndef RTE_H
#define RTE_H

/**
 * Initialise the RTE: BSW resources for the bound ports (ADC, DIO
 * direction), assign ASW calibration parameters, then call the ASW
 * initialise entry. Call once with interrupts disabled (StartupHook).
 */
void Rte_Init(void);

/**
 * One activation of the appKnbSwt runnable: implicit-read the input
 * ports from the BSW, run the ASW runnable, implicit-write the output
 * ports to the BSW. This is the OS task body - EROS calls it from a
 * cyclic alarm in production; the simavr test calls it directly.
 */
void Rte_Run_appKnbSwt(void);

/**
 * Bind the runnable's rate to an EROS cyclic alarm (RTE_CFG_PERIOD_MS).
 * Compiled only in a full-OS build (RTE_WITH_EROS); the simavr unit test
 * drives Rte_Run_appKnbSwt() itself and does not need it.
 */
#ifdef RTE_WITH_EROS
void Rte_Start(void);
#endif

#endif /* RTE_H */
