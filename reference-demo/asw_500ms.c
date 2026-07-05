/**
 * @file    asw_500ms.c
 * @brief   500 ms rate - TASK_STATUS: scope channel PD6 + status line and
 *          the ChainTask heartbeat it releases, plus TASK_REPORT itself.
 *
 * TASK_REPORT has no rate of its own (it is chained, effective period
 * 2 s), so it lives with the rate that owns its release. It drives the
 * on-board LED heartbeat; the deliberate boot-time E_OS_LIMIT is shown in
 * the status line (err=/lastE=), not on the LED.
 */

#include <avr/io.h>
#include <avr/pgmspace.h>

#include "eros.h"
#include "actuator.h"
#include "asw_signals.h"
#include "asw_500ms.h"

/** Scope jitter channel PD6 (Nano D6, 1 Hz) and the on-board heartbeat
 *  LED PB5 (D13) - two "classes" of the polymorphic actuator (PortD vs
 *  PortB vtable), both resolved from PROGMEM. */
static const ActuatorType actScope     PROGMEM = { &Actuator_OpsPortD,
                                                   (1u << PD6) };
static const ActuatorType actHeartbeat PROGMEM = { &Actuator_OpsPortB,
                                                   (1u << PB5) };

/* Rate-local state: ChainTask divider. */
static uint8_t statusRuns;

/**
 * TASK_STATUS - 500 ms. Toggles scope channel PD6 (1 Hz), prints the
 * periodic status line, and every 4th run chains TASK_REPORT (the 2 s
 * heartbeat). Asw_PrintStatus() takes and releases RES_UART internally,
 * so no resource is held across the ChainTask.
 */
void Task_Status(void)
{
    Actuator_Trigger(&actScope);
    Asw_PrintStatus();

    statusRuns++;
    if ((statusRuns & 0x03u) == 0u)
    {
        (void)ChainTask(TASK_REPORT); /* executed when we return */
    }
    TerminateTask();
}

/**
 * TASK_REPORT - heartbeat on the on-board LED PB5 (D13), toggled every
 * 2 s. Activated once at startup (the deliberate double-activation demo)
 * and afterwards chained by every 4th TASK_STATUS run. The toggle is a
 * single atomic PINx store via the polymorphic actuator, safe from any
 * context. PB5 is the heartbeat only: the boot E_OS_LIMIT is reported in
 * the serial status line instead of lighting the LED.
 */
void Task_Report(void)
{
    Actuator_Trigger(&actHeartbeat);
    TerminateTask();
}
