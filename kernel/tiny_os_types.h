/**
 * @file    tiny_os_types.h
 * @brief   TinyOS - OSEK BCC1 basic types, status codes, states and handles.
 *
 * TinyOS is an ultra-minimalist, statically configured, non-preemptive
 * (cooperative, run-to-completion) OSEK BCC1 kernel for the ATmega328P
 * (Arduino Nano, 16 MHz, 2 KiB SRAM, 32 KiB Flash).
 *
 * Design rules honoured throughout the code base:
 *   - Pure bare-metal C99. No Arduino framework, no dynamic allocation.
 *   - All objects (tasks, alarms, resources, pool) are declared statically
 *     at compile time. ROM ("config") data lives in PROGMEM, the few bytes
 *     of mutable state live in SRAM, linked to their config by index.
 *   - Opaque handles are uint8_t indices into static arrays - never RAM
 *     pointers - so a handle costs 1 byte and can be range-checked in O(1).
 *
 * MISRA C:2012 conformance statement
 * ----------------------------------
 * The code targets MISRA C:2012 mandatory/required rules where practical.
 * Project-wide documented deviations (referenced as D1..D8 in the sources):
 *
 *   D1  Rule 11.4/11.6 (pointer/integer conversion): unavoidable for
 *       memory-mapped AVR special function registers and for reading the
 *       stack pointer register (SP) during stack painting.
 *   D2  Rule 1.2 (language extensions): _Static_assert (C11) is used in
 *       -std=c99 builds (GCC >= 4.6 extension), together with the
 *       avr-gcc attributes ISR()/PROGMEM/naked/used/section/noreturn.
 *       These are required for correct bare-metal operation.
 *   D3  Dir 4.9 / Rule 21.2 (function-like macro): TerminateTask() is a
 *       macro expanding to 'return'. In a run-to-completion kernel a task
 *       terminates by returning from its entry function; the macro exists
 *       purely for OSEK source compatibility and MUST only be used at the
 *       top level of a task function body.
 *   D4  Rule 11.1 (function pointer conversion): task entry points and
 *       PROGMEM vtable slots are fetched with pgm_read_ptr() (void *) and
 *       converted back to their true function pointer type. The stored
 *       object is always written and read with the same type.
 *   D5  Rule 10.x (essential type model): controlled narrowing casts after
 *       integer promotion (AVR int is 16-bit) e.g. (uint8_t)~mask, and the
 *       deliberate wrap-safe tick comparison (int16_t)(now - expiry) which
 *       relies on GCC's defined modulo-2^16 unsigned arithmetic and
 *       two's-complement signed conversion.
 *   D6  Rule 2.2/17.7: OS service return values are explicitly discarded
 *       with (void) casts where the demo intentionally ignores them.
 *   D7  Rule 8.9: kernel-internal state objects have file scope (static)
 *       in tiny_os.c although each is used by several functions and the ISR.
 *   D8  Rule 21.x: <avr/...> and <util/atomic.h> vendor headers are used;
 *       they are the only sanctioned hardware access path on this target.
 *
 * OSEK deviations (documented per requirement):
 *   - Schedule() is intentionally omitted: in a fully non-preemptive
 *     kernel a re-scheduling point inside a running task could only be
 *     implemented by a nested scheduler invocation, which would grow the
 *     single shared stack by one full dispatcher frame per nesting level.
 *     Omitting it keeps worst-case stack depth = deepest task + ISR frame.
 *   - TerminateTask()/ChainTask() return to the caller (see D3); actual
 *     termination happens when the task entry function returns.
 *   - StartOS() takes no AppModeType (single application mode).
 *   - E_OS_CALLEVEL and E_OS_RESOURCE are not part of the mandated
 *     StatusType set; E_OS_ACCESS / E_OS_STATE are used in their place
 *     (call-context violations resp. termination while holding resources).
 */

#ifndef TINY_OS_TYPES_H
#define TINY_OS_TYPES_H

#include <stdint.h>

/* ------------------------------------------------------------------ */
/* Compile-time assertion wrapper (see deviation D2).                  */
/* ------------------------------------------------------------------ */
#define OS_STATIC_ASSERT(cond, msg) _Static_assert((cond), msg)

/* ------------------------------------------------------------------ */
/* OSEK basic types                                                    */
/* ------------------------------------------------------------------ */

/** OSEK status type. 8-bit on purpose: AVR returns it in a single register. */
typedef uint8_t StatusType;

/* Mandated StatusType set. Numeric values follow the OSEK OS 2.2.3
 * binding so traces remain comparable with other OSEK implementations
 * (E_OS_CALLEVEL=2 and E_OS_RESOURCE=6 are intentionally absent). */
#define E_OK          ((StatusType)0u)
#define E_OS_ACCESS   ((StatusType)1u)
#define E_OS_ID       ((StatusType)3u)
#define E_OS_LIMIT    ((StatusType)4u)
#define E_OS_NOFUNC   ((StatusType)5u)
#define E_OS_STATE    ((StatusType)7u)
#define E_OS_VALUE    ((StatusType)8u)

/** Task identifier == static priority == bit position in the ready mask. */
typedef uint8_t TaskType;

/** OSEK task state (BCC1: no WAITING state). SUSPENDED must be 0 so the
 *  zero-initialised .bss section equals "all tasks suspended". */
typedef uint8_t TaskStateType;
#define SUSPENDED     ((TaskStateType)0u)
#define READY         ((TaskStateType)1u)
#define RUNNING       ((TaskStateType)2u)

/** Alarm identifier (index into the static alarm tables). */
typedef uint8_t AlarmType;

/** Counter tick type. 16-bit; all comparisons use wrap-safe signed
 *  distance arithmetic: (int16_t)(now - expiry) >= 0.                 */
typedef uint16_t TickType;

/** Resource identifier (index into the static resource tables). */
typedef uint8_t ResourceType;

/** Fixed-block memory pool handle (block index). */
typedef uint8_t OsPoolHandleType;

/** Task entry point. Termination is implicit on return (BCC1 RTC). */
typedef void (*TaskEntryType)(void);

/** Application mode (single mode supported: OSDEFAULTAPPMODE). */
typedef uint8_t AppModeType;
#define OSDEFAULTAPPMODE ((AppModeType)0u)

/** OSEK reference types (out-parameters of the Get* services). */
typedef TaskType      *TaskRefType;
typedef TaskStateType *TaskStateRefType;
typedef TickType      *TickRefType;

/** OSEK alarm base characteristics (GetAlarmBase). All alarms share the
 *  single 1 kHz system counter. */
typedef struct
{
    TickType maxallowedvalue; /**< 65535 (full uint16_t range)          */
    TickType ticksperbase;    /**< 1                                    */
    TickType mincycle;        /**< 1 (cycle upper bound is 32767)       */
} AlarmBaseType;
typedef AlarmBaseType *AlarmBaseRefType;

/* ------------------------------------------------------------------ */
/* Reserved / invalid handle values                                    */
/* ------------------------------------------------------------------ */
#define OS_INVALID_TASK        ((TaskType)0xFFu)
#define OS_POOL_INVALID_HANDLE ((OsPoolHandleType)0xFFu)

/* ------------------------------------------------------------------ */
/* Static configuration record types (instantiated as const PROGMEM    */
/* tables in config.c; mutable state lives in tiny_os.c, same index).  */
/* ------------------------------------------------------------------ */

/** Per-task ROM configuration. */
typedef struct
{
    TaskEntryType entry;      /**< Task entry function.                     */
    uint8_t       autostart;  /**< 1 = activated during StartOS().          */
    uint8_t       wcet_ticks; /**< Documented WCET budget in ticks (ms).
                                   Monitored at each termination with +/-1
                                   tick resolution; 0 disables monitoring.  */
} OsTaskConfigType;

/** Per-alarm ROM configuration. Expiry action is task activation (BCC1). */
typedef struct
{
    TaskType task;            /**< Task activated on alarm expiry.          */
} OsAlarmConfigType;

/** Per-resource ROM configuration (Immediate Priority Ceiling Protocol).
 *
 *  NOTE: in a non-preemptive kernel the task-level ceiling has NO
 *  scheduling effect whatsoever - the running task can never be preempted
 *  by another task anyway. Resources exist (a) for OSEK API conformance
 *  and (b) to optionally raise the ceiling to ISR level, which masks the
 *  Category-2 tick interrupt while the resource is held.                  */
typedef struct
{
    uint8_t ceiling_prio;     /**< Highest priority of all users (doc/API). */
    uint8_t mask_tick_isr;    /**< 1 = ceiling at ISR level: holding the
                                   resource masks OCIE2A (tick ISR). Hold
                                   time MUST stay well below one tick (1 ms)
                                   - a single pending compare-match flag is
                                   buffered by hardware (TIFR2.OCF2A), so
                                   short critical sections lose no ticks.   */
} OsResourceConfigType;

/* ------------------------------------------------------------------ */
/* Hook prototypes (compile-time optional, see config.h switches)      */
/* ------------------------------------------------------------------ */
extern void StartupHook(void);
extern void ErrorHook(StatusType error);
extern void ShutdownHook(StatusType error);

#endif /* TINY_OS_TYPES_H */
