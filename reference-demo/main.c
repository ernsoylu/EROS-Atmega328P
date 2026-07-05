/**
 * @file    main.c
 * @brief   EROS reference demo (Arduino Nano / ATmega328P) - a full
 *          application on the EROS OSEK BCC1 kernel (../kernel): GPIO
 *          scope channels, an interrupt-driven serial console, a Timer1
 *          PWM breathing LED, pool/mailbox IPC, ChainTask and a
 *          deliberate error demonstration.
 *
 * Integration layer only: hooks, the one-shot startup task and main().
 * The application software (ASW) lives in one C/H pair per task rate -
 * the structure recommended for Simulink / Embedded Coder multitasking
 * output in ../codegen/README.md par.4:
 *
 *   asw_10ms.c    TASK_BUTTON  scope PD3 + debounced button (PD2/D2),
 *                              IPC producer (pool -> mailbox, RES_DEMO)
 *   asw_50ms.c    TASK_CMD     scope PD4 + serial parser (ON/OFF/STAT)
 *                              + button-event consumer (RES_DEMO)
 *   asw_100ms.c   TASK_RAMP    scope PD5 + Timer1 PWM breathing LED (PB1/D9)
 *   asw_500ms.c   TASK_STATUS  scope PD6 + status line, and the chained
 *                              TASK_REPORT 2 s heartbeat (PB5/D13)
 *   asw_signals.c cross-rate signals ("rate transition" layer) + the
 *                 shared status print - see asw_signals.h for the
 *                 concurrency contract (why no mutexes are needed on this
 *                 non-preemptive kernel, and where they would attach)
 *   actuator.c    polymorphic GPIO driver (OOP-in-C, vtables + instances
 *                 in PROGMEM, deviation D4): every scope + heartbeat
 *                 toggle dispatches through it
 *
 * Hardware (Arduino Nano):
 *   PB5 / D13 : on-board LED, 2 s heartbeat (TASK_REPORT via ChainTask)
 *   PB1 / D9  : PWM "breathing" LED, Timer1 @ 1 kHz
 *   PD2 / D2  : push button to GND, internal pull-up
 *   PD3..PD6  : scope jitter channels (50 / 10 / 5 / 1 Hz square waves)
 *   PD0/PD1   : UART 9600 8N1 serial monitor
 *   Scheduling: EROS alarms - 10 / 50 / 100 / 500 ms
 *
 * Serial commands (9600 baud, CR or LF ends a line): ON  resume the ramp,
 * OFF  stop it and force duty 0, STAT  print a status line now. Pressing
 * the button toggles the ramp as well - the event travels through a pool
 * block + the single-slot mailbox (under RES_DEMO) to TASK_CMD.
 *
 * Note the LED toggle idiom: writing a 1 to a PINx bit toggles the pin in
 * hardware (single atomic store, done here through actuator.c). The
 * tempting 'PIND |= (1 << PDx)' is a read-modify-write that toggles EVERY
 * pin of the port currently reading high; 'PINB = (1 << PB5)' is correct.
 */

#include <avr/io.h>
#include <avr/pgmspace.h>

#include "eros.h"
#include "asw_signals.h"
#include "asw_500ms.h"
#include "uart.h"
#include "pwm.h"

/* ------------------------------------------------------------------ */
/* Hooks                                                               */
/* ------------------------------------------------------------------ */

/** Board bring-up; StartOS() calls this with interrupts still disabled. */
void StartupHook(void)
{
    DDRB  |= (uint8_t)(1u << PB5);                 /* heartbeat LED       */
    DDRD  |= (uint8_t)((1u << PD3) | (1u << PD4) | /* scope channels...   */
                       (1u << PD5) | (1u << PD6));
    DDRD  &= (uint8_t)~(1u << PD2);                /* button input...     */
    PORTD |= (uint8_t)(1u << PD2);                 /* ...internal pull-up */
    UART_Init();
    PWM_Init();
}

/** May run in tick-ISR context (e.g. a deadline miss). It must therefore
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
 * TASK_STARTUP - autostart, runs once: banner, reset cause, arm the four
 * periodic alarms, and perform the deliberate BCC1 activation-limit demo.
 */
void Task_Startup(void)
{
    UART_Print_P(PSTR("\r\nEROS reference demo\r\n"));
    UART_Print_P(PSTR("reset cause MCUSR=0x"));
    /* WDRF/BORF/EXTRF/PORF. Caveat: meaningful on old-bootloader
     * (ATmegaBOOT) boards and bare chips; Optiboot clears MCUSR before
     * jumping to the application, so those boards always report 0x00
     * (Optiboot stashes the value in r2, which is not standardised). */
    UART_PrintHex8(os_resetCause);
    UART_Print_P(PSTR("  commands: ON | OFF | STAT\r\n"));

    /* Relative alarms fire <increment> ticks from now, then cyclically.
     * The SetAbsAlarm variant is shown for the 500 ms channel: the
     * counter is only a few ticks old here, so 500 is still in the future
     * and it fires at absolute tick 500, then every 500 ticks. */
    (void)SetRelAlarm(ALARM_BUTTON, 10u,  10u);
    (void)SetRelAlarm(ALARM_CMD,    50u,  50u);
    (void)SetRelAlarm(ALARM_RAMP,   100u, 100u);
    (void)SetAbsAlarm(ALARM_STATUS, 500u, 500u);

    /* Deliberate BCC1 activation-limit violation: the first activation is
     * legal (TASK_REPORT is SUSPENDED), the second finds it READY - on
     * this non-preemptive kernel TASK_REPORT cannot run until we return,
     * so it is still READY -> E_OS_LIMIT -> ErrorHook records it. It
     * surfaces in the first status line as 'err=1 lastE=..' (PB5 is the
     * heartbeat, not an error lamp). */
    (void)ActivateTask(TASK_REPORT);   /* E_OK                        */
    (void)ActivateTask(TASK_REPORT);   /* E_OS_LIMIT (intentional)    */

    TerminateTask();
}

/* ------------------------------------------------------------------ */
/* Entry point                                                         */
/* ------------------------------------------------------------------ */
int main(void)
{
    StartOS(); /* noreturn: scheduler loop runs forever */
}
