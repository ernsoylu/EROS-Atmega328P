/**
 * @file    config.h
 * @brief   TinyOS static application configuration (OSEK "OIL" equivalent).
 *
 * Everything the kernel knows about the application is defined here and in
 * config.c - at compile time, as const PROGMEM tables. There is no runtime
 * object creation of any kind.
 *
 * Scheduling model reminder:
 *   TaskID == static priority == bit position in the 8-bit ready mask.
 *   Higher ID = higher priority. Maximum 8 tasks, priorities unique by
 *   construction (enforced below with OS_STATIC_ASSERT).
 */

#ifndef TINY_OS_CONFIG_H
#define TINY_OS_CONFIG_H

#include "tiny_os_types.h"

/* ------------------------------------------------------------------ */
/* System counter                                                      */
/* ------------------------------------------------------------------ */
/** Tick frequency produced by Timer2 CTC (documentation constant - the
 *  Timer2 register values in tiny_os.c are derived for 16 MHz / 1 kHz). */
#define OS_TICK_HZ            1000u
#define OS_TICK_MS            1u

/** Maximum relative alarm offset/cycle: half the counter range, so the
 *  wrap-safe signed-distance comparison is unambiguous.                */
#define OS_ALARM_MAX_OFFSET   32767u

/* ------------------------------------------------------------------ */
/* Tasks                                                               */
/*                                                                     */
/* WCET budgets (documented, monitored at +/-1 tick resolution):       */
/*   TASK_INIT   <= 2 ms  (one-shot: alarm setup + demo activations)   */
/*   TASK_REPORT <= 1 ms  (LED toggle + counter)                       */
/*   TASK_SLOW   <= 1 ms  (GPIO toggle, ChainTask every 4th run)       */
/*   TASK_MED    <= 1 ms  (GPIO toggle, pool alloc + mailbox send)     */
/*   TASK_FAST   <= 1 ms  (GPIO toggle, mailbox receive + pool free)   */
/*                                                                     */
/* Task release jitter is therefore bounded by the largest WCET of any  */
/* running task (non-preemptive): <= 2 ms during startup, <= 1 ms      */
/* steady-state, in addition to the <= 1 tick alarm activation error.  */
/* ------------------------------------------------------------------ */
#define OS_NUM_TASKS          5u

#define TASK_INIT             ((TaskType)0u)  /* lowest priority  */
#define TASK_REPORT           ((TaskType)1u)
#define TASK_SLOW             ((TaskType)2u)
#define TASK_MED              ((TaskType)3u)
#define TASK_FAST             ((TaskType)4u)  /* highest priority */

/* Task entry prototypes (implemented in main.c, referenced by config.c) */
extern void Task_Init(void);
extern void Task_Report(void);
extern void Task_Slow(void);
extern void Task_Med(void);
extern void Task_Fast(void);

/* ------------------------------------------------------------------ */
/* Alarms (all attached to the 1 kHz system counter)                   */
/* ------------------------------------------------------------------ */
#define OS_NUM_ALARMS         3u

#define ALARM_FAST            ((AlarmType)0u) /* 10 ms cyclic -> TASK_FAST */
#define ALARM_MED             ((AlarmType)1u) /* 50 ms cyclic -> TASK_MED  */
#define ALARM_SLOW            ((AlarmType)2u) /* 500 ms cyclic -> TASK_SLOW */

/* ------------------------------------------------------------------ */
/* Resources (IPCP - see OsResourceConfigType note in tiny_os_types.h) */
/* ------------------------------------------------------------------ */
#define OS_NUM_RESOURCES      1u

#define RES_DEMO              ((ResourceType)0u) /* guards the demo mailbox */

/* ------------------------------------------------------------------ */
/* Fixed-block memory pool geometry                                    */
/* ------------------------------------------------------------------ */
#define OS_POOL_BLOCK_SIZE    8u   /* bytes per block (>= 1: free-list link) */
#define OS_POOL_NUM_BLOCKS    4u   /* 1..8 (allocation bitmask is 8-bit)     */

/* ------------------------------------------------------------------ */
/* Watchdog aliveness supervision                                      */
/*                                                                     */
/* The kernel kicks the 2 s watchdog only when every task in this mask */
/* has run to completion since the previous kick; the aliveness mask   */
/* is cleared immediately after each kick. A stuck/never-released task */
/* therefore leads to a watchdog system reset.                         */
/* ------------------------------------------------------------------ */
#define OS_ALIVE_REQUIRED_MASK \
    ((uint8_t)((1u << TASK_FAST) | (1u << TASK_MED) | (1u << TASK_SLOW)))

/* ------------------------------------------------------------------ */
/* Hooks (compile-time optional)                                       */
/* ------------------------------------------------------------------ */
#define OS_CFG_STARTUPHOOK    1
#define OS_CFG_ERRORHOOK      1
#define OS_CFG_SHUTDOWNHOOK   1

/* ------------------------------------------------------------------ */
/* Stack monitoring                                                    */
/* ------------------------------------------------------------------ */
#define OS_STACK_CANARY        0xC5u /* paint pattern                       */
#define OS_STACK_GUARD_BYTES   8u    /* canary bytes verified per check     */
#define OS_STACK_PAINT_MARGIN  16u   /* safety gap below live SP when painting */

/* ------------------------------------------------------------------ */
/* Compile-time configuration validation                               */
/* ------------------------------------------------------------------ */
OS_STATIC_ASSERT(OS_NUM_TASKS >= 1u, "at least one task required");
OS_STATIC_ASSERT(OS_NUM_TASKS <= 8u, "ready queue is an 8-bit mask: max 8 tasks");

/* TaskID == priority == bit position, all unique and in range: OR-ing
 * one bit per task must yield a dense mask of OS_NUM_TASKS low bits.  */
OS_STATIC_ASSERT(
    ((1u << TASK_INIT) | (1u << TASK_REPORT) | (1u << TASK_SLOW) |
     (1u << TASK_MED)  | (1u << TASK_FAST)) == ((1u << OS_NUM_TASKS) - 1u),
    "task IDs/priorities must be unique bit positions 0..OS_NUM_TASKS-1");

OS_STATIC_ASSERT(OS_NUM_ALARMS >= 1u, "at least one alarm required");
OS_STATIC_ASSERT((ALARM_FAST < OS_NUM_ALARMS) && (ALARM_MED < OS_NUM_ALARMS) &&
                 (ALARM_SLOW < OS_NUM_ALARMS), "alarm ID out of range");

OS_STATIC_ASSERT(OS_NUM_RESOURCES <= 8u, "resource held-mask is 8-bit");
OS_STATIC_ASSERT(RES_DEMO < OS_NUM_RESOURCES, "resource ID out of range");

OS_STATIC_ASSERT((OS_POOL_NUM_BLOCKS >= 1u) && (OS_POOL_NUM_BLOCKS <= 8u),
                 "pool allocation bitmask is 8-bit: 1..8 blocks");
OS_STATIC_ASSERT(OS_POOL_BLOCK_SIZE >= 1u,
                 "free-list link needs one byte per block");

OS_STATIC_ASSERT((OS_ALIVE_REQUIRED_MASK & ~((1u << OS_NUM_TASKS) - 1u)) == 0u,
                 "aliveness mask references a non-existent task");

#endif /* TINY_OS_CONFIG_H */
