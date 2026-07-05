/**
 * @file    asw_ipc.h
 * @brief   Producer/consumer payload protocol shared between the 50 ms
 *          producer (asw_50ms.c) and the 10 ms consumer (asw_10ms.c).
 *
 * The demo's only cross-rate data travels through kernel IPC - a pool
 * block handed over via the single-slot mailbox under the RES_DEMO
 * resource - never through shared globals. On this non-preemptive
 * run-to-completion kernel tasks cannot interleave, so no further
 * mutex/semaphore is needed for task<->task data; the RES_DEMO wrap
 * marks the handoff as one logical unit and is where mutual exclusion
 * would attach if the kernel ever became preemptive (same reasoning as
 * Simulink rate transitions - see ../codegen/README.md par.4 and
 * comprehensive-demo/asw_signals.h).
 */

#ifndef ASW_IPC_H
#define ASW_IPC_H

/* Payload layout inside a pool block (OS_POOL_BLOCK_SIZE bytes):     */
#define ASW_MSG_SEQ 0u  /* producer sequence number                   */
#define ASW_MSG_CHK 1u  /* integrity complement: chk == (uint8_t)~seq */

#endif /* ASW_IPC_H */
