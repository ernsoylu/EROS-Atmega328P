/**
 * @file    asw_50ms.c
 * @brief   50 ms rate - TASK_CMD: serial command parser + button events.
 *
 * Cross-rate writes go through the asw_signals accessors (rampRun) and
 * the exported ramp service Asw_RampReset() owned by asw_100ms.c; the
 * line buffer below is rate-local. See asw_signals.h for the
 * concurrency contract.
 */

#include <avr/pgmspace.h>
#include <string.h>

#include "eros.h"
#include "asw_signals.h"
#include "asw_50ms.h"
#include "asw_100ms.h"
#include "uart.h"

/* Rate-local state: command line buffer. */
static char    cmdBuf[12];
static uint8_t cmdLen;

/**
 * TASK_CMD - 50 ms. Two input sources:
 *   1. button events arriving via mailbox (from TASK_BUTTON),
 *   2. serial characters from the RX ring (from the Category-1 RX ISR),
 *      echoed and line-buffered, then parsed: ON / OFF / STAT.
 * Character intake is capped per activation to bound the WCET; the cap
 * equals the RX ring size, which itself exceeds the worst-case number
 * of bytes 9600 baud can deliver per period (48), so pasted input is
 * never dropped.
 */
void Task_Cmd(void)
{
    OsPoolHandleType h;
    uint8_t          budget;
    char             c;

    /* --- 1. button events ----------------------------------------- */
    if (OS_MailboxReceive(&h) == E_OK)
    {
        const uint8_t *const payload = (const uint8_t *)OS_PoolPtr(h);

        if ((payload != (const uint8_t *)0) &&
            (payload[0] == EVT_BUTTON_PRESS))
        {
            Asw_SetRampRun((Asw_GetRampRun() != 0u) ? 0u : 1u);
            UART_Print_P((Asw_GetRampRun() != 0u) ? PSTR("BTN -> RUN\r\n")
                                                  : PSTR("BTN -> HOLD\r\n"));
        }
        (void)OS_PoolFree(h);
    }

    /* --- 2. serial command intake. The cap bounds the WCET; 64 (the
     * RX ring size) is the natural upper limit and outruns the wire:
     * 9600 baud delivers at most 48 bytes per 50 ms period. ---------- */
    for (budget = 64u; (budget != 0u) && (UART_GetChar(&c) != 0u); budget--)
    {
        if ((c == '\r') || (c == '\n'))
        {
            if (cmdLen != 0u) /* ignore bare line endings */
            {
                cmdBuf[cmdLen] = '\0';
                cmdLen = 0u;

                UART_Print_P(PSTR("\r\n"));
                if (strcmp_P(cmdBuf, PSTR("ON")) == 0)
                {
                    Asw_SetRampRun(1u);
                    UART_Print_P(PSTR("ramp ON\r\n"));
                }
                else if (strcmp_P(cmdBuf, PSTR("OFF")) == 0)
                {
                    Asw_SetRampRun(0u);
                    Asw_RampReset(); /* duty 0, ramp direction up */
                    UART_Print_P(PSTR("ramp OFF\r\n"));
                }
                else if (strcmp_P(cmdBuf, PSTR("STAT")) == 0)
                {
                    Asw_PrintStatus();
                }
                else
                {
                    UART_Print_P(PSTR("unknown command\r\n"));
                }
            }
        }
        else if ((c >= ' ') && (c <= '~')) /* printable ASCII only */
        {
            (void)UART_PutChar(c); /* echo */
            if (cmdLen < (uint8_t)(sizeof(cmdBuf) - 1u))
            {
                cmdBuf[cmdLen] = c;
                cmdLen++;
            }
        }
        else
        {
            /* control characters other than CR/LF are ignored */
        }
    }
    TerminateTask();
}
