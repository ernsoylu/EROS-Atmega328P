/**
 * @file    asw_500ms.c
 * @brief   500 ms rate - TASK_STATUS: heartbeat LED + status report.
 */

#include <avr/io.h>

#include "eros.h"
#include "asw_signals.h"
#include "asw_500ms.h"

/**
 * TASK_STATUS - 500 ms. Heartbeat (no delay loop: atomic PINx
 * toggle instead of a delay loop) plus the periodic status report.
 */
void Task_Status(void)
{
    PINB = (uint8_t)(1u << PB5); /* hardware toggle, single atomic store */
    Asw_PrintStatus();
    TerminateTask();
}
