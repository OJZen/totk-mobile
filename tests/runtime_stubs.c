// Host-only checks exercise kernel/SVC behavior without executing a proprietary SDK.
#include "dm.h"
#include <stdbool.h>
#include <time.h>
struct dm_flags g_flag;
uint64_t g_t0_ns;
uint64_t now_ns(void) {
    struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint64_t)t.tv_sec * 1000000000 + t.tv_nsec;
}
uint64_t guest_ns(void) { return now_ns(); }
int dm_detclock_enabled(void) { return 0; }
void dm_prof_add(int id, uint64_t ns, uint64_t bytes) { (void)id; (void)ns; (void)bytes; }
void crash_thread_init(void) {}
void guest_thread_init(void) {}
#ifndef ZELDA_TEST_GUEST_STACK
void dm_thread_enter(uint64_t entry, uint64_t arg, uint64_t sp) {
    (void)sp; ((void (*)(uint64_t))entry)(arg); abort();
}
#endif
void *guest_sym(const char *name) { (void)name; return NULL; }
module_t *module_of(const void *p) {
    for (int i = 0; i < g_nmod; i++)
        if ((uintptr_t)p >= (uintptr_t)g_mod[i].base && (uintptr_t)p - (uintptr_t)g_mod[i].base < g_mod[i].size) return &g_mod[i];
    return NULL;
}
void zelda_present_metalfx(int enabled) { (void)enabled; }
int zelda_present_metalfx_mode(void) { return 0; }
void zelda_set_shader_wait(bool wait) { (void)wait; }
