/**
 * @file    asw_100ms.h
 * @brief   100 ms rate - PWM breathing ramp (TASK_RAMP).
 */

#ifndef ASW_100MS_H
#define ASW_100MS_H

/** 100 ms task entry (released by ALARM_RAMP, WCET <= 1 ms). */
extern void Task_Ramp(void);

/** Reset the ramp to duty 0, direction up, and force the PWM output
 *  low. Exported service of this rate (the ramp state is owned here);
 *  called by TASK_CMD on the OFF command. Safe cross-rate on the
 *  non-preemptive kernel - see asw_signals.h. */
void Asw_RampReset(void);

#endif /* ASW_100MS_H */
