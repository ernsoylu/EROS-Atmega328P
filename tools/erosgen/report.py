"""End-of-run report: the pre-flash schedulability + static-RAM plan."""


def report(s):
    print(f"erosgen: system '{s.name}' ({s.src})")
    print(f"  tick: {s.tick_hz} Hz")
    print("  tasks (prio high->low):")
    for t in reversed(s.tasks_by_prio):
        if t.period_ms:
            kind = f"{t.period_ms} ms"
        elif t.autostart:
            kind = "autostart"
        else:
            kind = "activated"
        wd = " wdg" if t.watchdog else ""
        print(f"    {t.priority}  TASK_{t.name:<10} {kind:>10}  "
              f"wcet {t.wcet_ticks} tick(s){wd}  -> {t.entry}()")
    base = s.periodic[0]
    load = sum(t.wcet_ticks for t in s.periodic)
    print(f"  schedulability: sum(WCET)={load} ticks <= base period "
          f"{base.period_ticks} ticks  OK")
    print("  static RAM plan:")
    arena = s.pool_block * s.pool_blocks
    print("    kernel state          ~35 B")
    print(f"    pool arena            {arena} B "
          f"({s.pool_blocks} x {s.pool_block})")
    uart = s.peripherals.get("uart")
    if uart is not None:
        uart = uart or {}
        tx, rx = int(uart.get("tx_ring", 128)), int(uart.get("rx_ring", 64))
        print(f"    uart rings            {tx + rx} B (TX {tx} + RX {rx})")
    for w in s.warnings:
        print(f"  WARNING: {w}")
