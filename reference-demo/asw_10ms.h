/**
 * @file    asw_10ms.h
 * @brief   10 ms rate - scope jitter channel + debounced button
 *          (TASK_BUTTON).
 */

#ifndef ASW_10MS_H
#define ASW_10MS_H

/** 10 ms task entry (released by ALARM_BUTTON, highest priority). */
extern void Task_Button(void);

#endif /* ASW_10MS_H */
