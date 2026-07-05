/**
 * @file    main.c
 * @brief   Integration layer: hooks + init task + main().
 *
 * Generated once by tools/erosgen.py - edit freely. Pin setup
 * and alarm arming live in os_gen.h (regenerated every run).
 */

#include "eros.h"
#include "os_gen.h"

void StartupHook(void)
{
    Board_ConfigurePins(); /* generated: gpio + driver init */
}

void ErrorHook(StatusType error)
{
    (void)error; /* may run in tick-ISR context: stay tiny */
}

void ShutdownHook(StatusType error)
{
    (void)error; /* terminal fault tombstone */
}

/** TASK_INIT - autostart: arm the cyclic alarms. */
void Task_Init(void)
{
    OS_StartAlarms(); /* generated from the YAML task set */
    TerminateTask();
}

int main(void)
{
    StartOS(); /* noreturn */
}
