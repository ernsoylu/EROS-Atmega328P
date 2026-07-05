/**
 * @file    actuator.c
 * @brief   Polymorphic GPIO actuator - vtables and virtual dispatch.
 */

#include <avr/io.h>

#include "actuator.h"

static void Actuator_ToggleD(uint8_t mask) { PIND = mask; }
static void Actuator_ToggleB(uint8_t mask) { PINB = mask; }

const ActuatorOpsType Actuator_OpsPortD PROGMEM = { Actuator_ToggleD };
const ActuatorOpsType Actuator_OpsPortB PROGMEM = { Actuator_ToggleB };

void Actuator_Trigger(const ActuatorType *self)
{
    const ActuatorOpsType *const ops =
        (const ActuatorOpsType *)pgm_read_ptr(&self->ops);
    const ActuatorWriteFn fn = (ActuatorWriteFn)pgm_read_ptr(&ops->trigger);

    fn(pgm_read_byte(&self->mask));
}
