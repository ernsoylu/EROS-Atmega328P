/**
 * @file    config.h
 * @brief   TinyOS static configuration for the comprehensive demo.
 *
 * This application re-uses the TinyOS kernel from ../kernel unchanged -
 * only this configuration and the task set differ from the root demo,
 * demonstrating the OSEK model of one app-agnostic kernel +
 * per-application static config.
 *
 * Feature map:
 *   TASK_STATUS  -> heartbeat LED on PB5 (D13) + serial status report
 *   TASK_BUTTON  -> debounced input on PD2 (D2), internal pull-up
 *   TASK_CMD     -> command parser over interrupt-driven UART
 *                   (ON / OFF / STAT), button events via mailbox
 *   TASK_RAMP    -> breathing LED on OC1A/PB1 (D9), Timer1 fast PWM
 *                   (Timer2 belongs to the OS tick!)
 *   Scheduling   -> TinyOS alarms, 10/50/100/500 ms periodic tasks
 */

#ifndef TINY_OS_CONFIG_H
#define TINY_OS_CONFIG_H

#include "tiny_os_types.h"

/* ------------------------------------------------------------------ */
/* System counter                                                      */
/* ------------------------------------------------------------------ */
#define OS_TICK_HZ            1000u
#define OS_TICK_MS            1u
#define OS_ALARM_MAX_OFFSET   32767u

/* ------------------------------------------------------------------ */
/* Tasks (TaskID == priority == ready-mask bit; higher = more urgent)  */
/*                                                                     */
/* WCET budgets (documented, monitored at +/-1 tick resolution):       */
/*   TASK_STARTUP <= 2 ms (banner enqueue + alarm setup, runs once)    */
/*   TASK_STATUS  <= 2 ms (LED toggle + status line enqueue)           */
/*   TASK_RAMP    <= 1 ms (duty step + OCR1A update)                   */
/*   TASK_CMD     <= 2 ms (drains <= 64 UART chars + mailbox poll)     */
/*   TASK_BUTTON  <= 1 ms (debounce sample + optional event post)      */
/* All UART output is enqueued into an interrupt-driven ring buffer,   */
/* so no task ever busy-waits on the wire.                             */
/* ------------------------------------------------------------------ */
#define OS_NUM_TASKS          5u

#define TASK_STARTUP          ((TaskType)0u)  /* lowest priority  */
#define TASK_STATUS           ((TaskType)1u)
#define TASK_RAMP             ((TaskType)2u)
#define TASK_CMD              ((TaskType)3u)
#define TASK_BUTTON           ((TaskType)4u)  /* highest priority */

extern void Task_Startup(void);
extern void Task_Status(void);
extern void Task_Ramp(void);
extern void Task_Cmd(void);
extern void Task_Button(void);

/* ------------------------------------------------------------------ */
/* Alarms                                                              */
/* ------------------------------------------------------------------ */
#define OS_NUM_ALARMS         4u

#define ALARM_BUTTON          ((AlarmType)0u) /* 10 ms  -> TASK_BUTTON */
#define ALARM_CMD             ((AlarmType)1u) /* 50 ms  -> TASK_CMD    */
#define ALARM_RAMP            ((AlarmType)2u) /* 100 ms -> TASK_RAMP   */
#define ALARM_STATUS          ((AlarmType)3u) /* 500 ms -> TASK_STATUS */

/* ------------------------------------------------------------------ */
/* Resources                                                           */
/* RES_UART groups the multi-part status line into one logical unit.   */
/* In this non-preemptive kernel it has no scheduling effect (tasks    */
/* cannot interleave anyway) - kept for OSEK API conformance and as    */
/* the place where mutual exclusion would attach if the kernel ever    */
/* became preemptive. No ISR ceiling: the UART ISRs never print.       */
/* ------------------------------------------------------------------ */
#define OS_NUM_RESOURCES      1u

#define RES_UART              ((ResourceType)0u)

/* ------------------------------------------------------------------ */
/* Fixed-block pool: transports button events to TASK_CMD.             */
/* ------------------------------------------------------------------ */
#define OS_POOL_BLOCK_SIZE    8u
#define OS_POOL_NUM_BLOCKS    4u

/* ------------------------------------------------------------------ */
/* Watchdog aliveness: every periodic task must complete within each   */
/* 2 s WDT window or the board resets.                                 */
/* ------------------------------------------------------------------ */
#define OS_ALIVE_REQUIRED_MASK \
    ((uint8_t)((1u << TASK_BUTTON) | (1u << TASK_CMD) | \
               (1u << TASK_RAMP)   | (1u << TASK_STATUS)))

/* ------------------------------------------------------------------ */
/* Hooks                                                               */
/* ------------------------------------------------------------------ */
#define OS_CFG_STARTUPHOOK    1
#define OS_CFG_ERRORHOOK      1
#define OS_CFG_SHUTDOWNHOOK   1

/* ------------------------------------------------------------------ */
/* Stack monitoring                                                    */
/* ------------------------------------------------------------------ */
#define OS_STACK_CANARY        0xC5u
#define OS_STACK_GUARD_BYTES   8u
#define OS_STACK_PAINT_MARGIN  16u

/* ------------------------------------------------------------------ */
/* Compile-time validation                                             */
/* ------------------------------------------------------------------ */
OS_STATIC_ASSERT(OS_NUM_TASKS >= 1u, "at least one task required");
OS_STATIC_ASSERT(OS_NUM_TASKS <= 8u, "ready queue is an 8-bit mask: max 8 tasks");

OS_STATIC_ASSERT(
    ((1u << TASK_STARTUP) | (1u << TASK_STATUS) | (1u << TASK_RAMP) |
     (1u << TASK_CMD)     | (1u << TASK_BUTTON)) ==
    ((1u << OS_NUM_TASKS) - 1u),
    "task IDs/priorities must be unique bit positions 0..OS_NUM_TASKS-1");

OS_STATIC_ASSERT(OS_NUM_ALARMS >= 1u, "at least one alarm required");
OS_STATIC_ASSERT((ALARM_BUTTON < OS_NUM_ALARMS) && (ALARM_CMD < OS_NUM_ALARMS) &&
                 (ALARM_RAMP < OS_NUM_ALARMS) && (ALARM_STATUS < OS_NUM_ALARMS),
                 "alarm ID out of range");

OS_STATIC_ASSERT(OS_NUM_RESOURCES <= 8u, "resource held-mask is 8-bit");
OS_STATIC_ASSERT(RES_UART < OS_NUM_RESOURCES, "resource ID out of range");

OS_STATIC_ASSERT((OS_POOL_NUM_BLOCKS >= 1u) && (OS_POOL_NUM_BLOCKS <= 8u),
                 "pool allocation bitmask is 8-bit: 1..8 blocks");
OS_STATIC_ASSERT(OS_POOL_BLOCK_SIZE >= 1u,
                 "free-list link needs one byte per block");

OS_STATIC_ASSERT((OS_ALIVE_REQUIRED_MASK & ~((1u << OS_NUM_TASKS) - 1u)) == 0u,
                 "aliveness mask references a non-existent task");

#endif /* TINY_OS_CONFIG_H */
