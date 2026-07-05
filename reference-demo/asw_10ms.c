/**
 * @file    asw_10ms.c
 * @brief   10 ms rate - TASK_FAST: scope channel PD2 + IPC consumer.
 *
 * Rate-local state only; the produced data arrives through kernel IPC
 * (pool + mailbox under RES_DEMO) - see asw_ipc.h for the payload
 * protocol and the concurrency rationale.
 */

#include <avr/io.h>
#include <avr/pgmspace.h>

#include "eros.h"
#include "actuator.h"
#include "asw_ipc.h"
#include "asw_10ms.h"

static const ActuatorType actFast PROGMEM = { &Actuator_OpsPortD,
                                              (1u << PD2) };

/* Rate-local statistics (this task is the only writer/reader). */
static uint8_t rxGood; /* verified messages received */
static uint8_t rxBad;  /* payload integrity failures */

/**
 * TASK_FAST - 10 ms period, consumer + highest priority. Scope channel
 * PD2 (50 Hz). Polls the mailbox under RES_DEMO, verifies the payload
 * and frees the block, completing the block's alloc->send->receive->free
 * life cycle.
 */
void Task_Fast(void)
{
    OsPoolHandleType handle = OS_POOL_INVALID_HANDLE;
    StatusType       status;

    Actuator_Trigger(&actFast);

    if (GetResource(RES_DEMO) == E_OK)
    {
        status = OS_MailboxReceive(&handle);
        (void)ReleaseResource(RES_DEMO);

        if (status == E_OK)
        {
            const uint8_t *const payload =
                (const uint8_t *)OS_PoolPtr(handle);
            const uint8_t expected =
                (payload != (const uint8_t *)0)
                    ? (uint8_t)~payload[ASW_MSG_SEQ] : 0u;

            if ((payload != (const uint8_t *)0) &&
                (payload[ASW_MSG_CHK] == expected))
            {
                rxGood++;
            }
            else
            {
                rxBad++;
            }
            (void)OS_PoolFree(handle);
        }
        /* E_OS_NOFUNC (empty) is the normal case 4 out of 5 polls:
         * the producer runs at a fifth of our rate. */
    }

    TerminateTask();
}
