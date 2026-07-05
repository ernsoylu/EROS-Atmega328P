/**
 * @file    runtest.c
 * @brief   Host-side simavr runner for EROS firmware unit tests.
 *
 * Loads an AVR ELF into a simulated ATmega328P, optionally applies
 * external stimulus (ADC voltages, GPIO edges, SPI slave echo), captures
 * USART0 output, and decides pass/fail from the sentinel line the
 * firmware prints (see tests/common/testkit.h):
 *
 *     EROS-TEST: PASS   -> exit 0
 *     EROS-TEST: FAIL x -> exit 1
 *     (neither before timeout) -> exit 2
 *
 * Build: linked against libsimavr (see tests/Makefile). This is the AVR
 * analogue of a Renode robot script - Renode has no AVR core, so simavr
 * is the simulator that can actually execute ATmega328P firmware.
 *
 * Usage:
 *   runtest <fw.elf> [--timeout-ms N] [--freq HZ] [--mcu NAME] [--echo]
 *           [--adc CH:MV]...        static ADC channel voltage (mV)
 *           [--adc-sweep CH:HI:LO:HALF_MS]
 *                                   triangle ramp: HI->LO over HALF_MS,
 *                                   then LO->HI over HALF_MS, then hold HI
 *           [--spi-echo]            SPI slave returns the byte just sent
 *           [--pin P,BIT,US,LVL]... drive PORTx pin BIT to LVL at +US us
 *           [--watch-pin P,BIT]     log transitions of output pin P,BIT
 */

#include <sim_avr.h>
#include <sim_elf.h>
#include <avr_uart.h>
#include <avr_adc.h>
#include <avr_ioport.h>
#include <avr_spi.h>

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

#define MAX_STIM 32

enum { VERDICT_PENDING = 0, VERDICT_PASS, VERDICT_FAIL };

static int         g_verdict = VERDICT_PENDING;
static int         g_echo    = 0;         /* mirror UART to stderr        */
static char        g_line[256];           /* current UART line buffer     */
static size_t      g_linelen = 0;
static char        g_failtag[64] = "";

/* --- UART output capture ------------------------------------------- */

static void uart_out_cb(struct avr_irq_t *irq, uint32_t value, void *param)
{
    (void)irq; (void)param;
    char c = (char)value;

    if (g_echo)
        fputc(c, stderr);

    if (c == '\n' || g_linelen >= sizeof(g_line) - 1)
    {
        g_line[g_linelen] = '\0';
        if (strncmp(g_line, "EROS-TEST: PASS", 15) == 0)
            g_verdict = VERDICT_PASS;
        else if (strncmp(g_line, "EROS-TEST: FAIL", 15) == 0)
        {
            g_verdict = VERDICT_FAIL;
            strncpy(g_failtag, g_line + 15, sizeof(g_failtag) - 1);
            g_failtag[sizeof(g_failtag) - 1] = '\0';
        }
        g_linelen = 0;
    }
    else if (c != '\r')
    {
        g_line[g_linelen++] = c;
    }
}

/* --- SPI slave: echo the byte the master just clocked out ---------- */

static avr_t *g_avr = NULL;

static void spi_echo_cb(struct avr_irq_t *irq, uint32_t value, void *param)
{
    (void)irq; (void)param;
    /* Feed the same byte back as the slave's response so the master's
     * SPI_Transfer() sees a known, deterministic MISO stream. */
    avr_raise_irq(avr_io_getirq(g_avr, AVR_IOCTL_SPI_GETIRQ('0'),
                                SPI_IRQ_INPUT), value & 0xFF);
}

/* --- Scheduled GPIO edge ------------------------------------------- */

struct pin_edge {
    char     port;
    uint8_t  bit;
    uint8_t  level;
    uint64_t at_us;
};
static struct pin_edge g_pins[MAX_STIM];
static int             g_npins = 0;

static avr_cycle_count_t pin_edge_cb(struct avr_t *avr,
                                     avr_cycle_count_t when, void *param)
{
    (void)when;
    struct pin_edge *p = (struct pin_edge *)param;
    avr_raise_irq(avr_io_getirq(avr, AVR_IOCTL_IOPORT_GETIRQ(p->port),
                                p->bit), p->level);
    return 0; /* one-shot */
}

/* --- ADC static channel voltages ----------------------------------- */

struct adc_val { uint8_t ch; uint32_t mv; };
static struct adc_val g_adc[MAX_STIM];
static int            g_nadc = 0;

/* --- ADC triangle sweep (ramp down then up) ------------------------ */
/* Models a knob swept HI -> LO over half_ms, then LO -> HI over the next
 * half_ms, then held at HI. Re-fires every 1 ms of simulated time. */

struct adc_sweep { uint8_t ch; long hi_mv, lo_mv, half_ms; int active; };
static struct adc_sweep g_sweep = { 0, 0, 0, 0, 0 };

static avr_cycle_count_t adc_sweep_cb(struct avr_t *avr,
                                      avr_cycle_count_t when, void *param)
{
    (void)param;
    long per_ms = (long)(avr->frequency / 1000u);
    long ms     = (long)(when / (avr_cycle_count_t)per_ms);
    long h      = g_sweep.half_ms;
    long mv;

    if (ms <= h)
        mv = g_sweep.hi_mv + (g_sweep.lo_mv - g_sweep.hi_mv) * ms / h;
    else if (ms <= 2 * h)
        mv = g_sweep.lo_mv + (g_sweep.hi_mv - g_sweep.lo_mv) * (ms - h) / h;
    else
        mv = g_sweep.hi_mv;

    if (mv < 0) mv = 0;
    avr_raise_irq(avr_io_getirq(avr, AVR_IOCTL_ADC_GETIRQ,
                                ADC_IRQ_ADC0 + g_sweep.ch), (uint32_t)mv);
    return when + (avr_cycle_count_t)per_ms;   /* again in 1 ms */
}

/* --- Watch an output pin and log its transitions ------------------- */

struct pin_watch { char port; uint8_t bit; int active; };
static struct pin_watch g_watch = { 0, 0, 0 };

static void watch_pin_cb(struct avr_irq_t *irq, uint32_t value, void *param)
{
    (void)irq; (void)param;
    long per_ms = (long)(g_avr->frequency / 1000u);
    long ms     = (long)(g_avr->cycle / (avr_cycle_count_t)per_ms);
    printf("  DO %c%u = %u  @ %ld ms\n",
           g_watch.port, g_watch.bit, value & 1u, ms);
}

int main(int argc, char **argv)
{
    const char *fname   = NULL;
    const char *mcu     = "atmega328p";
    uint32_t    freq    = 16000000;
    uint32_t    timeout = 2000;   /* ms of simulated time */

    for (int i = 1; i < argc; i++)
    {
        if (argv[i][0] != '-') { fname = argv[i]; continue; }

        if (!strcmp(argv[i], "--timeout-ms") && i + 1 < argc)
            timeout = (uint32_t)strtoul(argv[++i], NULL, 0);
        else if (!strcmp(argv[i], "--freq") && i + 1 < argc)
            freq = (uint32_t)strtoul(argv[++i], NULL, 0);
        else if (!strcmp(argv[i], "--mcu") && i + 1 < argc)
            mcu = argv[++i];
        else if (!strcmp(argv[i], "--echo"))
            g_echo = 1;
        /* --spi-echo is re-scanned as a plain flag after this loop. */
        else if (!strcmp(argv[i], "--adc") && i + 1 < argc)
        {
            unsigned ch, mv;
            if (sscanf(argv[++i], "%u:%u", &ch, &mv) == 2 && g_nadc < MAX_STIM)
            { g_adc[g_nadc].ch = (uint8_t)ch; g_adc[g_nadc].mv = mv; g_nadc++; }
        }
        else if (!strcmp(argv[i], "--pin") && i + 1 < argc)
        {
            char port; unsigned bit, us, lvl;
            if (sscanf(argv[++i], "%c,%u,%u,%u", &port, &bit, &us, &lvl) == 4
                && g_npins < MAX_STIM)
            {
                g_pins[g_npins].port  = port;
                g_pins[g_npins].bit   = (uint8_t)bit;
                g_pins[g_npins].at_us = us;
                g_pins[g_npins].level = (uint8_t)lvl;
                g_npins++;
            }
        }
        else if (!strcmp(argv[i], "--adc-sweep") && i + 1 < argc)
        {
            unsigned ch; long hi, lo, half;
            if (sscanf(argv[++i], "%u:%ld:%ld:%ld", &ch, &hi, &lo, &half) == 4)
            {
                g_sweep.ch = (uint8_t)ch; g_sweep.hi_mv = hi;
                g_sweep.lo_mv = lo; g_sweep.half_ms = half > 0 ? half : 1;
                g_sweep.active = 1;
            }
        }
        else if (!strcmp(argv[i], "--watch-pin") && i + 1 < argc)
        {
            char port; unsigned bit;
            if (sscanf(argv[++i], "%c,%u", &port, &bit) == 2)
            {
                g_watch.port = port; g_watch.bit = (uint8_t)bit;
                g_watch.active = 1;
            }
        }
    }

    if (!fname) { fprintf(stderr, "usage: runtest <fw.elf> [opts]\n"); return 3; }

    /* Re-scan for --spi-echo as a plain flag (kept separate from pins). */
    int spi_echo = 0;
    for (int i = 1; i < argc; i++)
        if (!strcmp(argv[i], "--spi-echo")) spi_echo = 1;

    elf_firmware_t fw;
    memset(&fw, 0, sizeof(fw));
    if (elf_read_firmware(fname, &fw) != 0)
    {
        fprintf(stderr, "runtest: cannot read ELF '%s'\n", fname);
        return 3;
    }

    avr_t *avr = avr_make_mcu_by_name(mcu);
    if (!avr) { fprintf(stderr, "runtest: unknown mcu '%s'\n", mcu); return 3; }
    avr_init(avr);
    avr->frequency = freq;
    g_avr = avr;
    avr_load_firmware(avr, &fw);

    /* UART0 output -> capture. */
    avr_irq_register_notify(
        avr_io_getirq(avr, AVR_IOCTL_UART_GETIRQ('0'), UART_IRQ_OUTPUT),
        uart_out_cb, NULL);

    /* The UART OUTPUT notify above is the "reader": simavr delivers every
     * transmitted byte to it, so no flow-control flag tweaking is needed
     * for our small, promptly-drained bursts. */

    if (spi_echo)
        avr_irq_register_notify(
            avr_io_getirq(avr, AVR_IOCTL_SPI_GETIRQ('0'), SPI_IRQ_OUTPUT),
            spi_echo_cb, NULL);

    for (int i = 0; i < g_nadc; i++)
        avr_raise_irq(avr_io_getirq(avr, AVR_IOCTL_ADC_GETIRQ,
                                    ADC_IRQ_ADC0 + g_adc[i].ch), g_adc[i].mv);

    for (int i = 0; i < g_npins; i++)
        avr_cycle_timer_register_usec(avr, g_pins[i].at_us,
                                      pin_edge_cb, &g_pins[i]);

    if (g_sweep.active)
    {
        /* Seed the starting voltage, then update every 1 ms. */
        avr_raise_irq(avr_io_getirq(avr, AVR_IOCTL_ADC_GETIRQ,
                                    ADC_IRQ_ADC0 + g_sweep.ch),
                      (uint32_t)g_sweep.hi_mv);
        avr_cycle_timer_register(avr, freq / 1000, adc_sweep_cb, NULL);
    }

    if (g_watch.active)
        avr_irq_register_notify(
            avr_io_getirq(avr, AVR_IOCTL_IOPORT_GETIRQ(g_watch.port),
                          g_watch.bit), watch_pin_cb, NULL);

    uint64_t budget = (uint64_t)timeout * (freq / 1000);
    int      state  = cpu_Running;

    while (avr->cycle < budget && g_verdict == VERDICT_PENDING)
    {
        state = avr_run(avr);
        if (state == cpu_Done || state == cpu_Crashed)
            break;
    }

    if (g_verdict == VERDICT_PASS)
    {
        printf("PASS  %s\n", fname);
        return 0;
    }
    if (g_verdict == VERDICT_FAIL)
    {
        printf("FAIL  %s :%s\n", fname, g_failtag);
        return 1;
    }
    fprintf(stderr, "TIMEOUT/NO-VERDICT  %s (cpu state %d, %llu cycles)\n",
            fname, state, (unsigned long long)avr->cycle);
    return 2;
}
