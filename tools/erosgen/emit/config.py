"""Emitters for config.h and config.c (always regenerated)."""

from ..constants import GENERATED_BANNER, INCLUDE_EROS_H


def emit_config_h(s):
    L = []
    L.append("/**")
    L.append(" * @file    config.h")
    L.append(f" * @brief   {GENERATED_BANNER.format(src=s.src.name)}")
    L.append(" *")
    L.append(" * EROS static application configuration (OSEK \"OIL\" "
             "equivalent).")
    L.append(" * Edit " + s.src.name + " and regenerate; see tools/erosgen.py.")
    L.append(" */")
    L.append("")
    L.append("#ifndef EROS_CONFIG_H")
    L.append("#define EROS_CONFIG_H")
    L.append("")
    L.append('#include "eros_types.h"')
    L.append("")
    L.append("/* ---- system counter --------------------------------------------- */")
    L.append(f"#define OS_TICK_HZ            {s.tick_hz}u")
    L.append(f"#define OS_TICK_MS            {s.tick_ms}u")
    L.append(f"#define OS_ALARM_MAX_OFFSET   {s.alarm_max_offset}u")
    L.append("")
    L.append("/* ---- tasks: TaskID == priority == ready-mask bit ----------------- */")
    L.append("/* WCET budgets (ticks, monitored at +/-1 tick):                      */")
    for t in s.tasks_by_prio:
        if t.period_ms:
            per = f"{t.period_ms} ms"
        elif t.autostart:
            per = "autostart, one-shot"
        else:
            per = "activated/chained"
        L.append(f"/*   TASK_{t.name:<10} prio {t.priority}  wcet <= "
                 f"{t.wcet_ticks} tick(s)  ({per}) */")
    L.append(f"#define OS_NUM_TASKS          {len(s.tasks)}u")
    L.append("")
    for t in s.tasks_by_prio:
        L.append(f"#define TASK_{t.name:<16} ((TaskType){t.priority}u)")
    L.append("")
    for t in s.tasks_by_prio:
        L.append(f"extern void {t.entry}(void);")
    L.append("")
    L.append("/* ---- alarms: one cyclic alarm per periodic task ------------------ */")
    L.append(f"#define OS_NUM_ALARMS         {len(s.periodic)}u")
    L.append("")
    for i, t in enumerate(s.periodic):
        L.append(f"#define ALARM_{t.name:<15} ((AlarmType){i}u) "
                 f"/* {t.period_ms} ms -> TASK_{t.name} */")
    L.append("")
    L.append("/* Period macros for SetRelAlarm/SetAbsAlarm calls in the app. */")
    for t in s.periodic:
        L.append(f"#define TASK_{t.name}_PERIOD_TICKS {t.period_ticks}u")
    L.append("")
    L.append("/* ---- resources (IPCP; ceiling = highest-priority user) ---------- */")
    L.append(f"#define OS_NUM_RESOURCES      {len(s.resources)}u")
    L.append("")
    for i, r in enumerate(s.resources):
        users = ", ".join(t.name for t in r.users)
        L.append(f"#define RES_{r.name:<17} ((ResourceType){i}u) "
                 f"/* users: {users} */")
    L.append("")
    L.append("/* ---- fixed-block pool -------------------------------------------- */")
    L.append(f"#define OS_POOL_BLOCK_SIZE    {s.pool_block}u")
    L.append(f"#define OS_POOL_NUM_BLOCKS    {s.pool_blocks}u")
    L.append("")
    L.append("/* ---- watchdog aliveness ------------------------------------------ */")
    if s.alive_tasks:
        parts = " | ".join(f"(1u << TASK_{t.name})" for t in s.alive_tasks)
        L.append("#define OS_ALIVE_REQUIRED_MASK \\")
        L.append(f"    ((uint8_t)({parts}))")
    else:
        L.append("#define OS_ALIVE_REQUIRED_MASK ((uint8_t)0u)")
    L.append("")
    L.append("/* ---- hooks -------------------------------------------------------- */")
    L.append(f"#define OS_CFG_STARTUPHOOK    {s.hook_startup}")
    L.append(f"#define OS_CFG_ERRORHOOK      {s.hook_error}")
    L.append(f"#define OS_CFG_SHUTDOWNHOOK   {s.hook_shutdown}")
    L.append("")
    L.append("/* ---- stack monitoring --------------------------------------------- */")
    L.append(f"#define OS_STACK_CANARY        0x{s.stack_canary:02X}u")
    L.append(f"#define OS_STACK_GUARD_BYTES   {s.stack_guard}u")
    L.append(f"#define OS_STACK_PAINT_MARGIN  {s.stack_margin}u")
    L.append("")
    L.append("/* ---- compile-time validation -------------------------------------- */")
    L.append('OS_STATIC_ASSERT(OS_NUM_TASKS >= 1u, "at least one task required");')
    L.append('OS_STATIC_ASSERT(OS_NUM_TASKS <= 8u, "ready queue is an 8-bit mask: max 8 tasks");')
    L.append("")
    ors = " | ".join(f"(1u << TASK_{t.name})" for t in s.tasks_by_prio)
    L.append("OS_STATIC_ASSERT(")
    L.append(f"    ({ors}) ==")
    L.append("    ((1u << OS_NUM_TASKS) - 1u),")
    L.append('    "task IDs/priorities must be unique bit positions 0..OS_NUM_TASKS-1");')
    L.append("")
    L.append('OS_STATIC_ASSERT(OS_NUM_ALARMS >= 1u, "at least one alarm required");')
    checks = " && ".join(f"(ALARM_{t.name} < OS_NUM_ALARMS)" for t in s.periodic)
    L.append(f"OS_STATIC_ASSERT({checks},")
    L.append('                 "alarm ID out of range");')
    L.append("")
    L.append('OS_STATIC_ASSERT(OS_NUM_RESOURCES <= 8u, "resource held-mask is 8-bit");')
    rchecks = " && ".join(f"(RES_{r.name} < OS_NUM_RESOURCES)" for r in s.resources)
    L.append(f"OS_STATIC_ASSERT({rchecks},")
    L.append('                 "resource ID out of range");')
    L.append("")
    L.append('OS_STATIC_ASSERT((OS_POOL_NUM_BLOCKS >= 1u) && (OS_POOL_NUM_BLOCKS <= 8u),')
    L.append('                 "pool allocation bitmask is 8-bit: 1..8 blocks");')
    L.append('OS_STATIC_ASSERT(OS_POOL_BLOCK_SIZE >= 1u,')
    L.append('                 "free-list link needs one byte per block");')
    L.append("")
    L.append('OS_STATIC_ASSERT((OS_ALIVE_REQUIRED_MASK & ~((1u << OS_NUM_TASKS) - 1u)) == 0u,')
    L.append('                 "aliveness mask references a non-existent task");')
    L.append("")
    L.append("#endif /* EROS_CONFIG_H */")
    return "\n".join(L) + "\n"


def emit_config_c(s):
    L = []
    L.append("/**")
    L.append(" * @file    config.c")
    L.append(f" * @brief   {GENERATED_BANNER.format(src=s.src.name)}")
    L.append(" *")
    L.append(" * Const configuration tables in PROGMEM; the pool arena is the")
    L.append(" * only RAM contributed here (user payload, reported separately")
    L.append(" * from the kernel RAM budget).")
    L.append(" */")
    L.append("")
    L.append("#include <avr/pgmspace.h>")
    L.append("")
    L.append(INCLUDE_EROS_H)
    L.append("")
    L.append("const OsTaskConfigType OS_taskConfig[OS_NUM_TASKS] PROGMEM =")
    L.append("{")
    for t in s.tasks_by_prio:
        L.append(f"    [TASK_{t.name}] = {{ {t.entry}, "
                 f"{1 if t.autostart else 0}u /* autostart */, "
                 f"{t.wcet_ticks}u /* WCET ticks */ }},")
    L.append("};")
    L.append("")
    L.append("const OsAlarmConfigType OS_alarmConfig[OS_NUM_ALARMS] PROGMEM =")
    L.append("{")
    for t in s.periodic:
        L.append(f"    [ALARM_{t.name}] = {{ TASK_{t.name} }},")
    L.append("};")
    L.append("")
    L.append("const OsResourceConfigType OS_resourceConfig[OS_NUM_RESOURCES] PROGMEM =")
    L.append("{")
    for r in s.resources:
        L.append(f"    [RES_{r.name}] = {{ TASK_{r.ceiling.name} /* ceiling */, "
                 f"{1 if r.mask_tick_isr else 0}u /* mask tick ISR */ }},")
    L.append("};")
    L.append("")
    L.append("uint8_t OS_poolArena[(uint16_t)OS_POOL_NUM_BLOCKS * OS_POOL_BLOCK_SIZE];")
    return "\n".join(L) + "\n"
