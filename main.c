/**
 * @file    main.c
 * @brief   EROS demonstration application (Arduino Nano / ATmega328P).
 *
 * Integration layer only: hooks, the one-shot init task and main().
 * The application software (ASW) lives in one C/H pair per task rate -
 * the same structure recommended for Simulink / Embedded Coder
 * multitasking output in codegen/README.md:
 *
 *   asw_10ms.c   TASK_FAST   scope channel PD2 + mailbox consumer
 *   asw_50ms.c   TASK_MED    scope channel PD3 + pool/mailbox producer
 *   asw_500ms.c  TASK_SLOW   scope channel PD4 + ChainTask demo, and
 *                TASK_REPORT the chained 2 s heartbeat (PD5)
 *   asw_ipc.h    the producer->consumer payload protocol + the
 *                concurrency rationale (why no mutexes are needed on
 *                this non-preemptive kernel)
 *   actuator.c/h polymorphic GPIO driver - OOP-in-C, instances and
 *                vtables 100% in Flash (PROGMEM, deviation D4)
 *
 * Demonstrated kernel features:
 *
 *  1. Three periodic alarms toggling distinct GPIOs so release jitter can
 *     be measured with a scope (each toggle is a single atomic PINx write):
 *        ALARM_FAST : 10 ms  -> TASK_FAST -> PD2 (Nano pin D2, 50 Hz sq.)
 *        ALARM_MED  : 50 ms  -> TASK_MED  -> PD3 (Nano pin D3, 10 Hz sq.)
 *        ALARM_SLOW : 500 ms -> TASK_SLOW -> PD4 (Nano pin D4,  1 Hz sq.)
 *     Expected jitter: <= 1 tick activation error (alarm fires inside the
 *     tick ISR) + queueing delay bounded by the largest task WCET (<= 1 ms
 *     steady state - see the budget table in config.h).
 *
 *  2. A deliberate double activation in Task_Init: the second
 *     ActivateTask(TASK_REPORT) hits the BCC1 activation limit, returns
 *     E_OS_LIMIT and raises ErrorHook, which toggles the on-board LED
 *     (PB5) ON. PB5 is used by the hooks only, so the LED stays lit as
 *     a visible marker of the demonstrated error until the next error
 *     event (the heartbeat runs on PD5 instead).
 *
 *  3. Producer/consumer IPC: TASK_MED allocates a fixed-size pool block,
 *     fills it, and posts its handle into the single-slot mailbox; the
 *     handoff is guarded by the RES_DEMO IPCP resource (ISR-ceiling
 *     variant). TASK_FAST receives the handle, verifies the payload and
 *     frees the block.
 *
 *  4. ChainTask: every 4th run, TASK_SLOW chains TASK_REPORT (heartbeat
 *     LED toggle every 2 s).
 */

#include <avr/io.h>
#include <avr/pgmspace.h>

#include "eros.h"
#include "actuator.h"

/** Hook-owned actuator: the on-board LED belongs to the hooks only. */
static const ActuatorType actLed PROGMEM = { &Actuator_OpsPortB,
                                             (1u << PB5) };

/* ================================================================== */
/* Application state (RAM - counted as application, not kernel)        */
/* ================================================================== */

static volatile StatusType appLastError;   /* written from ISR context   */
static volatile uint8_t    appErrorCount;

/* ================================================================== */
/* Hooks (all three enabled in config.h)                               */
/* ================================================================== */

/** Board init. Called from StartOS() with interrupts still disabled -
 *  GPIO/hardware setup only, no OS service calls. */
void StartupHook(void)
{
    DDRD |= (uint8_t)((1u << PD2) | (1u << PD3) | (1u << PD4) |
                      (1u << PD5));
    DDRB |= (uint8_t)(1u << PB5);
}

/** Must be ISR-safe: may be raised from the Category-2 tick ISR (e.g. on
 *  a deadline miss). Records the error and pulses the on-board LED. */
void ErrorHook(StatusType error)
{
    appLastError = error;
    appErrorCount++;
    Actuator_Trigger(&actLed);
}

/** Terminal error (e.g. stack canary breach): LED solid ON as a tombstone.
 *  The kernel then parks the MCU in power-down forever. */
void ShutdownHook(StatusType error)
{
    (void)error;
    DDRB  |= (uint8_t)(1u << PB5);
    PORTB |= (uint8_t)(1u << PB5);
}

/* ================================================================== */
/* Init task (see config.h for the priority map and WCET budgets)      */
/* ================================================================== */

/**
 * TASK_INIT - autostart, runs exactly once (lowest priority, WCET <= 2ms).
 * Arms the three periodic alarms and performs the deliberate
 * double-activation ErrorHook demonstration.
 */
void Task_Init(void)
{
    /* Relative alarms: first expiry <increment> ticks from now, then
     * cyclic. SetAbsAlarm variant shown for the 500 ms channel: at this
     * point the counter is only a few ticks old, so 500 is still in the
     * future and fires at absolute tick 500, then every 500 ticks. */
    (void)SetRelAlarm(ALARM_FAST, 10u, 10u);
    (void)SetRelAlarm(ALARM_MED,  50u, 50u);
    (void)SetAbsAlarm(ALARM_SLOW, 500u, 500u);

    /* Deliberate BCC1 activation-limit violation:
     * the first activation is legal (TASK_REPORT is SUSPENDED), the
     * second finds it READY -> E_OS_LIMIT -> ErrorHook pulses the LED. */
    (void)ActivateTask(TASK_REPORT);   /* E_OK                        */
    (void)ActivateTask(TASK_REPORT);   /* E_OS_LIMIT (intentional)    */

    TerminateTask();
}

/* ================================================================== */
/* Entry point                                                         */
/* ================================================================== */
int main(void)
{
    StartOS(); /* noreturn: scheduler loop runs forever */
}
