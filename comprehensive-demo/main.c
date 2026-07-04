/**
 * @file    main.c
 * @brief   Comprehensive demo - GPIO, serial console, PWM and IPC
 *          integrated on the TinyOS OSEK BCC1 kernel (../kernel).
 *
 * Hardware (Arduino Nano):
 *   PB5 / D13 : heartbeat LED, toggled every 500 ms
 *   PD2 / D2  : push button to GND, internal pull-up
 *   PD0/PD1   : UART 9600 8N1 serial monitor
 *   PB1 / D9  : PWM "breathing" LED, Timer1 @ 1 kHz
 *   Scheduling: TinyOS alarms - 10/50/100/500 ms
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
#include <string.h>

#include "tiny_os.h"
#include "uart.h"
#include "pwm.h"

/* ------------------------------------------------------------------ */
/* Application state (all task-level; no ISR access except appLast/    */
/* appErrorCount, which ErrorHook may touch from the tick ISR)         */
/* ------------------------------------------------------------------ */

#define EVT_BUTTON_PRESS  0xB7u

#define RAMP_STEP_PERMILLE 50u /* 100 ms steps -> 4 s full breathe cycle */

static volatile StatusType appLastError;
static volatile uint8_t    appErrorCount;

static uint8_t  appRampRun = 1u;  /* 1 = breathing, 0 = frozen          */
static uint8_t  appRampUp  = 1u;
static uint16_t appDuty;

static uint8_t  appBtnHistory = 0xFFu; /* pull-up: idle reads 1         */

static char     appCmdBuf[12];
static uint8_t  appCmdLen;

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
 *  single-producer and the producer side belongs to task level. */
void ErrorHook(StatusType error)
{
    appLastError = error;
    appErrorCount++;
}

/** Terminal failure tombstone: heartbeat LED solid ON. */
void ShutdownHook(StatusType error)
{
    (void)error;
    DDRB  |= (uint8_t)(1u << PB5);
    PORTB |= (uint8_t)(1u << PB5);
}

/* ------------------------------------------------------------------ */
/* Shared print helper (task level only)                               */
/* ------------------------------------------------------------------ */

/** Status line, grouped under RES_UART so the multi-part write is one
 *  logical unit (conformance demo - see config.h note). */
static void PrintStatus(void)
{
    (void)GetResource(RES_UART);
    UART_Print_P(PSTR("t="));
    UART_PrintU16(GetCounterValue());
    UART_Print_P(PSTR(" duty="));
    UART_PrintU16(PWM_GetDutyPermille());
    UART_Print_P(PSTR(" run="));
    (void)UART_PutChar((appRampRun != 0u) ? '1' : '0');
    UART_Print_P(PSTR(" err="));
    UART_PrintU16((uint16_t)appErrorCount);
    UART_Print_P(PSTR(" lastE="));
    UART_PrintHex8(appLastError);
    UART_Print_P(PSTR(" txDrop="));
    UART_PrintU16((uint16_t)UART_TxDropped());
    UART_Print_P(PSTR("\r\n"));
    (void)ReleaseResource(RES_UART);
}

/* ------------------------------------------------------------------ */
/* Tasks                                                               */
/* ------------------------------------------------------------------ */

/**
 * TASK_STARTUP - autostart, runs once: banner, reset cause, arm alarms.
 */
void Task_Startup(void)
{
    UART_Print_P(PSTR("\r\nTinyOS comprehensive demo\r\n"));
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

/**
 * TASK_BUTTON - 10 ms. Debounce via 8-sample shift register: a press
 * event fires exactly once when the pin has read LOW for 7 consecutive
 * samples after having been HIGH (history == 0x80). The event is posted
 * to TASK_CMD as a pool block through the single-slot mailbox.
 */
void Task_Button(void)
{
    const uint8_t raw = ((PIND & (uint8_t)(1u << PD2)) != 0u) ? 1u : 0u;

    appBtnHistory = (uint8_t)((uint8_t)(appBtnHistory << 1) | raw);

    if (appBtnHistory == 0x80u) /* debounced falling edge = press */
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
            appRampRun = (appRampRun != 0u) ? 0u : 1u;
            UART_Print_P((appRampRun != 0u) ? PSTR("BTN -> RUN\r\n")
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
            if (appCmdLen != 0u) /* ignore bare line endings */
            {
                appCmdBuf[appCmdLen] = '\0';
                appCmdLen = 0u;

                UART_Print_P(PSTR("\r\n"));
                if (strcmp_P(appCmdBuf, PSTR("ON")) == 0)
                {
                    appRampRun = 1u;
                    UART_Print_P(PSTR("ramp ON\r\n"));
                }
                else if (strcmp_P(appCmdBuf, PSTR("OFF")) == 0)
                {
                    appRampRun = 0u;
                    appDuty    = 0u;
                    appRampUp  = 1u;
                    PWM_SetDutyPermille(0u);
                    UART_Print_P(PSTR("ramp OFF\r\n"));
                }
                else if (strcmp_P(appCmdBuf, PSTR("STAT")) == 0)
                {
                    PrintStatus();
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
            if (appCmdLen < (uint8_t)(sizeof(appCmdBuf) - 1u))
            {
                appCmdBuf[appCmdLen] = c;
                appCmdLen++;
            }
        }
        else
        {
            /* control characters other than CR/LF are ignored */
        }
    }
    TerminateTask();
}

/**
 * TASK_RAMP - 100 ms. Triangle ramp 0..1000 permille -> 4 s breathing
 * cycle on the PWM LED (D9) while running.
 */
void Task_Ramp(void)
{
    if (appRampRun != 0u)
    {
        if (appRampUp != 0u)
        {
            appDuty += RAMP_STEP_PERMILLE;
            if (appDuty >= 1000u)
            {
                appDuty   = 1000u;
                appRampUp = 0u;
            }
        }
        else
        {
            if (appDuty <= RAMP_STEP_PERMILLE)
            {
                appDuty   = 0u;
                appRampUp = 1u;
            }
            else
            {
                appDuty -= RAMP_STEP_PERMILLE;
            }
        }
        PWM_SetDutyPermille(appDuty);
    }
    TerminateTask();
}

/**
 * TASK_STATUS - 500 ms. Heartbeat (no delay loop: atomic PINx
 * toggle instead of a delay loop) plus the periodic status report.
 */
void Task_Status(void)
{
    PINB = (uint8_t)(1u << PB5); /* hardware toggle, single atomic store */
    PrintStatus();
    TerminateTask();
}

/* ------------------------------------------------------------------ */
/* Entry point                                                         */
/* ------------------------------------------------------------------ */
int main(void)
{
    StartOS(); /* noreturn */
}
