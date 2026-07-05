/**
 * @file    asw_10ms.c
 * @brief   10 ms rate - TASK_BUTTON: scope channel PD3 + debounced button.
 *
 * All state here is rate-local (this task is the only accessor) and so
 * needs no protection on the non-preemptive kernel. The debounced press
 * event leaves this rate through kernel IPC - a pool block posted into
 * the single-slot mailbox under the RES_DEMO (ISR-ceiling) resource -
 * never through shared memory. See asw_signals.h for the contract.
 */

#include <avr/io.h>
#include <avr/pgmspace.h>

#include "eros.h"
#include "actuator.h"
#include "asw_signals.h"
#include "asw_10ms.h"

/** Scope jitter channel: PD3 (Nano D3) toggles at 50 Hz. Dispatched
 *  through the polymorphic actuator (vtable + instance both in PROGMEM). */
static const ActuatorType actScope PROGMEM = { &Actuator_OpsPortD,
                                               (1u << PD3) };

/* Rate-local state: 8-sample debounce shift register.
 * Pull-up input: idle reads 1. */
static uint8_t btnHistory = 0xFFu;

/**
 * TASK_BUTTON - 10 ms, highest priority. Toggles scope channel PD3, then
 * debounces PD2 via an 8-sample shift register: a press fires exactly once
 * when the pin has read LOW for 7 consecutive samples after having been
 * HIGH (history == 0x80). The event is posted to TASK_CMD as a pool block
 * through the single-slot mailbox, the handoff guarded by RES_DEMO.
 */
void Task_Button(void)
{
    uint8_t raw;

    Actuator_Trigger(&actScope);

    raw = ((PIND & (uint8_t)(1u << PD2)) != 0u) ? 1u : 0u;
    btnHistory = (uint8_t)((uint8_t)(btnHistory << 1) | raw);

    if (btnHistory == 0x80u) /* debounced falling edge = press */
    {
        const OsPoolHandleType h = OS_PoolAlloc();

        if (h != OS_POOL_INVALID_HANDLE)
        {
            uint8_t *const payload = (uint8_t *)OS_PoolPtr(h);

            payload[0] = EVT_BUTTON_PRESS;

            if (GetResource(RES_DEMO) == E_OK)
            {
                if (OS_MailboxSend(h) != E_OK)
                {
                    (void)OS_PoolFree(h); /* mailbox full: drop the event */
                }
                (void)ReleaseResource(RES_DEMO); /* LIFO order */
            }
            else
            {
                (void)OS_PoolFree(h);
            }
        }
    }
    TerminateTask();
}
