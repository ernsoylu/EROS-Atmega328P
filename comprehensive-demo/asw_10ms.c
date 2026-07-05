/**
 * @file    asw_10ms.c
 * @brief   10 ms rate - TASK_BUTTON: debounced button input.
 *
 * All state in this file is rate-local (touched by this task only) and
 * therefore needs no protection on the non-preemptive kernel. The press
 * event leaves this rate through kernel IPC (pool block -> mailbox),
 * not through shared memory - see asw_signals.h for the contract.
 */

#include <avr/io.h>

#include "eros.h"
#include "asw_signals.h"
#include "asw_10ms.h"

/* Rate-local state: 8-sample debounce shift register.
 * Pull-up input: idle reads 1. */
static uint8_t btnHistory = 0xFFu;

/**
 * TASK_BUTTON - 10 ms. Debounce via 8-sample shift register: a press
 * event fires exactly once when the pin has read LOW for 7 consecutive
 * samples after having been HIGH (history == 0x80). The event is posted
 * to TASK_CMD as a pool block through the single-slot mailbox.
 */
void Task_Button(void)
{
    const uint8_t raw = ((PIND & (uint8_t)(1u << PD2)) != 0u) ? 1u : 0u;

    btnHistory = (uint8_t)((uint8_t)(btnHistory << 1) | raw);

    if (btnHistory == 0x80u) /* debounced falling edge = press */
    {
        const OsPoolHandleType h = OS_PoolAlloc();

        if (h != OS_POOL_INVALID_HANDLE)
        {
            uint8_t *const payload = (uint8_t *)OS_PoolPtr(h);

            payload[0] = EVT_BUTTON_PRESS;
            if (OS_MailboxSend(h) != E_OK)
            {
                (void)OS_PoolFree(h); /* mailbox full: drop the event */
            }
        }
    }
    TerminateTask();
}
