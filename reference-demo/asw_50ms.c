/**
 * @file    asw_50ms.c
 * @brief   50 ms rate - TASK_MED: scope channel PD3 + IPC producer.
 *
 * See asw_ipc.h for the payload protocol shared with the 10 ms
 * consumer and the concurrency rationale.
 */

#include <avr/io.h>
#include <avr/pgmspace.h>

#include "eros.h"
#include "actuator.h"
#include "asw_ipc.h"
#include "asw_50ms.h"

static const ActuatorType actMed PROGMEM = { &Actuator_OpsPortD,
                                             (1u << PD3) };

/* Rate-local state: producer payload sequence. */
static uint8_t txSequence;

/**
 * TASK_MED - 50 ms period, producer. Scope channel PD3 (10 Hz).
 * Allocates a pool block, fills it with a checkable payload and posts
 * the handle into the mailbox under the RES_DEMO IPCP resource.
 */
void Task_Med(void)
{
    OsPoolHandleType handle;

    Actuator_Trigger(&actMed);

    handle = OS_PoolAlloc();
    if (handle != OS_POOL_INVALID_HANDLE)
    {
        uint8_t *const payload = (uint8_t *)OS_PoolPtr(handle);

        txSequence++;
        payload[ASW_MSG_SEQ] = txSequence;
        payload[ASW_MSG_CHK] = (uint8_t)~txSequence;

        if (GetResource(RES_DEMO) == E_OK)
        {
            if (OS_MailboxSend(handle) != E_OK)
            {
                /* Mailbox still full (consumer behind): we keep block
                 * ownership, so return it - no leak, no fragmentation. */
                (void)OS_PoolFree(handle);
            }
            (void)ReleaseResource(RES_DEMO); /* LIFO order */
        }
        else
        {
            (void)OS_PoolFree(handle);
        }
    }
    /* Pool exhausted: drop this cycle's sample (bounded, deterministic). */

    TerminateTask();
}
