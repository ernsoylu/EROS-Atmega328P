/**
 * @file    asw_10ms.h
 * @brief   10 ms rate - scope jitter channel + mailbox consumer
 *          (TASK_FAST).
 */

#ifndef ASW_10MS_H
#define ASW_10MS_H

/** 10 ms task entry (released by ALARM_FAST, highest priority). */
extern void Task_Fast(void);

#endif /* ASW_10MS_H */
