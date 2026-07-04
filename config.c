/**
 * @file    config.c
 * @brief   TinyOS static configuration tables (Flash) and pool arena (RAM).
 *
 * Everything in this file is created at compile time - no runtime object
 * creation exists anywhere in TinyOS. The const configuration tables are
 * placed in PROGMEM (read-only Flash) and are linked by index to the small
 * volatile dynamic-state arrays that live in tiny_os.c.
 *
 * RAM accounting: the only RAM this file contributes is OS_poolArena[],
 * which is user-configured payload memory and therefore excluded from the
 * 128-byte kernel RAM budget (the Makefile 'budget' target reports it
 * separately, alongside the stack).
 */

#include <avr/pgmspace.h>

#include "tiny_os.h"

/* ------------------------------------------------------------------ */
/* Task table - index == TaskType == priority == ready-mask bit.       */
/* WCET budgets are documented in config.h and monitored at runtime    */
/* with +/-1 tick resolution.                                          */
/* ------------------------------------------------------------------ */
const OsTaskConfigType OS_taskConfig[OS_NUM_TASKS] PROGMEM =
{
    [TASK_INIT]   = { Task_Init,   1u /* autostart */, 2u /* WCET ticks */ },
    [TASK_REPORT] = { Task_Report, 0u,                 1u },
    [TASK_SLOW]   = { Task_Slow,   0u,                 1u },
    [TASK_MED]    = { Task_Med,    0u,                 1u },
    [TASK_FAST]   = { Task_Fast,   0u,                 1u },
};

/* ------------------------------------------------------------------ */
/* Alarm table - expiry action is activation of the configured task.   */
/* Periods are armed by Task_Init (see main.c): 10 / 50 / 500 ms.      */
/* ------------------------------------------------------------------ */
const OsAlarmConfigType OS_alarmConfig[OS_NUM_ALARMS] PROGMEM =
{
    [ALARM_FAST] = { TASK_FAST },
    [ALARM_MED]  = { TASK_MED  },
    [ALARM_SLOW] = { TASK_SLOW },
};

/* ------------------------------------------------------------------ */
/* Resource table (IPCP). RES_DEMO guards the demo mailbox handoff.    */
/* ceiling_prio documents the highest-priority user (TASK_FAST); in a  */
/* non-preemptive kernel it has no scheduling effect. mask_tick_isr=1  */
/* raises the ceiling to ISR level: the 1 kHz tick ISR is masked while */
/* the resource is held (hold times here are a few microseconds).      */
/* ------------------------------------------------------------------ */
const OsResourceConfigType OS_resourceConfig[OS_NUM_RESOURCES] PROGMEM =
{
    [RES_DEMO] = { TASK_FAST /* ceiling */, 1u /* mask tick ISR */ },
};

/* ------------------------------------------------------------------ */
/* Fixed-block pool arena (RAM, zero-init .bss). Geometry from         */
/* config.h; the free list is threaded through the blocks themselves,  */
/* so the arena is the pool's entire payload cost.                     */
/* ------------------------------------------------------------------ */
uint8_t OS_poolArena[(uint16_t)OS_POOL_NUM_BLOCKS * OS_POOL_BLOCK_SIZE];
