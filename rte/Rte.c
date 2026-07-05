/**
 * @file    Rte.c
 * @brief   RTE implementation for the appKnbSwt model<->OS integration.
 *
 * Includes only the *public* headers of the layers it connects and
 * touches nothing inside them: the ASW is driven through its exported
 * interface globals (Intfc/Param), the BSW through its driver APIs. This
 * is the template a future erosgen `models:` pass would emit per SWC
 * (see Rte_Cfg.h / rte/README.md).
 */

#include <avr/io.h>

#include "Rte.h"
#include "Rte_Cfg.h"

/* ASW - generated model (read-only) */
#include "appKnbSwt.h"        /* RTE_CFG_INIT_FN / RTE_CFG_RUNNABLE_FN     */
#include "appKnbSwt_Intfc.h"  /* RTE_CFG_KNB_SIGNAL / RTE_CFG_LED_SIGNAL   */
#include "appKnbSwt_Param.h"  /* Knb_Thresh_Pc_Pt / Knb_Hyst_Pc_Pt         */

/* BSW - MCAL drivers (read-only) */
#include "adc.h"              /* drivers/adc.c                             */

/* OS binding headers - only in a full-OS build (see Rte_Start below). */
#ifdef RTE_WITH_EROS
#include "eros.h"
#include "config.h"
#endif

/* --- Port adapters (IoHwAb): BSW signals <-> ASW ports -------------- */

/* Input port KnbVal: ADC conversion -> 0..1023 count. */
static uint16_t Rte_Read_KnbVal(void)
{
    return ADC_Read(RTE_CFG_KNB_ADC_CH);
}

/* Output port Led1: boolean -> digital output pin. */
static void Rte_Write_Led1(uint8_t on)
{
    if (on)
    {
        RTE_CFG_LED_PORT |= (uint8_t)(1u << RTE_CFG_LED_BIT);
    }
    else
    {
        RTE_CFG_LED_PORT &= (uint8_t)~(1u << RTE_CFG_LED_BIT);
    }
}

/* --- Lifecycle ----------------------------------------------------- */

void Rte_Init(void)
{
    /* BSW init for the bound ports. */
    ADC_Init();
    RTE_CFG_LED_DDR  |= (uint8_t)(1u << RTE_CFG_LED_BIT);
    RTE_CFG_LED_PORT &= (uint8_t)~(1u << RTE_CFG_LED_BIT);

    /* RTE owns configuration: assign ASW calibration parameters. */
    Knb_Thresh_Pc_Pt = RTE_CAL_Knb_Thresh_Pc_Pt;
    Knb_Hyst_Pc_Pt   = RTE_CAL_Knb_Hyst_Pc_Pt;

    /* ASW init. */
    RTE_CFG_INIT_FN();
}

void Rte_Run_appKnbSwt(void)
{
    /* implicit read: sensor ports <- BSW */
    RTE_CFG_KNB_SIGNAL = Rte_Read_KnbVal();

    /* run the ASW runnable */
    RTE_CFG_RUNNABLE_FN();

    /* implicit write: actuator ports -> BSW */
    Rte_Write_Led1(RTE_CFG_LED_SIGNAL);
}

/* --- OS binding (production build only) ----------------------------- */

#ifdef RTE_WITH_EROS
void Rte_Start(void)
{
    /* Assign the runnable's rate to the OS: a cyclic alarm releases the
     * task that calls Rte_Run_appKnbSwt(). TASK/ALARM ids come from the
     * generated config (app.yaml). */
    (void)SetRelAlarm(ALARM_APPKNBSWT,
                      RTE_CFG_PERIOD_MS, RTE_CFG_PERIOD_MS);
}
#endif
