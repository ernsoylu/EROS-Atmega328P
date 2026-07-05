/**
 * @file    asw_signals.c
 * @brief   Cross-rate ASW signal storage and access functions.
 *
 * See asw_signals.h for the concurrency contract (non-preemptive kernel
 * -> plain copies; this module is where locking would attach if that
 * ever changes).
 */

#include <avr/pgmspace.h>

#include "eros.h"
#include "asw_signals.h"
#include "uart.h"
#include "pwm.h"

/* ------------------------------------------------------------------ */
/* Signal storage. ISR-touched fields (ErrorHook may run in the tick   */
/* ISR) are volatile; task-level-only fields are plain because tasks   */
/* cannot interleave on this kernel.                                   */
/* ------------------------------------------------------------------ */

static volatile StatusType aswLastError;
static volatile uint8_t    aswErrorCount;

static uint8_t aswRampRun = 1u;  /* 1 = breathing, 0 = frozen          */

/* ------------------------------------------------------------------ */
/* Accessors                                                           */
/* ------------------------------------------------------------------ */

uint8_t Asw_GetRampRun(void)
{
    return aswRampRun;
}

void Asw_SetRampRun(uint8_t run)
{
    aswRampRun = (run != 0u) ? 1u : 0u;
}

/** ISR-safe by construction: two single-byte stores, no OS calls. */
void Asw_RecordError(StatusType error)
{
    aswLastError = error;
    aswErrorCount++;
}

uint8_t Asw_GetErrorCount(void)
{
    return aswErrorCount;
}

StatusType Asw_GetLastError(void)
{
    return aswLastError;
}

/* ------------------------------------------------------------------ */
/* Status line                                                         */
/* ------------------------------------------------------------------ */

/** Status line, grouped under RES_UART so the multi-part write is one
 *  logical unit (conformance demo - see config.h note). */
void Asw_PrintStatus(void)
{
    (void)GetResource(RES_UART);
    UART_Print_P(PSTR("t="));
    UART_PrintU16(GetCounterValue());
    UART_Print_P(PSTR(" duty="));
    UART_PrintU16(PWM_GetDutyPermille());
    UART_Print_P(PSTR(" run="));
    (void)UART_PutChar((aswRampRun != 0u) ? '1' : '0');
    UART_Print_P(PSTR(" err="));
    UART_PrintU16((uint16_t)aswErrorCount);
    UART_Print_P(PSTR(" lastE="));
    UART_PrintHex8(aswLastError);
    UART_Print_P(PSTR(" txDrop="));
    UART_PrintU16((uint16_t)UART_TxDropped());
    UART_Print_P(PSTR("\r\n"));
    (void)ReleaseResource(RES_UART);
}
