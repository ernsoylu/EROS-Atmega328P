/**
 * @file    asw_500ms.c
 * @brief   500 ms rate - TASK_SLOW: scope channel PD4 + ChainTask demo,
 *          and the chained TASK_REPORT heartbeat it releases.
 *
 * TASK_REPORT has no rate of its own (it is chained, effective period
 * 2 s), so it lives with the rate that owns its release.
 */

#include <avr/io.h>
#include <avr/pgmspace.h>

#include "eros.h"
#include "actuator.h"
#include "asw_500ms.h"

static const ActuatorType actSlow   PROGMEM = { &Actuator_OpsPortD,
                                                (1u << PD4) };
static const ActuatorType actReport PROGMEM = { &Actuator_OpsPortD,
                                                (1u << PD5) };

/* Rate-local state: ChainTask divider. */
static uint8_t slowRuns;

/**
 * TASK_SLOW - 500 ms period. Scope channel PD4 (1 Hz square wave).
 * Demonstrates ChainTask(): every 4th run chains TASK_REPORT.
 */
void Task_Slow(void)
{
    Actuator_Trigger(&actSlow);

    slowRuns++;
    if ((slowRuns & 0x03u) == 0u)
    {
        (void)ChainTask(TASK_REPORT); /* executed when we return */
    }
    TerminateTask();
}

/**
 * TASK_REPORT - heartbeat on PD5 (D5), toggled every 2 s. Activated once
 * at startup (double-activation demo) and afterwards chained by every
 * 4th TASK_SLOW run. PD5 is deliberately NOT the on-board LED: PB5 is
 * reserved for ErrorHook/ShutdownHook so the boot-time E_OS_LIMIT
 * demonstration stays visible instead of being toggled away microseconds
 * later by this task.
 */
void Task_Report(void)
{
    Actuator_Trigger(&actReport);
    TerminateTask();
}
