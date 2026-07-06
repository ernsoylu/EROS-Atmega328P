/**
 * @file    asw_100ms.c
 * @brief   100 ms rate - TASK_RAMP: scope channel PD5 + triangle PWM ramp.
 *
 * The ramp state (duty, direction) is owned by this rate: other rates
 * never touch it directly, they call the exported Asw_RampReset()
 * service instead. The run/hold flag is a cross-rate signal and comes
 * in through the asw_signals accessor.
 */

#include <avr/io.h>
#include <avr/pgmspace.h>

#include "eros.h"
#include "actuator.h"
#include "asw_signals.h"
#include "asw_100ms.h"
#include "pwm.h"

#define RAMP_STEP_PERMILLE 50u /* 100 ms steps -> 4 s full breathe cycle */

/** Scope jitter channel: PD5 (Nano D5) toggles at 5 Hz. */
static const ActuatorType actScope PROGMEM = { &Actuator_OpsPortD,
                                               (1u << PD5) };

/* Rate-local state (owned by TASK_RAMP; foreign rates use
 * Asw_RampReset() only). */
static uint8_t  rampUp = 1u;
static uint16_t duty;

/** Exported service: duty 0, direction up, PWM forced low. */
void Asw_RampReset(void)
{
    duty   = 0u;
    rampUp = 1u;
    Pwm_SetDutyCycle(0u);
}

/**
 * TASK_RAMP - 100 ms. Triangle ramp 0..1000 permille -> 4 s breathing
 * cycle on the PWM LED (D9) while running.
 */
void Task_Ramp(void)
{
    Actuator_Trigger(&actScope);

    if (Asw_GetRampRun() != 0u)
    {
        if (rampUp != 0u)
        {
            duty += RAMP_STEP_PERMILLE;
            if (duty >= 1000u)
            {
                duty   = 1000u;
                rampUp = 0u;
            }
        }
        else
        {
            if (duty <= RAMP_STEP_PERMILLE)
            {
                duty   = 0u;
                rampUp = 1u;
            }
            else
            {
                duty -= RAMP_STEP_PERMILLE;
            }
        }
        Pwm_SetDutyCycle(duty);
    }
    TerminateTask();
}
