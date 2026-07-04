/**
 * @file    main.c
 * @brief   TinyOS demonstration application (Arduino Nano / ATmega328P).
 *
 * Demonstrates every kernel feature:
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
 *
 * OOP-in-C pattern: the GPIO "actuator" driver below is polymorphic with
 * ZERO RAM cost - both the object instances and their vtables are const
 * PROGMEM, fetched with pgm_read_ptr()/pgm_read_byte() (deviation D4).
 */

#include <avr/io.h>
#include <avr/pgmspace.h>

#include "tiny_os.h"

/* ================================================================== */
/* Polymorphic GPIO actuator - instances and vtables 100% in Flash     */
/* ================================================================== */

typedef void (*ActuatorWriteFn)(uint8_t mask);

/** Actuator vtable ("interface"). Lives in PROGMEM - never in RAM. */
typedef struct
{
    ActuatorWriteFn trigger;
} ActuatorOpsType;

/** Actuator instance: vtable pointer + pin mask. Also PROGMEM. */
typedef struct
{
    const ActuatorOpsType *ops; /* -> PROGMEM vtable */
    uint8_t                mask;
} ActuatorType;

/* Two concrete implementations => real polymorphism.
 * Writing 1 to a PINx register toggles the PORTx bit in hardware
 * (ATmega328P datasheet 14.2.2) - a single atomic store, so these are
 * safe from any context, including ErrorHook in the tick ISR. */
static void Actuator_ToggleD(uint8_t mask) { PIND = mask; }
static void Actuator_ToggleB(uint8_t mask) { PINB = mask; }

static const ActuatorOpsType actOpsPortD PROGMEM = { Actuator_ToggleD };
static const ActuatorOpsType actOpsPortB PROGMEM = { Actuator_ToggleB };

static const ActuatorType actFast   PROGMEM = { &actOpsPortD, (1u << PD2) };
static const ActuatorType actMed    PROGMEM = { &actOpsPortD, (1u << PD3) };
static const ActuatorType actSlow   PROGMEM = { &actOpsPortD, (1u << PD4) };
static const ActuatorType actReport PROGMEM = { &actOpsPortD, (1u << PD5) };
static const ActuatorType actLed    PROGMEM = { &actOpsPortB, (1u << PB5) };

/** Virtual dispatch: instance -> vtable -> method, all read from Flash. */
static void Actuator_Trigger(const ActuatorType *self)
{
    const ActuatorOpsType *const ops =
        (const ActuatorOpsType *)pgm_read_ptr(&self->ops);
    const ActuatorWriteFn fn = (ActuatorWriteFn)pgm_read_ptr(&ops->trigger);

    fn(pgm_read_byte(&self->mask));
}

/* ================================================================== */
/* Application state (RAM - counted as application, not kernel)        */
/* ================================================================== */

static volatile StatusType appLastError;   /* written from ISR context   */
static volatile uint8_t    appErrorCount;
static uint8_t             appTxSequence;  /* producer payload sequence  */
static uint8_t             appRxGood;      /* verified messages received */
static uint8_t             appRxBad;       /* payload integrity failures */
static uint8_t             appSlowRuns;    /* ChainTask divider          */

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
/* Tasks (see config.h for the priority map and WCET budget table)     */
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

/**
 * TASK_REPORT - heartbeat on PD5 (D5), toggled every 2 s. Activated once
 * at startup (double-activation demo) and afterwards chained by every
 * 4th TASK_SLOW run. PD5 is deliberately NOT the on-board LED: PB5 is
 * reserved for ErrorHook/ShutdownHook so the boot-time E_OS_LIMIT
 * demonstration stays visible instead of being toggled away microseconds
 * later by this task.
 */
void Task_Report(void)
{
    Actuator_Trigger(&actReport);
    TerminateTask();
}

/**
 * TASK_SLOW - 500 ms period. Scope channel PD4 (1 Hz square wave).
 * Demonstrates ChainTask(): every 4th run chains TASK_REPORT.
 */
void Task_Slow(void)
{
    Actuator_Trigger(&actSlow);

    appSlowRuns++;
    if ((appSlowRuns & 0x03u) == 0u)
    {
        (void)ChainTask(TASK_REPORT); /* executed when we return */
    }
    TerminateTask();
}

/**
 * TASK_MED - 50 ms period, producer. Scope channel PD3 (10 Hz).
 * Allocates a pool block, fills it with a checkable payload and posts
 * the handle into the mailbox under the RES_DEMO IPCP resource.
 */
void Task_Med(void)
{
    OsPoolHandleType handle;

    Actuator_Trigger(&actMed);

    handle = OS_PoolAlloc();
    if (handle != OS_POOL_INVALID_HANDLE)
    {
        uint8_t *const payload = (uint8_t *)OS_PoolPtr(handle);

        appTxSequence++;
        payload[0] = appTxSequence;
        payload[1] = (uint8_t)~appTxSequence; /* integrity complement */

        if (GetResource(RES_DEMO) == E_OK)
        {
            if (OS_MailboxSend(handle) != E_OK)
            {
                /* Mailbox still full (consumer behind): we keep block
                 * ownership, so return it - no leak, no fragmentation. */
                (void)OS_PoolFree(handle);
            }
            (void)ReleaseResource(RES_DEMO); /* LIFO order */
        }
        else
        {
            (void)OS_PoolFree(handle);
        }
    }
    /* Pool exhausted: drop this cycle's sample (bounded, deterministic). */

    TerminateTask();
}

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
                (payload != (const uint8_t *)0) ? (uint8_t)~payload[0] : 0u;

            if ((payload != (const uint8_t *)0) &&
                (payload[1] == expected))
            {
                appRxGood++;
            }
            else
            {
                appRxBad++;
            }
            (void)OS_PoolFree(handle);
        }
        /* E_OS_NOFUNC (empty) is the normal case 4 out of 5 polls:
         * the producer runs at a fifth of our rate. */
    }

    TerminateTask();
}

/* ================================================================== */
/* Entry point                                                         */
/* ================================================================== */
int main(void)
{
    StartOS(); /* noreturn: scheduler loop runs forever */
}
