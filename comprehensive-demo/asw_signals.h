/**
 * @file    asw_signals.h
 * @brief   Cross-rate ASW signals - the hand-written "rate transition"
 *          layer between the periodic tasks (asw_10ms/50ms/100ms/500ms).
 *
 * Structure mirrors Simulink / Embedded Coder multitasking output (see
 * ../codegen/README.md par.4): one C file per task rate, and every signal
 * that crosses a rate boundary goes through this module - never through
 * ad-hoc globals shared between task files.
 *
 * Concurrency contract - why there is NO mutex/semaphore here:
 *   EROS is non-preemptive run-to-completion, so tasks can never
 *   interleave and task<->task signal copies need no locking - the same
 *   reason Embedded Coder's Rate Transition double-buffers are never
 *   contended on this kernel (codegen/README.md par.4). The accessors
 *   below are therefore plain copies today. They exist so that mutual
 *   exclusion has exactly ONE place to attach (a GetResource pair or
 *   ATOMIC_BLOCK inside the accessor body) if the kernel ever becomes
 *   preemptive or an ISR becomes a writer.
 *
 *   Rule for ISR-shared data (kernel contract, kernel/eros.c): the
 *   object must be volatile, and any access wider than one byte must
 *   run under ATOMIC_BLOCK(ATOMIC_RESTORESTATE). Single bytes are
 *   naturally atomic on AVR.
 */

#ifndef ASW_SIGNALS_H
#define ASW_SIGNALS_H

#include "eros.h"

/* ------------------------------------------------------------------ */
/* IPC payload protocol: asw_10ms (producer) -> mailbox -> asw_50ms    */
/* ------------------------------------------------------------------ */
#define EVT_BUTTON_PRESS  0xB7u

/* ------------------------------------------------------------------ */
/* rampRun: 1 = PWM ramp breathing, 0 = frozen.                        */
/* Writer: Task_Cmd (50 ms). Readers: Task_Ramp (100 ms), status line. */
/* ------------------------------------------------------------------ */
uint8_t Asw_GetRampRun(void);
void    Asw_SetRampRun(uint8_t run);

/* ------------------------------------------------------------------ */
/* Error telemetry: written by ErrorHook (may run in tick-ISR          */
/* context!), read by the status line at task level. Both fields are   */
/* single bytes -> naturally atomic; stored volatile in asw_signals.c. */
/* Asw_RecordError() must stay ISR-safe: no OS calls, no printing.     */
/* ------------------------------------------------------------------ */
void       Asw_RecordError(StatusType error);
uint8_t    Asw_GetErrorCount(void);
StatusType Asw_GetLastError(void);

/* ------------------------------------------------------------------ */
/* Multi-part status line, grouped under RES_UART (one logical unit).  */
/* Task level only - never from hooks/ISRs (UART rings are SPSC).      */
/* Shared by Task_Status (500 ms) and Task_Cmd (STAT command).         */
/* ------------------------------------------------------------------ */
void Asw_PrintStatus(void);

#endif /* ASW_SIGNALS_H */
