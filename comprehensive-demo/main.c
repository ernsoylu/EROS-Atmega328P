/**
 * @file    main.c
 * @brief   Comprehensive demo - GPIO, serial console, PWM and IPC
 *          integrated on the EROS OSEK BCC1 kernel (../kernel).
 *
 * Integration layer only: hooks, the one-shot startup task and main().
 * The application software (ASW) lives in one C/H pair per task rate -
 * the same structure recommended for Simulink / Embedded Coder
 * multitasking output in ../codegen/README.md:
 *
 *   asw_10ms.c    TASK_BUTTON  debounced push button (PD2/D2)
 *   asw_50ms.c    TASK_CMD     serial command parser (ON/OFF/STAT)
 *   asw_100ms.c   TASK_RAMP    Timer1 PWM breathing LED (PB1/D9)
 *   asw_500ms.c   TASK_STATUS  heartbeat LED (PB5/D13) + status line
 *   asw_signals.c cross-rate signals ("rate transition" layer) +
 *                 shared status print - see asw_signals.h for the
 *                 concurrency contract (why no mutexes are needed on
 *                 this non-preemptive kernel, and where they would
 *                 attach if that ever changes)
 *
 * Hardware (Arduino Nano):
 *   PB5 / D13 : heartbeat LED, toggled every 500 ms
 *   PD2 / D2  : push button to GND, internal pull-up
 *   PD0/PD1   : UART 9600 8N1 serial monitor
 *   PB1 / D9  : PWM "breathing" LED, Timer1 @ 1 kHz
 *   Scheduling: EROS alarms - 10/50/100/500 ms
 *
 * Serial commands (type in any serial monitor, 9600 baud, CR or LF ends
 * a line): ON    resume the PWM ramp
 *          OFF   stop the ramp and force duty to 0
 *          STAT  print a status line immediately
 * Pressing the button toggles the ramp as well (event travels through a
 * pool block + the single-slot mailbox to TASK_CMD).
 *
 * Note the LED toggle idiom: writing a 1 to a PINx bit toggles the pin
 * in hardware (single atomic store). The tempting
 * 'PIND |= (1 << PDx)' is a read-modify-write that toggles EVERY pin of
 * the port that currently reads high, and only appears to work while a
 * single pin is involved. 'PINB = (1 << PB5)' is the correct form.
 */

#include <avr/io.h>
#include <avr/pgmspace.h>

#include "eros.h"
#include "asw_signals.h"
#include "uart.h"
#include "pwm.h"

/* ------------------------------------------------------------------ */
/* Hooks                                                               */
/* ------------------------------------------------------------------ */

/** Board bring-up; StartOS() calls this with interrupts still disabled. */
void StartupHook(void)
{
    DDRB  |= (uint8_t)(1u << PB5);   /* heartbeat LED                   */
    DDRD  &= (uint8_t)~(1u << PD2);  /* button input...                 */
    PORTD |= (uint8_t)(1u << PD2);   /* ...with internal pull-up        */
    UART_Init();
    PWM_Init();
}

/** May run in tick-ISR context (e.g. deadline miss). It must therefore
 *  stay tiny and MUST NOT print: the UART driver's rings are strictly
 *  single-producer and the producer side belongs to task level.
 *  Asw_RecordError() is ISR-safe by construction (two byte stores). */
void ErrorHook(StatusType error)
{
    Asw_RecordError(error);
}

/** Terminal failure tombstone: heartbeat LED solid ON. */
void ShutdownHook(StatusType error)
{
    (void)error;
    DDRB  |= (uint8_t)(1u << PB5);
    PORTB |= (uint8_t)(1u << PB5);
}

/* ------------------------------------------------------------------ */
/* Startup task                                                        */
/* ------------------------------------------------------------------ */

/**
 * TASK_STARTUP - autostart, runs once: banner, reset cause, arm alarms.
 */
void Task_Startup(void)
{
    UART_Print_P(PSTR("\r\nEROS comprehensive demo\r\n"));
    UART_Print_P(PSTR("reset cause MCUSR=0x"));
    /* WDRF/BORF/EXTRF/PORF. Caveat: meaningful on old-bootloader
     * (ATmegaBOOT) boards and bare chips; Optiboot clears MCUSR before
     * jumping to the application, so those boards always report 0x00
     * (Optiboot stashes the value in r2, which is not standardised). */
    UART_PrintHex8(os_resetCause);
    UART_Print_P(PSTR("  commands: ON | OFF | STAT\r\n"));

    (void)SetRelAlarm(ALARM_BUTTON, 10u,  10u);
    (void)SetRelAlarm(ALARM_CMD,    50u,  50u);
    (void)SetRelAlarm(ALARM_RAMP,   100u, 100u);
    (void)SetRelAlarm(ALARM_STATUS, 500u, 500u);

    TerminateTask();
}

/* ------------------------------------------------------------------ */
/* Entry point                                                         */
/* ------------------------------------------------------------------ */
int main(void)
{
    StartOS(); /* noreturn */
}
