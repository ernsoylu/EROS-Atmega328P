/**
 * @file    eros.c
 * @brief   EROS kernel - scheduler, tick ISR, alarms, resources,
 *          mailbox, memory pool, stack canary and watchdog supervision.
 *
 * Target: ATmega328P @ 16 MHz (Arduino Nano).
 *
 * Memory budget (verified by the Makefile 'budget' target on a non-LTO
 * reference build; the shipped -flto image is smaller still):
 *   - Kernel Flash (eros.o + config.o text+data) : <= 3072 bytes
 *   - Kernel static RAM (eros.o data+bss)        : <= 128 bytes
 *   The stack and the user-configured pool arena (config.o bss) are
 *   excluded from the 128-byte budget and reported separately.
 *
 * Concurrency model:
 *   - Exactly one Category-2 ISR (Timer2 compare match, 1 kHz tick).
 *   - Every kernel state modification performed at task level uses
 *     ATOMIC_BLOCK(ATOMIC_RESTORESTATE); the ISR runs with interrupts
 *     globally disabled (no nested interrupts), so ISR-side access is
 *     implicitly atomic.
 *   - 8-bit loads/stores are naturally atomic on AVR; 16-bit shared
 *     objects (the tick counter, alarm expiries) are only ever touched
 *     inside atomic sections.
 *
 * MISRA C:2012: see the project deviation record D1..D8 in
 * eros_types.h. Hardware register accesses in this file fall under D1,
 * attribute/ISR usage under D2, PROGMEM reads under D4, and the wrap-safe
 * signed tick distance under D5.
 */

#include <avr/io.h>
#include <avr/interrupt.h>
#include <avr/sleep.h>
#include <avr/wdt.h>
#include <avr/pgmspace.h>
#include <util/atomic.h>

#include "eros.h"
#include "eros_tick.h"   /* EROS_TICK_* aliases: Timer2 (328P/2560) or Timer3 (32U4) */

/* ================================================================== */
/* Early watchdog disable (.init3)                                     */
/* ================================================================== */
/*
 * CRITICAL HARDWARE FIX - runs before main(), before .data/.bss init:
 * after a watchdog reset the ATmega328P keeps WDRF set in MCUSR, which
 * forces the shortest WDT period (15 ms). The stock "old bootloader"
 * (ATmegaBOOT) does not clear it and takes far longer than 15 ms, so the
 * board would boot-loop forever. Clearing MCUSR and disabling the WDT in
 * .init3 (canonical avr-libc pattern: naked + used + section) makes the
 * application side safe.
 *
 * Remaining caveat (documented): on ATmegaBOOT boards a *genuine* WDT
 * system reset still re-enters the slow bootloader with WDRF set and
 * loops before reaching this code. For dependable WDT recovery either
 * burn Optiboot (which clears MCUSR itself) or use the WDT in
 * interrupt+system-reset mode and perform rescue work in the interrupt.
 *
 * os_resetCause lives in .noinit so the reset reason survives for the
 * application to inspect (D2: attributes; .init3 code may not rely on
 * initialised statics - .noinit is never touched by startup code).
 */
uint8_t os_resetCause __attribute__((section(".noinit")));

void os_EarlyWdtDisable(void)
    __attribute__((naked, used, externally_visible, section(".init3")));
void os_EarlyWdtDisable(void)
{
    os_resetCause = MCUSR;
    MCUSR = 0u;
    wdt_disable();
}

/* ================================================================== */
/* Kernel dynamic state (RAM). Every object below is kernel-private.   */
/* Total: ~35 bytes for the demo configuration - well inside the       */
/* 128-byte kernel RAM budget.                                         */
/* ================================================================== */

/** Alarm dynamic state, linked to OS_alarmConfig[] by index. */
typedef struct
{
    TickType expiry;  /**< next expiry tick (wrap-safe compare)         */
    TickType cycle;   /**< 0 = one-shot, else cyclic period             */
    uint8_t  flags;   /**< OS_ALARM_* flag bits                         */
} OsAlarmStateType;

#define OS_ALARM_ACTIVE 0x01u
#define OS_ALARM_WRAP   0x02u /**< SetAbsAlarm past-value: wait for the
                                   half-range waypoint before arming the
                                   real (post-wraparound) expiry.       */

/* --- shared with the ISR: volatile ------------------------------- */
static volatile TickType         os_tickCount;                  /* 2 B  */
static volatile uint8_t          os_readyMask;                  /* 1 B  */
static volatile TaskStateType    os_taskState[OS_NUM_TASKS];    /* 5 B  */
static volatile OsAlarmStateType os_alarmState[OS_NUM_ALARMS];  /* 15 B */
static volatile uint8_t          os_kernelFlags;                /* 1 B  */
static volatile OsPoolHandleType os_mbxSlot;                    /* 1 B  */
static volatile uint8_t          os_mbxFull;                    /* 1 B  */
static volatile uint8_t          os_poolFreeHead;               /* 1 B  */
static volatile uint8_t          os_poolAllocMask;              /* 1 B  */

#define OS_KFLAG_IN_ERRORHOOK 0x01u

/* --- task level only (scheduler context, never touched by the ISR) - */
static TaskType os_currentTask;                                 /* 1 B  */
static TaskType os_chainPending;                                /* 1 B  */
static uint8_t  os_aliveMask;                                   /* 1 B  */
static uint8_t  os_resHeldMask;                                 /* 1 B  */
static uint8_t  os_resSp;                                       /* 1 B  */
static uint8_t  os_resStack[OS_NUM_RESOURCES];                  /* 1 B  */

/** Resource LIFO stack entry: bit7 = "tick ISR was enabled on entry",
 *  bits 6..0 = resource ID (OS_NUM_RESOURCES <= 8 fits trivially).    */
#define OS_RES_ISR_WAS_ON 0x80u
#define OS_RES_ID_MASK    0x7Fu

/* Linker-provided start of free RAM after .data/.bss/.noinit; an incomplete
   array, since it marks the base of a region we walk, not a single byte (D1). */
extern uint8_t __heap_start[];

/* ================================================================== */
/* Internal helpers                                                    */
/* ================================================================== */

/**
 * Central error funnel: raises the (compile-time optional) ErrorHook and
 * hands the status back so call sites can 'return os_Error(code);'.
 * Re-entrancy policy: while one ErrorHook invocation is in progress, any
 * further error (e.g. raised by the tick ISR interrupting the hook) is
 * still returned to its caller but deliberately NOT hooked - it is
 * silently discarded from the hook's perspective. May run in ISR
 * context - the hook implementation must be ISR-safe.
 */
static StatusType os_Error(StatusType code)
{
#if OS_CFG_ERRORHOOK
    if ((os_kernelFlags & OS_KFLAG_IN_ERRORHOOK) == 0u)
    {
        os_kernelFlags |= OS_KFLAG_IN_ERRORHOOK;
        ErrorHook(code);
        os_kernelFlags &= (uint8_t)~OS_KFLAG_IN_ERRORHOOK;
    }
#endif
    return code;
}

/* ------------------------------------------------------------------ */
/* O(1) highest-priority lookup                                        */
/*                                                                     */
/* AVR has no CLZ instruction, so the highest set bit of the 8-bit      */
/* ready mask is found with a 16-entry PROGMEM nibble LUT: two flash    */
/* byte reads worst case, constant time, no array iteration.           */
/* ------------------------------------------------------------------ */
static const uint8_t os_msbNibbleLut[16] PROGMEM =
{
    0u, 0u, 1u, 1u, 2u, 2u, 2u, 2u, 3u, 3u, 3u, 3u, 3u, 3u, 3u, 3u
};

/** Highest set bit position of @p mask. Precondition: mask != 0. */
static uint8_t os_HighestBit(uint8_t mask)
{
    uint8_t pos;
    const uint8_t hi = (uint8_t)(mask >> 4);

    if (hi != 0u)
    {
        pos = (uint8_t)(4u + pgm_read_byte(&os_msbNibbleLut[hi]));
    }
    else
    {
        pos = pgm_read_byte(&os_msbNibbleLut[mask & 0x0Fu]);
    }
    return pos;
}

/**
 * Core activation primitive. Precondition: interrupts disabled (called
 * from the ISR or from inside an ATOMIC_BLOCK). Range check is done by
 * the callers.
 */
static StatusType os_ActivateInternal(TaskType task)
{
    StatusType status;

    if (os_taskState[task] != SUSPENDED)
    {
        status = E_OS_LIMIT; /* BCC1: one pending activation maximum */
    }
    else
    {
        os_taskState[task] = READY;
        os_readyMask |= (uint8_t)(1u << task);
        status = E_OK;
    }
    return status;
}

/* ================================================================== */
/* Category 2 ISR: 1 kHz system tick (tick timer compare match A)      */
/*                                                                     */
/* WCET: O(OS_NUM_ALARMS), all constant work per alarm; with 3 alarms   */
/* well below 10 us at 16 MHz. Alarm expiry -> task activation happens  */
/* here, so activation error is <= 1 tick by construction.             */
/* ================================================================== */
ISR(EROS_TICK_VECT)
{
    os_tickCount++;
    const TickType now = os_tickCount;

    for (uint8_t i = 0u; i < OS_NUM_ALARMS; i++)
    {
        volatile OsAlarmStateType *const a = &os_alarmState[i];

        if ((a->flags & OS_ALARM_ACTIVE) != 0u)
        {
            /* Mandated wrap-safe comparison (deviation D5). */
            if ((int16_t)(now - a->expiry) >= 0)
            {
                if ((a->flags & OS_ALARM_WRAP) != 0u)
                {
                    /* Half-range waypoint reached: the real (absolute,
                     * post-wraparound) expiry is now inside the signed
                     * comparison window. */
                    a->flags &= (uint8_t)~OS_ALARM_WRAP;
                    a->expiry = (TickType)(a->expiry + 0x8000u);
                }
                else
                {
                    const TaskType task =
                        pgm_read_byte(&OS_alarmConfig[i].task);

                    if (os_ActivateInternal(task) != E_OK)
                    {
                        /* Previous activation still pending: the task
                         * overran its period (deadline miss). */
                        (void)os_Error(E_OS_LIMIT);
                    }

                    if (a->cycle != 0u)
                    {
                        /* Anchored re-arm: drift-free cyclic alarm. */
                        a->expiry = (TickType)(a->expiry + a->cycle);
                    }
                    else
                    {
                        a->flags &= (uint8_t)~OS_ALARM_ACTIVE;
                    }
                }
            }
        }
    }
}

/* ================================================================== */
/* Counter & alarms                                                    */
/* ================================================================== */

TickType GetCounterValue(void)
{
    TickType t;

    ATOMIC_BLOCK(ATOMIC_RESTORESTATE)
    {
        t = os_tickCount; /* torn-read-safe 16-bit access */
    }
    return t;
}

StatusType SetRelAlarm(AlarmType alarm, TickType increment, TickType cycle)
{
    StatusType status = E_OK;

    if (alarm >= OS_NUM_ALARMS)
    {
        status = E_OS_ID;
    }
    else if ((increment == 0u) || (increment > OS_ALARM_MAX_OFFSET) ||
             (cycle > OS_ALARM_MAX_OFFSET))
    {
        status = E_OS_VALUE;
    }
    else
    {
        ATOMIC_BLOCK(ATOMIC_RESTORESTATE)
        {
            volatile OsAlarmStateType *const a = &os_alarmState[alarm];

            if ((a->flags & OS_ALARM_ACTIVE) != 0u)
            {
                status = E_OS_STATE;
            }
            else
            {
                a->expiry = (TickType)(os_tickCount + increment);
                a->cycle  = cycle;
                a->flags  = OS_ALARM_ACTIVE;
            }
        }
    }

    return (status == E_OK) ? E_OK : os_Error(status);
}

StatusType SetAbsAlarm(AlarmType alarm, TickType start, TickType cycle)
{
    StatusType status = E_OK;

    if (alarm >= OS_NUM_ALARMS)
    {
        status = E_OS_ID;
    }
    else if (cycle > OS_ALARM_MAX_OFFSET)
    {
        status = E_OS_VALUE;
    }
    else
    {
        ATOMIC_BLOCK(ATOMIC_RESTORESTATE)
        {
            volatile OsAlarmStateType *const a = &os_alarmState[alarm];

            if ((a->flags & OS_ALARM_ACTIVE) != 0u)
            {
                status = E_OS_STATE;
            }
            else
            {
                a->cycle = cycle;

                if ((int16_t)(os_tickCount - start) >= 0)
                {
                    /* start has already passed (or is now): per OSEK the
                     * alarm expires only after the counter wraps back to
                     * 'start'. That event lies up to 65536 ticks ahead -
                     * outside the signed comparison window - so aim for
                     * a half-range waypoint first (see ISR).            */
                    a->expiry = (TickType)(start + 0x8000u);
                    a->flags  = (uint8_t)(OS_ALARM_ACTIVE | OS_ALARM_WRAP);
                }
                else
                {
                    a->expiry = start;
                    a->flags  = OS_ALARM_ACTIVE;
                }
            }
        }
    }

    return (status == E_OK) ? E_OK : os_Error(status);
}

StatusType CancelAlarm(AlarmType alarm)
{
    StatusType status = E_OK;

    if (alarm >= OS_NUM_ALARMS)
    {
        status = E_OS_ID;
    }
    else
    {
        ATOMIC_BLOCK(ATOMIC_RESTORESTATE)
        {
            volatile OsAlarmStateType *const a = &os_alarmState[alarm];

            if ((a->flags & OS_ALARM_ACTIVE) == 0u)
            {
                status = E_OS_NOFUNC;
            }
            else
            {
                a->flags = 0u;
            }
        }
    }

    return (status == E_OK) ? E_OK : os_Error(status);
}

StatusType GetAlarm(AlarmType alarm, TickRefType tickRef)
{
    StatusType status = E_OK;

    if (alarm >= OS_NUM_ALARMS)
    {
        status = E_OS_ID;
    }
    else if (tickRef == (TickRefType)0)
    {
        status = E_OS_VALUE;
    }
    else
    {
        ATOMIC_BLOCK(ATOMIC_RESTORESTATE)
        {
            volatile OsAlarmStateType *const a = &os_alarmState[alarm];

            if ((a->flags & OS_ALARM_ACTIVE) == 0u)
            {
                status = E_OS_NOFUNC;
            }
            else
            {
                /* Modulo-2^16 distance; a pending SetAbsAlarm waypoint
                 * sits half a range before the real expiry. */
                TickType rel = (TickType)(a->expiry - os_tickCount);

                if ((a->flags & OS_ALARM_WRAP) != 0u)
                {
                    rel = (TickType)(rel + 0x8000u);
                }
                *tickRef = rel;
            }
        }
    }

    return (status == E_OK) ? E_OK : os_Error(status);
}

StatusType GetAlarmBase(AlarmType alarm, AlarmBaseRefType baseRef)
{
    StatusType status = E_OK;

    if (alarm >= OS_NUM_ALARMS)
    {
        status = E_OS_ID;
    }
    else if (baseRef == (AlarmBaseRefType)0)
    {
        status = E_OS_VALUE;
    }
    else
    {
        baseRef->maxallowedvalue = 0xFFFFu;
        baseRef->ticksperbase    = 1u;
        baseRef->mincycle        = 1u;
    }

    return (status == E_OK) ? E_OK : os_Error(status);
}

/* ================================================================== */
/* Task management                                                     */
/* ================================================================== */

StatusType ActivateTask(TaskType task)
{
    StatusType status;

    if (task >= OS_NUM_TASKS)
    {
        status = E_OS_ID;
    }
    else
    {
        ATOMIC_BLOCK(ATOMIC_RESTORESTATE)
        {
            status = os_ActivateInternal(task);
        }
    }

    return (status == E_OK) ? E_OK : os_Error(status);
}

StatusType ChainTask(TaskType task)
{
    StatusType status = E_OK;

    if (task >= OS_NUM_TASKS)
    {
        status = E_OS_ID;
    }
    else if (os_currentTask == OS_INVALID_TASK)
    {
        status = E_OS_ACCESS; /* not called from task context */
    }
    else if (os_chainPending != OS_INVALID_TASK)
    {
        status = E_OS_LIMIT;  /* only one chain may be pending */
    }
    else if (task != os_currentTask)
    {
        /* Chaining another task requires it to be SUSPENDED now (BCC1
         * activation limit, checked again when the chain executes). */
        ATOMIC_BLOCK(ATOMIC_RESTORESTATE)
        {
            if (os_taskState[task] != SUSPENDED)
            {
                status = E_OS_LIMIT;
            }
        }
    }
    else
    {
        /* ChainTask(self): explicitly legal, never E_OS_LIMIT - the
         * caller is SUSPENDED before the chain activation runs. */
    }

    if (status == E_OK)
    {
        os_chainPending = task;
    }

    return (status == E_OK) ? E_OK : os_Error(status);
}

StatusType GetTaskID(TaskRefType taskRef)
{
    StatusType status = E_OK;

    if (taskRef == (TaskRefType)0)
    {
        status = E_OS_VALUE;
    }
    else
    {
        *taskRef = os_currentTask; /* OS_INVALID_TASK outside task ctx */
    }

    return (status == E_OK) ? E_OK : os_Error(status);
}

StatusType GetTaskState(TaskType task, TaskStateRefType stateRef)
{
    StatusType status = E_OK;

    if (task >= OS_NUM_TASKS)
    {
        status = E_OS_ID;
    }
    else if (stateRef == (TaskStateRefType)0)
    {
        status = E_OS_VALUE;
    }
    else
    {
        *stateRef = os_taskState[task]; /* 8-bit read: atomic on AVR */
    }

    return (status == E_OK) ? E_OK : os_Error(status);
}

AppModeType GetActiveApplicationMode(void)
{
    return OSDEFAULTAPPMODE;
}

/* ================================================================== */
/* Resources (Immediate Priority Ceiling Protocol)                     */
/*                                                                     */
/* Non-preemptive kernel => the task-level ceiling has no scheduling    */
/* effect (the running task can never be preempted by another task).    */
/* The resource API exists for OSEK conformance and, when               */
/* mask_tick_isr is configured, to raise the ceiling to ISR level by    */
/* masking the Category-2 tick interrupt (EROS_TICK_OCIE). A compare    */
/* match arriving while masked is latched in TIFR.OCF, so holding a      */
/* resource for < 1 tick loses no time.                                 */
/* ================================================================== */

StatusType GetResource(ResourceType res)
{
    StatusType status = E_OK;

    if (res >= OS_NUM_RESOURCES)
    {
        status = E_OS_ID;
    }
    else if ((os_resHeldMask & (uint8_t)(1u << res)) != 0u)
    {
        status = E_OS_ACCESS; /* already occupied */
    }
    else
    {
        uint8_t entry = res;

        if (pgm_read_byte(&OS_resourceConfig[res].mask_tick_isr) != 0u)
        {
            if ((EROS_TICK_TIMSK & (uint8_t)(1u << EROS_TICK_OCIE)) != 0u)
            {
                entry |= OS_RES_ISR_WAS_ON;
            }
            EROS_TICK_TIMSK &= (uint8_t)~(1u << EROS_TICK_OCIE); /* raise to ISR ceiling */
        }

        os_resStack[os_resSp] = entry;
        os_resSp++;
        os_resHeldMask |= (uint8_t)(1u << res);
    }

    return (status == E_OK) ? E_OK : os_Error(status);
}

StatusType ReleaseResource(ResourceType res)
{
    StatusType status = E_OK;

    if (res >= OS_NUM_RESOURCES)
    {
        status = E_OS_ID;
    }
    else if ((os_resSp == 0u) ||
             ((os_resStack[os_resSp - 1u] & OS_RES_ID_MASK) != res))
    {
        /* Not occupied, or LIFO nesting order violated. */
        status = E_OS_NOFUNC;
    }
    else
    {
        os_resSp--;
        if ((os_resStack[os_resSp] & OS_RES_ISR_WAS_ON) != 0u)
        {
            EROS_TICK_TIMSK |= (uint8_t)(1u << EROS_TICK_OCIE); /* drop ISR ceiling */
        }
        os_resHeldMask &= (uint8_t)~(1u << res);
    }

    return (status == E_OK) ? E_OK : os_Error(status);
}

/** Dispatcher epilogue guard: a task terminated while still holding
 *  resources. Raise ErrorHook once, then force-release in LIFO order so
 *  the kernel (and the tick ISR masking) returns to a sane state. */
static void os_ReleaseLeakedResources(void)
{
    if (os_resSp != 0u)
    {
        (void)os_Error(E_OS_STATE); /* OSEK would say E_OS_RESOURCE (D-note) */

        while (os_resSp != 0u)
        {
            os_resSp--;
            if ((os_resStack[os_resSp] & OS_RES_ISR_WAS_ON) != 0u)
            {
                EROS_TICK_TIMSK |= (uint8_t)(1u << EROS_TICK_OCIE);
            }
        }
        os_resHeldMask = 0u;
    }
}

/* ================================================================== */
/* Deterministic O(1) fixed-block memory pool                          */
/*                                                                     */
/* Free blocks form a singly linked list threaded through their own     */
/* first byte (index of the next free block, 0xFF = end), so the only   */
/* kernel RAM cost is the head index plus an 8-bit allocation bitmask   */
/* used for O(1) double-free / invalid-handle detection.                */
/* ================================================================== */

static void os_PoolInit(void)
{
    for (uint8_t i = 0u; i < (uint8_t)(OS_POOL_NUM_BLOCKS - 1u); i++)
    {
        OS_poolArena[(uint16_t)i * OS_POOL_BLOCK_SIZE] = (uint8_t)(i + 1u);
    }
    OS_poolArena[(uint16_t)(OS_POOL_NUM_BLOCKS - 1u) * OS_POOL_BLOCK_SIZE] =
        OS_POOL_INVALID_HANDLE;

    os_poolFreeHead  = 0u;
    os_poolAllocMask = 0u;
}

OsPoolHandleType OS_PoolAlloc(void)
{
    OsPoolHandleType handle;

    ATOMIC_BLOCK(ATOMIC_RESTORESTATE)
    {
        handle = os_poolFreeHead;
        if (handle != OS_POOL_INVALID_HANDLE)
        {
            os_poolFreeHead =
                OS_poolArena[(uint16_t)handle * OS_POOL_BLOCK_SIZE];
            os_poolAllocMask |= (uint8_t)(1u << handle);
        }
    }
    return handle; /* OS_POOL_INVALID_HANDLE <=> OS_PoolPtr() == NULL */
}

StatusType OS_PoolFree(OsPoolHandleType handle)
{
    StatusType status = E_OK;

    if (handle >= OS_POOL_NUM_BLOCKS)
    {
        status = E_OS_VALUE;
    }
    else
    {
        ATOMIC_BLOCK(ATOMIC_RESTORESTATE)
        {
            if ((os_poolAllocMask & (uint8_t)(1u << handle)) == 0u)
            {
                status = E_OS_VALUE; /* double free / never allocated */
            }
            else
            {
                OS_poolArena[(uint16_t)handle * OS_POOL_BLOCK_SIZE] =
                    os_poolFreeHead;
                os_poolFreeHead   = handle;
                os_poolAllocMask &= (uint8_t)~(1u << handle);
            }
        }
    }

    return (status == E_OK) ? E_OK : os_Error(status);
}

void *OS_PoolPtr(OsPoolHandleType handle)
{
    void *p = (void *)0;

    /* Both range AND allocation state are checked, honouring the
     * documented NULL-for-unallocated contract: handing out a pointer
     * into a free block would let the caller overwrite the free-list
     * link threaded through its first byte. The 8-bit mask read is
     * naturally atomic on AVR. */
    if ((handle < OS_POOL_NUM_BLOCKS) &&
        ((os_poolAllocMask & (uint8_t)(1u << handle)) != 0u))
    {
        p = &OS_poolArena[(uint16_t)handle * OS_POOL_BLOCK_SIZE];
    }
    return p;
}

/* ================================================================== */
/* Single-slot mailbox (pool handle + full flag)                       */
/*                                                                     */
/* Full-on-send (E_OS_STATE) and empty-on-receive (E_OS_NOFUNC) are     */
/* NORMAL polling outcomes for a non-blocking mailbox, not API misuse,  */
/* so they deliberately do NOT raise ErrorHook - otherwise a consumer   */
/* polling faster than its producer would drown the hook in noise.     */
/* Genuine misuse (NULL out-parameter) still goes through os_Error().  */
/* ================================================================== */

StatusType OS_MailboxSend(OsPoolHandleType handle)
{
    StatusType status = E_OK;

    ATOMIC_BLOCK(ATOMIC_RESTORESTATE)
    {
        if (os_mbxFull != 0u)
        {
            status = E_OS_STATE; /* normal flow: no ErrorHook */
        }
        else
        {
            os_mbxSlot = handle;
            os_mbxFull = 1u;
        }
    }

    return status;
}

StatusType OS_MailboxReceive(OsPoolHandleType *handle)
{
    StatusType status = E_OK;

    if (handle == (OsPoolHandleType *)0)
    {
        status = os_Error(E_OS_VALUE);
    }
    else
    {
        ATOMIC_BLOCK(ATOMIC_RESTORESTATE)
        {
            if (os_mbxFull == 0u)
            {
                status = E_OS_NOFUNC; /* normal flow: no ErrorHook */
            }
            else
            {
                *handle    = os_mbxSlot;
                os_mbxFull = 0u;
            }
        }
    }

    return status;
}

/* ================================================================== */
/* Stack canary                                                        */
/* ================================================================== */
/*
 * At StartOS() every byte between the end of the static data
 * (__heap_start) and a small margin below the live stack pointer is
 * painted with OS_STACK_CANARY. At every scheduling point the deepest
 * OS_STACK_GUARD_BYTES of that region are verified: the stack grows
 * downwards, so an overflow tramples them first. A breach is fatal
 * (memory is already corrupt) => ShutdownOS(E_OS_LIMIT).
 * (OSEK's E_OS_STACKFAULT is outside the mandated StatusType set;
 * E_OS_LIMIT is used as the documented substitute.)
 */

static void os_StackPaint(void)
{
    uint8_t *p = __heap_start;
    uint8_t *const limit =
        (uint8_t *)(uint16_t)(SP - (uint16_t)OS_STACK_PAINT_MARGIN); /* D1 */

    while (p < limit)
    {
        /* NOSONAR(c:S3519): __heap_start is the linker heap-start symbol; the
         * RAM from it up to SP is the stack region this loop paints (avr-libc
         * idiom, deviation D1). Sonar models the extern array as one byte. */
        *p = (uint8_t)OS_STACK_CANARY; /* NOSONAR */
        p++;
    }
}

static void os_StackCheck(void)
{
    const uint8_t *p = __heap_start;
    uint8_t i;

    for (i = 0u; i < (uint8_t)OS_STACK_GUARD_BYTES; i++)
    {
        /* NOSONAR(c:S3519): reads the guard bytes of the __heap_start stack
         * region (see os_StackPaint) - the extern array is not a 1-byte object. */
        if (p[i] != (uint8_t)OS_STACK_CANARY) /* NOSONAR */
        {
            ShutdownOS(E_OS_LIMIT); /* stack overflow - unrecoverable */
        }
    }
}

/* ================================================================== */
/* Watchdog supervision                                                */
/* ================================================================== */
/*
 * The 2 s watchdog is kicked only when every task in
 * OS_ALIVE_REQUIRED_MASK has run to completion since the last kick
 * (the dispatcher records each clean termination in os_aliveMask).
 * The aliveness mask is reset immediately after each kick, so every
 * supervised task must prove itself again within every WDT period.
 */
static void os_WdtService(void)
{
    if ((os_aliveMask & OS_ALIVE_REQUIRED_MASK) == OS_ALIVE_REQUIRED_MASK)
    {
        wdt_reset();
        os_aliveMask = 0u; /* reset aliveness immediately after the kick */
    }
}

/* ================================================================== */
/* Dispatcher                                                          */
/* ================================================================== */

static void os_Dispatch(void)
{
    TaskType   tid;
    StatusType chainStatus = E_OK;

    ATOMIC_BLOCK(ATOMIC_RESTORESTATE)
    {
        tid = os_HighestBit(os_readyMask);          /* O(1) pick        */
        os_readyMask &= (uint8_t)~(1u << tid);      /* dequeue          */
        os_taskState[tid] = RUNNING;
    }

    os_currentTask  = tid;
    os_chainPending = OS_INVALID_TASK;

    {
        /* D4: entry pointer stored in PROGMEM, fetched via pgm_read_ptr. */
        const TaskEntryType entry =
            (TaskEntryType)pgm_read_ptr(&OS_taskConfig[tid].entry);
        const TickType tStart = GetCounterValue();

        entry(); /* run to completion - return == TerminateTask()       */

        /* WCET budget supervision (+/- 1 tick resolution). */
        {
            const uint8_t budget =
                pgm_read_byte(&OS_taskConfig[tid].wcet_ticks);
            const TickType elapsed =
                (TickType)(GetCounterValue() - tStart);

            if ((budget != 0u) && (elapsed > (TickType)budget))
            {
                (void)os_Error(E_OS_LIMIT); /* WCET budget overrun      */
            }
        }
    }

    os_ReleaseLeakedResources();

    ATOMIC_BLOCK(ATOMIC_RESTORESTATE)
    {
        os_taskState[tid] = SUSPENDED; /* implicit TerminateTask()      */

        if (os_chainPending != OS_INVALID_TASK)
        {
            /* ChainTask(self) is legal precisely because the caller is
             * SUSPENDED again by the time this activation runs. */
            chainStatus = os_ActivateInternal(os_chainPending);
            os_chainPending = OS_INVALID_TASK;
        }
    }

    if (chainStatus != E_OK)
    {
        /* Chained task was activated by an ISR between the ChainTask()
         * call and task termination - BCC1 activation limit. */
        (void)os_Error(E_OS_LIMIT);
    }

    os_aliveMask |= (uint8_t)(1u << tid); /* clean termination proven   */
    os_currentTask = OS_INVALID_TASK;
}

/* ================================================================== */
/* OS control                                                          */
/* ================================================================== */

void StartOS(void)
{
    uint8_t i;

    cli();

    os_StackPaint();

    /* Explicit kernel state init: .bss zeroing already covers most of
     * it, but nonzero sentinels and the pool free list must be built. */
    os_currentTask  = OS_INVALID_TASK;
    os_chainPending = OS_INVALID_TASK;
    os_poolFreeHead = OS_POOL_INVALID_HANDLE;
    os_PoolInit();

    /* Autostart task activations (interrupts still disabled). */
    for (i = 0u; i < (uint8_t)OS_NUM_TASKS; i++)
    {
        if (pgm_read_byte(&OS_taskConfig[i].autostart) != 0u)
        {
            (void)os_ActivateInternal((TaskType)i);
        }
    }

    /*
     * Hardware tick: CTC mode, 1 kHz, on the profile-selected tick timer
     * (Timer2 on 328P/2560; Timer3 on 32U4 - see eros_tick.h).
     *   16 MHz / 64 (prescaler) = 250 kHz; OCRA = 249 -> 250 counts
     *   -> exactly 1.000 kHz compare match rate.
     * The CTC/prescaler bit encodings differ per timer, so the values are
     * pre-composed in eros_tick.h (EROS_TICK_TCCR{A,B}_VAL).
     */
    EROS_TICK_TCCRA = EROS_TICK_TCCRA_VAL;  /* CTC, OCxA/OCxB disconnected */
    EROS_TICK_TCCRB = EROS_TICK_TCCRB_VAL;  /* CTC bits + prescaler /64    */
    EROS_TICK_OCRA  = 249u;
    EROS_TICK_TCNT  = 0u;
#if EROS_TICK_HAS_ASSR
    ASSR = 0u;                        /* Timer2 synchronous clocking    */
#endif
    EROS_TICK_TIFR  = (uint8_t)(1u << EROS_TICK_OCF);  /* clear stale flag */
    EROS_TICK_TIMSK = (uint8_t)(1u << EROS_TICK_OCIE); /* compare match A on */

    set_sleep_mode(SLEEP_MODE_IDLE);  /* tick timer keeps running in IDLE */

#if OS_CFG_STARTUPHOOK
    StartupHook(); /* interrupts disabled; board init only              */
#endif

    wdt_enable(WDTO_2S);
    wdt_reset();

    sei();

    /* ---------------- scheduler loop (never exits) ----------------- */
    for (;;)
    {
        os_StackCheck();   /* every scheduling point                    */
        os_WdtService();

        /* Canonical race-free idle entry: a wakeup interrupt occurring
         * between the ready_mask test and sleep_cpu() cannot be lost,
         * because sei() re-enables interrupts only one instruction
         * before SLEEP, and that instruction boundary is interrupt-
         * atomic on AVR. */
        cli();
        if (os_readyMask == 0u)
        {
            sleep_enable();
            sei();
            sleep_cpu();
            sleep_disable();
        }
        else
        {
            sei();
            os_Dispatch();
        }
    }
}

void ShutdownOS(StatusType error)
{
    cli();

#if OS_CFG_SHUTDOWNHOOK
    ShutdownHook(error);
#else
    (void)error;
#endif

    /* Halt for good: WDT off, deepest sleep, interrupts off. Only a
     * hardware reset recovers - see eros.h for the auto-recovery
     * alternative and the ATmegaBOOT boot-loop caveat above. */
    wdt_disable();
    set_sleep_mode(SLEEP_MODE_PWR_DOWN);
    for (;;)
    {
        sleep_enable();
        sleep_cpu();
    }
}
