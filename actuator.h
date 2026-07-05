/**
 * @file    actuator.h
 * @brief   Polymorphic GPIO actuator - OOP-in-C with ZERO RAM cost.
 *
 * Both the object instances and their vtables are const PROGMEM,
 * fetched with pgm_read_ptr()/pgm_read_byte() (deviation D4). Each ASW
 * rate file defines its own instances for the pins it owns; the two
 * port "classes" (vtables) and the virtual dispatch live in actuator.c.
 *
 * Writing 1 to a PINx register toggles the PORTx bit in hardware
 * (ATmega328P datasheet 14.2.2) - a single atomic store, so triggering
 * an actuator is safe from any context, including ErrorHook in the
 * tick ISR.
 */

#ifndef ACTUATOR_H
#define ACTUATOR_H

#include <stdint.h>
#include <avr/pgmspace.h>

typedef void (*ActuatorWriteFn)(uint8_t mask);

/** Actuator vtable ("interface"). Lives in PROGMEM - never in RAM. */
typedef struct
{
    ActuatorWriteFn trigger;
} ActuatorOpsType;

/** Actuator instance: vtable pointer + pin mask. Also PROGMEM. */
typedef struct
{
    const ActuatorOpsType *ops; /* -> PROGMEM vtable */
    uint8_t                mask;
} ActuatorType;

/* Two concrete implementations => real polymorphism. */
extern const ActuatorOpsType Actuator_OpsPortD PROGMEM;
extern const ActuatorOpsType Actuator_OpsPortB PROGMEM;

/** Virtual dispatch: instance -> vtable -> method, all read from Flash. */
void Actuator_Trigger(const ActuatorType *self);

#endif /* ACTUATOR_H */
