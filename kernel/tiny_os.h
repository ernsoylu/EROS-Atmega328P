/**
 * @file    tiny_os.h
 * @brief   TinyOS public API - OSEK BCC1 services plus documented extensions.
 *
 * Interrupt service routine categories (OSEK):
 *   Category 1: MUST NOT call any OS service. Use for latency-critical
 *               ISRs that only touch hardware (declare with ISR() as usual).
 *   Category 2: may call ActivateTask() (and only ActivateTask()) from
 *               interrupt context. The 1 ms Timer2 tick ISR inside the
 *               kernel is Category 2: it activates tasks on alarm expiry.
 *
 * Deviation (documented): Schedule() is omitted. In this fully
 * non-preemptive kernel every task runs to completion, so the only effect
 * Schedule() could have is a nested dispatcher invocation, growing the
 * single shared stack by a full scheduler frame per nesting level. Leaving
 * it out keeps the worst-case stack depth statically boundable.
 */

#ifndef TINY_OS_H
#define TINY_OS_H

#include "tiny_os_types.h"
#include "config.h"

/* ------------------------------------------------------------------ */
/* Static configuration tables (defined in config.c, stored in Flash)  */
/* ------------------------------------------------------------------ */
extern const OsTaskConfigType     OS_taskConfig[OS_NUM_TASKS];
extern const OsAlarmConfigType    OS_alarmConfig[OS_NUM_ALARMS];
extern const OsResourceConfigType OS_resourceConfig[OS_NUM_RESOURCES];

/** Memory pool arena (defined in config.c; reported separately from the
 *  kernel RAM budget, together with the stack). */
extern uint8_t OS_poolArena[(uint16_t)OS_POOL_NUM_BLOCKS * OS_POOL_BLOCK_SIZE];

/** MCUSR snapshot captured in .init3 before the register is cleared for
 *  the early watchdog disable (lives in .noinit, so it survives startup).
 *  Bits: PORF/EXTRF/BORF/WDRF - lets the application report the reset
 *  cause, e.g. distinguish a watchdog recovery from a power-on.          */
extern uint8_t os_resetCause;

/* ------------------------------------------------------------------ */
/* OS control                                                          */
/* ------------------------------------------------------------------ */

/**
 * Start the operating system. Never returns.
 *
 * Sequence: stack paint -> kernel state init -> autostart activations ->
 * Timer2 1 kHz tick config -> StartupHook() (interrupts still disabled;
 * intended for board/GPIO init - defer OS service calls to an autostart
 * task) -> wdt_enable(WDTO_2S) -> sei() -> scheduler loop.
 *
 * Idle behaviour: when no task is READY the CPU enters SLEEP_MODE_IDLE
 * using the canonical race-free cli()/sleep_enable()/sei()/sleep_cpu()
 * sequence; the tick ISR (or any other enabled interrupt) wakes it.
 */
void StartOS(void) __attribute__((noreturn));

/**
 * Shut down the operating system. Never returns.
 *
 * Disables interrupts, calls ShutdownHook(error) if configured, disables
 * the watchdog and parks the MCU in SLEEP_MODE_PWR_DOWN forever (only a
 * reset recovers). Rationale: an uncontrolled error (e.g. stack canary
 * breach) must not be "rebooted around" silently. If automatic recovery
 * is preferred, remove wdt_disable() and the WDT will reset the board -
 * but read the ATmegaBOOT caveat in tiny_os.c first.
 */
void ShutdownOS(StatusType error) __attribute__((noreturn));

/* ------------------------------------------------------------------ */
/* Task management (OSEK BCC1)                                         */
/* ------------------------------------------------------------------ */

/**
 * Transfer @p task from SUSPENDED to READY.
 *
 * BCC1: exactly one activation may be pending. Activating a task that is
 * not SUSPENDED (READY or RUNNING - including the caller itself) returns
 * E_OS_LIMIT and raises ErrorHook.
 *
 * Callable from task level and from Category 2 ISRs.
 *
 * @retval E_OK        activated
 * @retval E_OS_ID     invalid task ID
 * @retval E_OS_LIMIT  activation limit (task not SUSPENDED)
 */
StatusType ActivateTask(TaskType task);

/**
 * Terminate the calling task (OSEK source-compatibility macro).
 *
 * In this run-to-completion kernel termination is implicit when the task
 * entry function returns; the macro simply performs that return and MUST
 * only be used at the top level of a task function (deviation D3).
 */
#define TerminateTask() return

/**
 * Terminate the calling task and activate @p task afterwards.
 *
 * ChainTask(current task ID) is explicitly legal and never returns
 * E_OS_LIMIT: the chained re-activation happens after the caller has
 * been set back to SUSPENDED. Deviation D3: unlike strict OSEK this
 * service returns; the caller must return (or use TerminateTask())
 * immediately afterwards - the chain is executed by the dispatcher at
 * that point.
 *
 * @retval E_OK        chain recorded (executed on task return)
 * @retval E_OS_ID     invalid task ID
 * @retval E_OS_ACCESS not called from task context
 * @retval E_OS_LIMIT  chained task (other than self) not SUSPENDED, or a
 *                     chain is already pending
 */
StatusType ChainTask(TaskType task);

/**
 * Return the ID of the task currently RUNNING (OS_INVALID_TASK when
 * called outside task context, e.g. from a hook or Category-2 ISR).
 *
 * @retval E_OK        always (E_OS_VALUE only for a NULL reference)
 */
StatusType GetTaskID(TaskRefType taskRef);

/**
 * Return the state (SUSPENDED / READY / RUNNING) of @p task.
 *
 * @retval E_OK        state stored
 * @retval E_OS_ID     invalid task ID
 * @retval E_OS_VALUE  NULL reference
 */
StatusType GetTaskState(TaskType task, TaskStateRefType stateRef);

/**
 * Return the active application mode. TinyOS supports the single
 * OSDEFAULTAPPMODE (StartOS takes no mode - documented deviation).
 */
AppModeType GetActiveApplicationMode(void);

/* ------------------------------------------------------------------ */
/* Counter & alarms                                                    */
/* ------------------------------------------------------------------ */

/**
 * Atomically read the 16-bit system counter (1 kHz tick). The read is
 * performed inside ATOMIC_BLOCK(ATOMIC_RESTORESTATE) to prevent torn
 * 16-bit accesses.
 */
TickType GetCounterValue(void);

/**
 * Arm @p alarm to expire @p increment ticks from now, then every
 * @p cycle ticks (cycle == 0: one-shot). Expiry activates the statically
 * configured task from within the tick ISR with <= 1 tick (1 ms) error.
 *
 * @retval E_OK        armed
 * @retval E_OS_ID     invalid alarm ID
 * @retval E_OS_VALUE  increment == 0 or increment/cycle > 32767
 * @retval E_OS_STATE  alarm already armed
 */
StatusType SetRelAlarm(AlarmType alarm, TickType increment, TickType cycle);

/**
 * Arm @p alarm to expire when the counter reaches the absolute value
 * @p start, then every @p cycle ticks (cycle == 0: one-shot).
 *
 * Documented behaviour when @p start has already passed (or equals the
 * current counter value): per OSEK, the alarm expires after the counter
 * wraps around, i.e. the next time the counter equals @p start - up to
 * 65536 ticks in the future. Internally this is realised with the same
 * wrap-safe comparison via an intermediate half-range waypoint
 * (see os_TickIsr in tiny_os.c), never with exact-match polling.
 *
 * @retval E_OK        armed
 * @retval E_OS_ID     invalid alarm ID
 * @retval E_OS_VALUE  cycle > 32767
 * @retval E_OS_STATE  alarm already armed
 */
StatusType SetAbsAlarm(AlarmType alarm, TickType start, TickType cycle);

/**
 * Disarm @p alarm.
 *
 * @retval E_OK        cancelled
 * @retval E_OS_ID     invalid alarm ID
 * @retval E_OS_NOFUNC alarm was not armed
 */
StatusType CancelAlarm(AlarmType alarm);

/**
 * Return the number of ticks before @p alarm expires (wraparound-aware,
 * including the SetAbsAlarm post-wrap waypoint distance).
 *
 * @retval E_OK        *tickRef holds the remaining ticks
 * @retval E_OS_ID     invalid alarm ID
 * @retval E_OS_NOFUNC alarm not armed
 * @retval E_OS_VALUE  NULL reference
 */
StatusType GetAlarm(AlarmType alarm, TickRefType tickRef);

/**
 * Return the characteristics of @p alarm's counter (all alarms share the
 * 1 kHz system counter: maxallowedvalue 65535, ticksperbase 1, mincycle 1).
 *
 * @retval E_OK        *baseRef filled in
 * @retval E_OS_ID     invalid alarm ID
 * @retval E_OS_VALUE  NULL reference
 */
StatusType GetAlarmBase(AlarmType alarm, AlarmBaseRefType baseRef);

/* ------------------------------------------------------------------ */
/* Resources (IPCP, non-blocking - see OsResourceConfigType)           */
/* ------------------------------------------------------------------ */

/**
 * Occupy a resource. Task level only. Nested acquisitions MUST be
 * released in LIFO order. If the resource is configured with an ISR
 * ceiling (mask_tick_isr) the Category-2 tick interrupt is masked until
 * release; hold time must stay well below 1 ms.
 *
 * @retval E_OK        occupied
 * @retval E_OS_ID     invalid resource ID
 * @retval E_OS_ACCESS resource already occupied
 */
StatusType GetResource(ResourceType res);

/**
 * Release a resource.
 *
 * @retval E_OK        released
 * @retval E_OS_ID     invalid resource ID
 * @retval E_OS_NOFUNC resource not occupied, or LIFO order violated
 *                     (ErrorHook is raised)
 */
StatusType ReleaseResource(ResourceType res);

/* ------------------------------------------------------------------ */
/* TinyOS extensions (not part of OSEK BCC1 - documented additions)    */
/* ------------------------------------------------------------------ */

/**
 * O(1) fixed-block allocator over the static arena in config.c.
 * @return block handle, or OS_POOL_INVALID_HANDLE if the pool is
 *         exhausted (the pointer-level equivalent, OS_PoolPtr(), then
 *         yields NULL).
 */
OsPoolHandleType OS_PoolAlloc(void);

/**
 * Return the block handle to the pool.
 * @retval E_OK       released
 * @retval E_OS_VALUE invalid handle or block not currently allocated
 *                    (double free)
 */
StatusType OS_PoolFree(OsPoolHandleType handle);

/**
 * Translate a pool handle into its block address.
 * @return block pointer, or NULL for an invalid/unallocated handle.
 */
void *OS_PoolPtr(OsPoolHandleType handle);

/**
 * Post a pool handle into the single-slot mailbox (non-blocking).
 * @retval E_OK       posted
 * @retval E_OS_STATE mailbox full (message NOT consumed - caller keeps
 *                    ownership of the block). Normal polling outcome:
 *                    does NOT raise ErrorHook.
 */
StatusType OS_MailboxSend(OsPoolHandleType handle);

/**
 * Fetch the pool handle from the single-slot mailbox (non-blocking).
 * @retval E_OK        received; *handle holds the block (ownership moves
 *                     to the caller, who must OS_PoolFree() it)
 * @retval E_OS_NOFUNC mailbox empty. Normal polling outcome: does NOT
 *                     raise ErrorHook.
 * @retval E_OS_VALUE  handle pointer is NULL (raises ErrorHook)
 */
StatusType OS_MailboxReceive(OsPoolHandleType *handle);

#endif /* TINY_OS_H */
