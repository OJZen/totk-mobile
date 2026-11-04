"""Adapt the shared NVN implementation in core/ at build time without changing its sources."""
import os
import json
import re
from pathlib import Path

EXTRA_NAMES = (
    'nvnProgramGetSubtileSize', 'nvnTextureBuilderGetViewOffset',
    'nvnCommandBufferSetControlMemoryCallbackEnabled', 'nvnCommandBufferBindUniformBuffersDeferred',
    'nvnCommandBufferBindStorageBuffersDeferred', 'nvnCommandBufferBindTexturesDeferred',
    'nvnCommandBufferBindImagesDeferred', 'nvnCommandBufferBindSeparateTexturesDeferred',
    'nvnCommandBufferBindSeparateSamplersDeferred', 'nvnCommandBufferOverrideSubtileSize',
    'nvnCommandBufferSetZCullZF32CompressionEnable', 'nvnCommandBufferSetProvokingVertexMode',
    'nvnDeviceSetAutomaticCheckpointsMask',
)
EXTRA_PROTOTYPES = {
    'nvnCommandBufferBindTexturesDeferred': 'static void h_nvnCommandBufferBindTexturesDeferred(NVNcommandBuffer *, NVNshaderStage, int, int, const NVNtextureHandle *);',
}


def adapt_interface(source):
    extra = ''.join(EXTRA_PROTOTYPES[name] + '\n' if name in EXTRA_PROTOTYPES else
                    f'static void missing_{name}(void) {{ LOG("BLOCKED NVN {name}"); exit(78); }}\n' for name in EXTRA_NAMES)
    lookup = ''.join(f'    if (!strcmp(name, "{name}")) return (void *){"h" if name in EXTRA_PROTOTYPES else "missing"}_{name};\n' for name in EXTRA_NAMES)
    replacements = {
        'static FILE *g_tsv;': '#include "nvn_frame_probe.h"\n#include "trace_writer.h"\nstatic FILE *g_tsv;',
        'g_tsv = fopen(t, "w")': 'g_tsv = zelda_trace_open(t)',
        '    if (g_tsv) fflush(g_tsv);':
        '    if (g_tsv) { zelda_frame_probe p = zelda_frame_probe_start(); fflush(g_tsv); zelda_frame_probe_note(g_frame, "tsv-flush", p); }',
        '    if (g_tsv) fprintf(g_tsv, "%llu\\t%s\\t%.3f\\n", f, name, v);':
        '    if (g_tsv) { zelda_frame_probe p = zelda_frame_probe_start(); fprintf(g_tsv, "%llu\\t%s\\t%.3f\\n", f, name, v); zelda_frame_probe_note(f, name, p); }',
        '    uint64_t f = g_frame, ps, dr; mtl_stats(&ps, &dr);':
        '    zelda_frame_probe z_metrics = zelda_frame_probe_start();\n    uint64_t f = g_frame, ps, dr; mtl_stats(&ps, &dr);',
        '    dump_frame();':
        '''    zelda_frame_probe_note(f, "metrics", z_metrics);
    zelda_frame_probe z_dump = zelda_frame_probe_start();
    dump_frame();
    zelda_frame_probe_note(f, "maintenance", z_dump);
    zelda_frame_probe z_tail = zelda_frame_probe_start();''',
        '    uint64_t period = present_period(o), now = dm_detclock_enabled() ? guest_ns() : now_ns();':
        '    zelda_frame_probe_note(f, "frame-tail", z_tail);\n    uint64_t period = present_period(o), now = dm_detclock_enabled() ? guest_ns() : now_ns();',
        '    if (o->presents > 9) { uint64_t n = o->presents - 8;':
        '    zelda_frame_probe z_feedback = zelda_frame_probe_start();\n    if (o->presents > 9) { uint64_t n = o->presents - 8;',
        '    prev_out = now_ns();':
        '    zelda_frame_probe_note(f, "present-feedback", z_feedback);\n    prev_out = now_ns();',
        '__attribute__((weak)) double dm_brightness_now(void) { return -1; }':
        '''__attribute__((weak)) double dm_brightness_now(void) { return -1; }
__attribute__((weak)) unsigned zelda_game_frame_rate(void) { return 0; }
__attribute__((weak)) void zelda_perf_present(uint64_t f, double t, double gpu, uint64_t cpu, uint64_t draws, uint64_t upload, double rendered_at, double pacing_fps, int forced) {
    (void)f; (void)t; (void)gpu; (void)cpu; (void)draws; (void)upload; (void)rendered_at; (void)pacing_fps; (void)forced;
}''',
        '        if (a_ > 0) trow(f - 8, "@pres_at_ms", a_ * 1e3 - g_t0_ns / 1e6);':
        '''        zelda_perf_present(f - 8, a_, mtl_gpu_ms(f - 8), dm_nvn_sub_cpu_ns(), dr, mtl_upload_bytes(), t0 / 1e9, period ? 1e9 / period : 0, g_display_mode || g_hz != 60);
        if (a_ > 0) trow(f - 8, "@pres_at_ms", a_ * 1e3 - g_t0_ns / 1e6);''',
        '(p_draw){pr, first, n}': '(p_draw){pr, first, n, 0, 1}',
        # ZELDA_RT_POLICY=0 (diagnostic): leave the acquire / translation threads at their normal scheduling.
        '    static mach_timebase_info_data_t tb; if (!tb.denom) mach_timebase_info(&tb);\n    if (comp_ns < 1000000ull)':
        '''    { static int off = -1; if (off < 0) { const char *e = getenv("ZELDA_RT_POLICY"); off = e && !atoi(e); if (off) LOG("[nvn] ZELDA_RT_POLICY=0: time-constraint policy disabled"); } if (off) return; }
    static mach_timebase_info_data_t tb; if (!tb.denom) mach_timebase_info(&tb);
    if (comp_ns < 1000000ull)''',
        '    if (s) { ((sync_t *)s)->fenced = 1; ((sync_t *)s)->fence = x_fence(); }':
        '''    // Acquire depends on this image's previous use, not the newest unrelated frame.
    if (s) { ((sync_t *)s)->fenced = 1; ((sync_t *)s)->fence = *idx < 4 ? o->render_fence[*idx] : mtl_last_fence(); }''',
        '        o->ticket[idx] = x_push(&j); }':
        '''        o->render_fence[idx] = mtl_last_fence();
        zelda_timeline(TL_PRESENT, zelda_timeline_now(), (uint64_t)idx, o->render_fence[idx]);
        o->ticket[idx] = x_push(&j); }''',
        'static const tex_t *snap_tex(const NVNtexture *t) {\n    tex_t *o = TX(t); if (!o || !o->serial) return o;   // \u6ca1\u521d\u59cb\u5316\u8fc7\u7684\u5bf9\u8c61\u6ca1\u6709\u53ef\u7559\u7684\u5185\u5bb9，\u7167\u65e7\u5b58\u539f\u5740 | An object that was never initialized has no content to keep; store the original address as before\n    const tex_t *s = __atomic_load_n((const tex_t **)&o->snap, __ATOMIC_ACQUIRE);\n    if (s && s->serial == o->serial) return s;\n    pthread_mutex_lock(&g_snap_mu);\n    s = o->snap;\n    if (!s || s->serial != o->serial) { snap_slot *n = snap_alloc(); n->t = *o; n->t.snap = NULL; s = &n->t; __atomic_store_n((const tex_t **)&o->snap, s, __ATOMIC_RELEASE); }\n    pthread_mutex_unlock(&g_snap_mu);\n    return s;\n}\n':
        'static const tex_t *snap_tex(const NVNtexture *t) { return TX(t); }   // Zelda: recorded by address (nvn_obj.h adaptation)\n',
        'NVN(nvnTextureFinalize, void, NVNtexture *t) { snap_drop(&TX(t)->snap, TX(t)->serial, offsetof(tex_t, serial)); mtl_tex_release(TX(t));':
        'NVN(nvnTextureFinalize, void, NVNtexture *t) { mtl_tex_release(TX(t));',
        'NVN(nvnProgramInitialize, NVNboolean, NVNprogram *p, NVNdevice *d) {':
        '#include "nvn_shader_capture.inc"\nNVN(nvnProgramInitialize, NVNboolean, NVNprogram *p, NVNdevice *d) {',
        '    mtl_host_apply(host);\n    uint64_t t0 = now_ns(),':
        '    mtl_host_apply(host); uint64_t z_timeline = zelda_timeline_now();\n    uint64_t t0 = now_ns(),',
        '    uint64_t f = mtl_sub_commit();\n    if (f != sub) {':
        '''    uint64_t f = mtl_sub_commit();
    zelda_timeline(TL_SUBMIT, z_timeline, (uint64_t)n, f);
    // Partial submits (nvn_submit.inc) commit several Metal fences per game submit, so fences are not submit counts.
    if (0 && f != sub) {''',
        # Translation stays on the submitting thread: partial submits and the game fences built on Metal fences, the
        # dirty tracker, input script and timeline all assume in-place translation (DM_NVN_SYNC=0 is not supported yet).
        '    const char *e = getenv("DM_NVN_SYNC"); g_x_sync = e && atoi(e);':
        '    const char *e = getenv("DM_NVN_SYNC"); g_x_sync = 1; if (e && !atoi(e)) LOG("[nvn] DM_NVN_SYNC=0 ignored: Zelda translates in place");',
        '    return mtl_wait(o->fence, timeout) ? NVN_SYNC_WAIT_RESULT_CONDITION_SATISFIED : NVN_SYNC_WAIT_RESULT_TIMEOUT_EXPIRED;':
        '''    uint64_t z_timeline = zelda_timeline_now(), z_latest = mtl_last_fence();
    int z_satisfied = mtl_wait(o->fence, timeout);
    zelda_timeline(TL_SYNC_WAIT, z_timeline, o->fence, z_latest);
    return z_satisfied ? NVN_SYNC_WAIT_RESULT_CONDITION_SATISFIED : NVN_SYNC_WAIT_RESULT_TIMEOUT_EXPIRED;''',
        '(void)q; (void)c; (void)f; ((sync_t *)s)->fenced = 1; ((sync_t *)s)->fence = x_fence(); }':
        '(void)q; (void)c; (void)f; ((sync_t *)s)->fenced = 1; ((sync_t *)s)->fence = mtl_last_fence(); zelda_timeline(TL_FENCE, zelda_timeline_now(), ((sync_t *)s)->fence, 0); }',
        'wait = at > now ? at - now : 0;\n': 'wait = at > now ? at - now : 0;\n    zelda_timeline(TL_ACQUIRE, zelda_timeline_now(), (uint64_t)*idx, wait);\n',
        'NVN(nvnTextureInitialize, NVNboolean, NVNtexture *t, const NVNtextureBuilder *b) {':
        '#include "nvn_packaged.inc"\nNVN(nvnTextureInitialize, NVNboolean, NVNtexture *t, const NVNtextureBuilder *b) {',
        '    o->serial = nvn_new_serial();':
        '''    if (!zelda_packaged_storage_valid(s, o)) die("Packaged texture data does not match mapped storage");
    o->serial = nvn_new_serial();''',
        '    return 0;\n}\n\ntypedef struct { const char *name; void *fn; } nvn_impl;':
        '    exit(78);\n}\n\ntypedef struct { const char *name; void *fn; } nvn_impl;',
        '        nvn_slot[i].fn = (void *)nvn_nop;':
        '        if (strstr(g_nops[k], "Debug")) nvn_slot[i].fn = (void *)nvn_nop;',
        '    unsigned nnop = sizeof g_nops / sizeof *g_nops;': '    unsigned nnop = 3;',
        '    "nvnCommandBufferSetShaderScratchMemory",\n': '',
        '    "nvnCommandBufferSetDepthBounds",\n': '',
        'static void *nvn_lookup(const char *name) {': extra + '\nstatic void *nvn_lookup(const char *name) {\n' + lookup,
        '    [0x00] = 53, [0x01] = 311,': '    [0x00] = 55, [0x01] = 15,',
        '    o->cur = start; o->end = end;': '    o->cur = start; o->end = end; o->memory_start = start; o->command_callback_pending = 0;',
        '    o->recording = 1;': '    o->recording = 1; o->begin = NULL;',
        'if (o->recording && o->cur)': 'if (o->recording && o->begin && o->cur)',
        '        o->cb((NVNcommandBuffer *)o, NVN_COMMAND_BUFFER_MEMORY_EVENT_OUT_OF_COMMAND_MEMORY, size + OP_RESERVE, o->cb_data);':
        '''        // The callback can reuse the old arena tail immediately after querying its consumed bytes.
        o->command_callback_pending = o->recording && o->begin && o->cur;
        o->cb((NVNcommandBuffer *)o, NVN_COMMAND_BUFFER_MEMORY_EVENT_OUT_OF_COMMAND_MEMORY, size + OP_RESERVE, o->cb_data);
        o->command_callback_pending = 0;''',
        '        if (!o->cb || tries == 2)':
        '''        if (o->command_callback_disabled) {
            if ((uint64_t)size + OP_RESERVE > HOSTSEG_SZ) die("NVN record exceeds host overflow chunk");
            size_t extent = ((size_t)size + OP_RESERVE + 4095) & ~(size_t)4095;
            cmd_host_segment(o, extent); continue;
        }
        if (o->cb && tries == 2) {
            // The game's budget is sized for hardware NVN commands; ours are larger (v1.4.3 movie frames exhaust it
            // with an 8-byte record and a 200-byte tail grant). Keep recording in host overflow memory.
            static unsigned spills; static uint64_t spill_bytes;
            size_t extent = ((size_t)size + OP_RESERVE + 4095) & ~(size_t)4095;
            unsigned n = __atomic_add_fetch(&spills, 1, __ATOMIC_RELAXED);
            uint64_t total = __atomic_add_fetch(&spill_bytes, extent, __ATOMIC_RELAXED);
            if (n == 1 || !(n & (n - 1)) || !(n % 256))
                LOG("NVN command memory still short after the game's callback: record=%u granted=%zu; host overflow %u times, %.1f KiB",
                    size, o->guest_size, n, total / 1024.0);
            cmd_host_segment(o, extent); continue;
        }
        if (!o->cb || tries == 2)''',
        'cmd_new_segment(CB(c), hostseg_take(), HOSTSEG_SZ);':
        'cmd_host_segment(CB(c), HOSTSEG_SZ);',
        '    cmd_new_segment(CB(c), pl->mem + off, n);':
        '''    if (off < 0 || (uint64_t)off > pl->size) die("NVN command memory offset");
    // main+0xbb4cc requests at least 512 bytes even at the pool tail; larger host records spill outside that arena.
    if (n > pl->size - (uint64_t)off) n = pl->size - (uint64_t)off;
    if (n < 24) die("NVN command memory tail cannot hold a jump");
    cmd_new_segment(CB(c), pl->mem + off, n);
    CB(c)->guest_memory = pl->mem + off; CB(c)->guest_size = n; CB(c)->guest_used = 0; CB(c)->guest_active = 1;''',
        'VW(v)->set_levels = 1;':
        'if (base < 0 || base > 31 || n < -1 || n > 32) die("NVN texture view mip range"); VW(v)->set_levels = 1;',
        'VW(v)->set_format = 1;':
        'if ((unsigned)f > 255) die("NVN texture view format range"); VW(v)->set_format = 1;',
        'VW(v)->set_target = 1;':
        'if ((unsigned)t > 10) die("NVN texture view target range"); VW(v)->set_target = 1;',
        'NVN(nvnCommandBufferAddControlMemory, void, NVNcommandBuffer *c, void *m, size_t n) { (void)c; (void)m; (void)n; }':
        'NVN(nvnCommandBufferAddControlMemory, void, NVNcommandBuffer *c, void *m, size_t n) { (void)m; CB(c)->control_size = n; }',
        'o->has_layout = s->has_layout; memcpy(o->layout, s->layout, 8);':
        'o->has_layout = s->has_layout; memcpy(o->layout, s->layout, 8); o->swizzle = s->swizzle;',
        'NVN(nvnMemoryPoolBuilderSetDefaults, void, NVNmemoryPoolBuilder *b)':
        '#include "nvn_virtual.inc"\nNVN(nvnMemoryPoolBuilderSetDefaults, void, NVNmemoryPoolBuilder *b)',
        'o->magic = MAGIC(\'P\', \'O\', \'O\', \'L\'); o->flags = s->flags; o->mem = s->mem; o->size = s->size;':
        'o->magic = MAGIC(\'P\', \'O\', \'O\', \'L\'); o->flags = s->flags; o->mem = s->mem; o->size = s->size; o->physical = NULL;\n    if (o->flags & 0x200) return virtual_pool_initialize(o);',
        'if (((pool_t *)p)->mem) { nvn_dirty_remove_range':
        'if (((pool_t *)p)->flags & 0x200) virtual_pool_finalize((pool_t *)p); else if (((pool_t *)p)->mem) { nvn_dirty_remove_range',
        'o->mem = s->pool && s->pool->mem ? s->pool->mem + s->off : NULL;':
        'o->mem = s->pool && s->pool->mem ? (s->pool->physical ?: s->pool->mem) + s->off : NULL;',
        'o->nstage = n; memcpy(o->sd, sd, n * sizeof *sd); o->serial = nvn_new_serial();':
        '''o->nstage = n; memcpy(o->sd, sd, n * sizeof *sd); o->serial = nvn_new_serial();
    zelda_capture_program(sd, n);
    mtl_prog_prefetch(sd, n);
    if (g_log) for (int i = 0; i < n; i++) {
        const uint32_t *ctl = sd[i].control;
        LOG("SHADER stage=%u control_bytes=%u code_bytes=%u offset=%u has_header=%d", ctl[0x714/4], ctl[8],
            ctl[0x704/4], ctl[0x708/4], *(const uint32_t *)(uintptr_t)sd[i].data == (ctl[0x714/4] == 5 ? 0x12345679u : 0x12345678u));
    }''',
    }
    for original, replacement in replacements.items():
        if source.count(original) != 1:
            raise ValueError('NVN upstream changed; inspect the adapter before building')
        source = source.replace(original, replacement)
    # The same version-bound target drives elapsed game time and presentation; movies stay at 30.
    start, end = source.index('// \u51fa\u56fe\u56fa\u5b9a 60 Hz'), source.index('int (*g_nvn_loadscreen_up)(void);')
    source = source[:start] + '''static int g_hz = -1, g_display_mode;
void nvn_present_config(int hz, int display_mode) { g_hz = hz; g_display_mode = display_mode; }
static void present_cfg(const win_t *o) {
    if (g_hz >= 0) return;
    const char *e = getenv("DM_PRESENT_HZ"), *m = getenv("DM_PRESENT_INTERVAL");
    g_hz = e ? atoi(e) : 60;
    g_display_mode = m && !strcmp(m, "display");
    LOG("[nvn] Zelda pacing %d Hz, interval %s, requested %d", !g_display_mode && g_hz == 60 && zelda_game_frame_rate() ? (int)zelda_game_frame_rate() : g_hz,
        g_display_mode ? "display (profiling only)" : zelda_game_frame_rate() ? "timing patch / movie 30" : "game", o->interval);
    if (g_display_mode || g_hz != 60) LOG("[nvn] profiling override: Zelda game-speed correctness is unverified");
}
static uint64_t present_period(const win_t *o) {
    present_cfg(o);
    unsigned target = zelda_game_frame_rate();
    if (target && !g_display_mode && g_hz == 60) return 1000000000ull / target;
    int itv = g_display_mode ? 1 : o->interval > 0 ? o->interval : 1;
    return g_hz > 0 ? (uint64_t)itv * 1000000000ull / (uint64_t)g_hz : 0;
}
''' + source[end:]
    start, end = source.index('#define HOSTSEG_SZ'), source.index('NVN(nvnCommandBufferAddCommandMemory,')
    source = source[:start] + '#include "nvn_command_memory.inc"\n' + source[end:]
    return '#include "timeline.h"\n' + source + '\n#include "nvn_extra.inc"\n'


def adapt_glue(source):
    finalizer = 'HLE_FORCE("_ZN2nv16FinalizeGraphicsEv", WHY, void, h_nv_Fin, void) {}\n'
    if source.count(finalizer) != 1:
        raise ValueError('NV glue upstream changed')
    return source.replace(finalizer, '').replace('#include "../host/hle.h"', '#include "hle.h"')


def adapt_objects(source):
    draw = 'typedef struct { int32_t prim, first, count; } p_draw;'
    if source.count(draw) != 1:
        raise ValueError('NVN array draw record changed')
    source = source.replace(draw, 'typedef struct { int32_t prim, first, count, base_instance, instances; } p_draw;')
    opcode = 'OP_CLEAR_TEX, OP_COUNT_'
    if source.count(opcode) != 1:
        raise ValueError('NVN command opcode layout changed')
    source = source.replace(opcode, 'OP_CLEAR_TEX, OP_RENDER_ENABLE, OP_COUNT_')
    source += '''
static inline int nvn_command_valid(const void *record, op_hdr header) {
    return !((uintptr_t)record & 7) && header.size >= 8 && !(header.size & 7) && header.op < OP_COUNT_;
}
typedef struct { uint64_t address; int32_t mode, enabled; } p_render_enable;
static inline int nvn_render_test(const p_render_enable *r, const uint64_t counters[4]) {
    return !r->address ? r->enabled : ((counters[0] == counters[2]) == (r->mode == 0));
}
'''
    original = 'uint32_t magic; nvn_texdesc d; nvn_texlayout L; uint8_t *mem; pool_t *pool; int64_t off; uint8_t has_layout, layout[8];'
    replacement = 'uint32_t magic; uint8_t has_layout, layout[8]; nvn_texdesc d; nvn_texlayout L; uint8_t *mem; pool_t *pool; int64_t off;'
    if source.count(original) != 1:
        raise ValueError('NVN texture layout changed')
    # Zelda allocates 0x80 bytes; moving the byte fields into padding preserves every field and value copies.
    source = source.replace(original, replacement)
    # No room for upstream's per-serial snapshot address: Zelda translates in place (hle_nvn.c x_start), so recorded
    # texture pointers keep naming the game object as before the translation thread existed.
    snapshot = '\n    const void *snap;                    // \u672c\u5e8f\u53f7\u7684\u4e0d\u53ef\u53d8\u526f\u672c（hle_nvn.c snap_tex）：\u547d\u4ee4\u8f7d\u8377\u5b58\u5b83，\u4e0d\u5b58\u6e38\u620f\u5bf9\u8c61\u5730\u5740 | This serial\'s immutable snapshot (hle_nvn.c snap_tex): command payloads store it, not the game object\'s address\n} tex_t;'
    if source.count(snapshot) != 1:
        raise ValueError('NVN texture snapshot field changed')
    source = source.replace(snapshot, '\n} tex_t;')
    pool = 'uint8_t *mem; uint64_t size; } pool_t;'
    command = '} cmd_t;'
    if source.count(pool) != 1 or source.count(command) != 1:
        raise ValueError('NVN pool/command object layout changed')
    source = source.replace(pool, 'uint8_t *mem; uint64_t size; uint8_t *physical; } pool_t;')
    source = source.replace(command, 'uint8_t *memory_start, *guest_memory; size_t control_size, guest_size, guest_used; uint8_t command_callback_disabled, guest_active, command_callback_pending; } cmd_t;')
    cursors = 'uint8_t *cur, *end;'
    if source.count(cursors) != 1:
        raise ValueError('NVN command buffer cursor layout changed')
    # Zelda writes its inline-command dirty flag at object+0x10 even when the fast path is disabled.
    source = source.replace(cursors, 'uint8_t *cur; uint64_t guest_flags; uint8_t *end;')
    source += '\n_Static_assert(offsetof(cmd_t, guest_flags) == 0x10, "Zelda command dirty flag ABI");\n'
    handles = 'typedef struct { int32_t stage, first, n; uint64_t h[32]; } p_bind_handles;'
    if source.count(handles) != 1:
        raise ValueError('NVN binding record layout changed')
    source = source.replace(handles, '''typedef struct { int32_t stage, first, n; const uint64_t *deferred; uint64_t h[32]; } p_bind_handles;
static inline const uint64_t *nvn_bound_handles(const p_bind_handles *binding) {
    return binding->deferred ? binding->deferred : binding->h;
}''')
    view = 'typedef struct { uint8_t set_levels, set_layers, set_format, set_target; int32_t base_level, nlevels, min_layer, nlayers, format, target; } view_t;'
    if source.count(view) != 1:
        raise ValueError('NVN texture view layout changed')
    # main+0x52f668 passes a 16-byte stack view immediately before an owning array.
    source = source.replace(view, '''typedef struct {
    uint8_t set_levels : 1, set_layers : 1, set_format : 1, set_target : 1;
    int8_t base_level, nlevels;
    uint8_t format, target;
    uint16_t swizzle;
    int32_t min_layer, nlayers;
} view_t;
_Static_assert(sizeof(view_t) == 16, "Zelda NVNtextureView is 16 bytes");''')
    if source.count('uint8_t has_layout, layout[8];') != 2:
        raise ValueError('NVN builder/texture byte fields changed')
    if source.count('uint64_t presents, avail[4], pace, ticket[4];') != 1:
        raise ValueError('NVN window layout changed')
    source = source.replace('uint64_t presents, avail[4], pace, ticket[4];',
                            'uint64_t presents, avail[4], pace, ticket[4], render_fence[4];')
    source = source.replace('} tex_builder_t;', '    const void *packaged_data;\n} tex_builder_t;')
    source += '\nuint64_t nvn_canonical_address(uint64_t address, uint64_t length);\n'
    return source.replace('uint8_t has_layout, layout[8];', 'uint8_t has_layout, layout[8]; uint16_t swizzle;') + '\n_Static_assert(sizeof(tex_t) <= 0x80, "Zelda NVNtexture is 128 bytes");\n'


def adapt_backend(source):
    cpu_texture = '''t->mem && t->d.target != NVN_TEXTURE_TARGET_BUFFER &&
            (t->has_layout || (t->pool && (t->pool->flags &
                (NVN_MEMORY_POOL_FLAGS_CPU_CACHED | NVN_MEMORY_POOL_FLAGS_CPU_UNCACHED))))'''
    replacements = {
        'static void c1_store(NvShader *o) {':
        '#include "nvn_c1_storage.inc"\nstatic void c1_store(NvShader *o) {',
        'if (cur == v || v.parentTexture == cur)': 'if (zelda_texture_view_current(v, cur))',
        '''        o->c1buf = [g_dev newBufferWithLength:C1_DECL_SIZE options:MTLResourceStorageModeShared]; o->c1_gpu = o->c1buf.gpuAddress;
        if (!o->c1buf) { os_unfair_lock_unlock(&g_c1_lock); MDIE("c1 \u72ec\u5360\u7f13\u51b2\u5206\u914d\u5931\u8d25"); }
        res_add(o->c1buf);
        memset(o->c1buf.contents, 0, C1_DECL_SIZE); memcpy(o->c1buf.contents, o->rf.c1, len);
        g_c1_alloc_bytes += C1_DECL_SIZE; g_c1_data_bytes += len; g_c1_count++; g_c1_exclusive++;''':
        '''        o->c1buf = zelda_c1_buffer(o->rf.c1, len); o->c1_gpu = o->c1buf.gpuAddress;
        g_c1_data_bytes += len; g_c1_count++;''',
        '\u5168\u90e8 c1 \u72ec\u5360 64 KiB': 'c1 preserves the full 64 KiB read range (content sharing enabled)',
        'static void res_add(id<MTLResource> r) {\n    if (!r) return;\n    pthread_mutex_lock(&g_res_mu);\n    [g_res addAllocation:(id<MTLAllocation>)r];\n    if (!g_res_objects) g_res_objects = [NSHashTable weakObjectsHashTable];\n    [g_res_objects addObject:r];\n#if DM_NVN_M3\n    if ([r conformsToProtocol:@protocol(MTLBuffer)]) m3_buf_add((id<MTLBuffer>)r);\n#endif\n    if (g_res_census < 0) g_res_census = getenv("DM_RES_CENSUS") != NULL;\n    if (g_res_census > 0) { if (!g_res_ever) g_res_ever = [NSHashTable weakObjectsHashTable]; [g_res_ever addObject:r]; }\n    g_res_dirty = 1;\n    pthread_mutex_unlock(&g_res_mu);\n}\nstatic void res_remove(id<MTLResource> r) {\n    if (!r) return;\n    pthread_mutex_lock(&g_res_mu);\n    [g_res removeAllocation:(id<MTLAllocation>)r];\n    [g_res_objects removeObject:r];\n#if DM_NVN_M3\n    m3_buf_del(r);\n#endif\n    g_res_dirty = 1;\n    pthread_mutex_unlock(&g_res_mu);\n}\n': '#include "nvn_residency.inc"\n',
        '#define PRING 1024': '#include "nvn_present.h"\n#define PRING 1024',
        'static inline void enc_b_copy_buffer(id e, id<MTLBuffer> s, NSUInteger so, id<MTLBuffer> d, NSUInteger doff, NSUInteger n) { [e copyFromBuffer:s sourceOffset:so toBuffer:d destinationOffset:doff size:n]; }':
        '#include "nvn_buffer_copy.inc"',
        'static id<MTLBuffer> g_up_block;': '#include "nvn_upload_pool.h"\nstatic id<MTLBuffer> g_up_block;',
        'if (g_up_block) { defer_obj_release(g_up_block); g_up_block = nil; }':
        'if (g_up_block) { if (!zelda_upload_is_pooled(g_up_block)) defer_obj_release(g_up_block); g_up_block = nil; }',
        '    for (id obj in g_up_keep) defer_obj_release(obj);':
        '    for (id obj in g_up_keep) if (!zelda_upload_is_pooled(obj)) defer_obj_release(obj);',
        'static id<MTLBuffer> up_alloc(size_t n, size_t *off) {':
        '''static id<MTLBuffer> up_alloc(size_t n, size_t *off) {
    if (!g_up_block && n <= (4u << 20)) {
        if (!g_up_cb || !g_up_seq) MDIE("Upload buffer requested outside an upload batch");
        g_up_block = zelda_upload_buffer((g_up_seq - 1) % 3, n);
        g_up_block_size = g_up_block.length; g_up_block_off = 0;
    }''',
        '(unsigned long long)g_up_blocks_new, (unsigned long long)g_up_swaps':
        '(unsigned long long)(g_up_blocks_new + z_upload_buffers_created), (unsigned long long)g_up_swaps',
        '@interface NvPkg : NSObject { @public id<MTLTexture> tex; uint64_t stamp, seen, hash; } @end':
        '@interface NvPkg : NSObject { @public id<MTLTexture> tex; uint64_t stamp, seen, hash; NSData *small_bytes, *storage_key; id<MTLBuffer> storage_pool; const uint8_t *z_alias; uint8_t z_alias_known, z_alias_bad; NSData *z_rendered_pages; uint64_t z_written_frame, z_spell_frame, z_writeback_fence; uint8_t z_writeback_queued; } @end\n#include "nvn_texture_dirty.inc"',
        'static id<MTLTexture> pkg_upload_(const tex_t *t, uint64_t *stamp);':
        '#include "nvn_texture_reuse.inc"\nstatic id<MTLTexture> pkg_upload_(const tex_t *t, uint64_t *stamp);',
        'static id<MTLTexture> pkg_upload_(const tex_t *t, uint64_t *stamp) {\n    MTLTextureDescriptor *d = tex_desc(t, 1);\n    if (!d) return nil;\n    id<MTLTexture> mt = [g_dev newTextureWithDescriptor:d];':
        '''static id<MTLTexture> pkg_upload_(const tex_t *t, uint64_t *stamp) {
    MTLTextureDescriptor *d = tex_desc(t, 1);
    if (!d) return nil;
    id<MTLTexture> mt = zelda_texture_reuse(d) ?: [g_dev newTextureWithDescriptor:d];''',
        'id<MTLTexture> old = pk->tex; pk->tex = nt; defer_obj_release(old);':
        'id<MTLTexture> old = pk->tex; pk->tex = nt; zelda_texture_retire(old);',
        '    out->dss = g_dss.n;':
        '''    out->dss = g_dss.n;
    MLOG("Uniform snapshots total=%llu merged_deferred_writes=%llu", (unsigned long long)z_uniform_snapshots, (unsigned long long)z_uniform_deferred);
    MLOG("Cube batches=%llu draws=%llu", (unsigned long long)z_cube_passes, (unsigned long long)z_cube_draws);
    MLOG("Framebuffer-fetch draws=%llu variants=%llu", (unsigned long long)z_fetch_draws, (unsigned long long)z_fetch_variants);
    MLOG("Conditional clears: joined=%llu deferred=%llu new_passes=%llu standalone=%llu query_read_flushes=%llu",
        (unsigned long long)z_color_clear_joined, (unsigned long long)z_color_clear_deferred, (unsigned long long)z_color_clear_flushed,
        (unsigned long long)z_color_clear_alone, (unsigned long long)z_query_read_flushes);
    MLOG("Inline uniforms deferred=%llu snapshot_draws=%llu flush_batches=%llu", (unsigned long long)z_inline_deferred,
        (unsigned long long)z_inline_snapshots, (unsigned long long)z_inline_flushes);
    MLOG("8-bit indices: expanded=%llu reused=%llu", (unsigned long long)z_index_expansions, (unsigned long long)z_index_hits);
    MLOG("Shader prefetch: compiled=%llu waits=%llu wait_ms=%.1f compiled_before_worker=%llu", (unsigned long long)z_prefetch_compiled,
        (unsigned long long)z_prefetch_waits, z_prefetch_wait_ns / 1e6, (unsigned long long)z_prefetch_started_late);''',
        '        heap_mb, heap_free_mb, heap_maxblk_kb, heap_ok ? "" : "（\u672a\u8bfb\u5230）", avail);':
        '''        heap_mb, heap_free_mb, heap_maxblk_kb, heap_ok ? "" : "（\u672a\u8bfb\u5230）", avail);
    MLOG("Upload texture reuse through frame %llu: hits=%llu retained=%llu KiB / 16384 KiB", frame,
        (unsigned long long)z_texture_reuse_hits, (unsigned long long)(z_texture_reuse_bytes >> 10));''',
        'static id<MTLTexture> tex_mtl(const tex_t *ct) {':
        '#include "nvn_small_texture.inc"\n#include "nvn_texture_alias.inc"\n#include "nvn_texture_rendered.inc"\nstatic id<MTLTexture> tex_mtl(const tex_t *ct) {',
        'static inline __attribute__((always_inline)) void *tex_fast(int tid) {':
        '#include "nvn_texture_lookup.inc"\nstatic inline __attribute__((always_inline)) void *tex_fast(int tid) {',
        '    return (__bridge void *)tex_by_id(tid);':
        '    void *cached; if (zelda_cached_texture(tid, &cached)) return cached;\n    return (__bridge void *)tex_by_id(tid);',
        '    id<MTLTexture> t = tex_by_id(tid); if (out) *out = (__bridge void *)t; return t ? t.gpuResourceID._impl : 0;':
        '    void *cached; if (zelda_cached_texture(tid, &cached)) { if (out) *out = cached; return g_treg[tid].view_rid; }\n'
        '    id<MTLTexture> t = tex_by_id(tid); if (out) *out = (__bridge void *)t; return t ? t.gpuResourceID._impl : 0;',
        '        NvPkg *pk = have;': '        NvPkg *pk = have; int z_upload_lost = 0;',
        'if (clean && atomic_load_explicit(&g_up_lost_n, memory_order_relaxed) && up_lost_take(pk->tex))':
        'if (atomic_load_explicit(&g_up_lost_n, memory_order_relaxed) && up_lost_take(pk->tex))',
        'clean = 0; uint64_t k = atomic_load(&g_up_lost_redo);':
        'clean = 0; z_upload_lost = 1; uint64_t k = atomic_load(&g_up_lost_redo);',
        '            uint64_t st; id<MTLTexture> nt = pkg_upload(t, &st);':
        '            if (zelda_rendered_texture_unchanged(pk, t, z_upload_lost)) return pk->tex;\n'
        '            if (zelda_small_texture_unchanged(pk, t, z_upload_lost)) return pk->tex;\n            uint64_t st; id<MTLTexture> nt = pkg_upload(t, &st);',
        'pk->stamp = st; pk->seen = nvn_dirty_now(); g_pkg_reuploads++;':
        'pk->stamp = st; pk->seen = st; zelda_reuploaded_texture_snapshot(pk, t); g_pkg_reuploads++;',
        '        pk->seen = nvn_dirty_now(); g_pkg_uploads++;':
        '        pk->seen = pk->stamp; zelda_small_texture_snapshot(pk, t); zelda_packaged_register(t, pk); g_pkg_uploads++;',
        # Packaged objects over identical storage share one holder (nvn_texture_rendered.inc).
        '        pk = [NvPkg new];':
        '        if ((pk = zelda_packaged_shared_holder(t))) { serial_put(t->serial, pk); return tex_mtl(t); }\n        pk = [NvPkg new];',
        # Pool lookups use a sorted index instead of scanning every pool (nvn_pool_index.inc).
        'void mtl_pool_add(const void *mem, uint64_t size) {': '#include "nvn_pool_index.inc"\nvoid mtl_pool_add(const void *mem, uint64_t size) {',
        '    g_pool_adds++;\n': '    g_pool_adds++; z_pool_sorted_dirty = 1;\n',
        'g_pools[i] = g_pools[--g_npool]; break; }': 'g_pools[i] = g_pools[--g_npool]; z_pool_sorted_dirty = 1; break; }',
        'int mtl_addr(uint64_t addr, uint64_t len, void **buf, uint64_t *off) {\n    int ok = 0;\n    pthread_mutex_lock(&g_mu);\n    for (int i = 0; i < g_npool; i++)\n        if (addr >= g_pools[i].lo && addr + len <= g_pools[i].hi) {':
        'int mtl_addr(uint64_t addr, uint64_t len, void **buf, uint64_t *off) {\n    int ok = 0;\n    pthread_mutex_lock(&g_mu);\n    for (int i = zelda_pool_find(addr, len); i >= 0; i = -1) {',
        'int mtl_gpu_addr(uint64_t addr, uint64_t len, void **buf, uint64_t *off, uint64_t *gpu) {\n    int ok = 0;\n    pthread_mutex_lock(&g_mu);\n    for (int i = 0; i < g_npool; i++)\n        if (addr >= g_pools[i].lo && addr + len <= g_pools[i].hi) {':
        'int mtl_gpu_addr(uint64_t addr, uint64_t len, void **buf, uint64_t *off, uint64_t *gpu) {\n    int ok = 0;\n    pthread_mutex_lock(&g_mu);\n    for (int i = zelda_pool_find(addr, len); i >= 0; i = -1) {',
        # Virtual-pool textures are tracked through both mappings (nvn_texture_dirty.inc).
        '    nvn_dirty_protect(t->mem, t->L.size);\n    *stamp = nvn_dirty_now();':
        '    zelda_texture_protect(t);\n    *stamp = nvn_dirty_now();',
        '            int tracked = nvn_dirty_tracked(t->mem, t->L.size);\n            int clean = tracked && (now == pk->seen || !nvn_dirty_since(t->mem, t->L.size, pk->stamp));':
        '            int tracked = zelda_texture_tracked(pk, t);\n            int clean = tracked && (now == pk->seen || !zelda_texture_dirty_since(pk, t, pk->stamp));',
        'static _Atomic uint64_t g_reg_gen = 1;':
        'static _Atomic uint64_t z_pool_generation = 1;\nstatic _Atomic uint64_t g_reg_gen = 1;',
        '    mtl_init(); g_reg_gen++;': '    mtl_init(); g_reg_gen++; z_pool_generation++;',
        'void mtl_pool_del(const void *mem) {\n    g_reg_gen++;':
        'void mtl_pool_del(const void *mem) {\n    g_reg_gen++; z_pool_generation++;',
        # The input script's "dump N" opens an N-frame window at the frame where the backend next checks.
        '    long f = (long)atomic_load(&g_cur_frame); return a0 >= 0 && f >= a0 && f <= a1;':
        '    long f = (long)atomic_load(&g_cur_frame); unsigned z_dump = zelda_passdump_take();\n'
        '    if (z_dump) { a0 = f; a1 = f + (long)z_dump - 1; }\n'
        '    return a0 >= 0 && f >= a0 && f <= a1;',
        'static NvShader *shader_get(NSString *pc, int stage) {':
        '#include "nvn_shader_prefetch.inc"\nstatic NvShader *shader_get(NSString *pc, int stage) {',
        'uint64_t bt = bp_now(); o = shader_load(pc, stage);':
        'uint64_t bt = bp_now(); zelda_prefetch_wait(pc); o = shader_load(pc, stage);',
        # Metal restarts at 0xFFFF / 0xFFFFFFFF (8-bit indices are expanded to 16 bits, staying below 0x100).
        '        if (di->count) enc_r_draw_indexed(en, mp, di->count,':
        '''        if (S.restart && (di->itype == 0 ? S.restart_index <= 0xff : di->itype == 1 ? S.restart_index < 0xffff : S.restart_index != 0xffffffffu)) {
            MLOG("BLOCKED NVN primitive restart index=%#x with index type %d", S.restart_index, di->itype); exit(78);
        }
        if (di->count && z_conditional_gpu) {
            [(id<MTL4RenderCommandEncoder>)en drawIndexedPrimitives:mp indexType:di->itype == 1 ? MTLIndexTypeUInt16 : MTLIndexTypeUInt32
                indexBuffer:gpu indexBufferLength:(uint64_t)di->count * (di->itype == 1 ? 2 : 4) indirectBuffer:z_conditional_gpu];
        } else if (di->count) enc_r_draw_indexed(en, mp, di->count,''',
        'static id<MTLLibrary> lib_from_file(NSString *path, NSError **err) {':
        '#include "nvn_shader_math.h"\n#include "shader_prep.h"\nstatic id<MTLLibrary> lib_from_file(NSString *path, NSError **err) {',
        'if (len < 4 || memcmp(m, "MTLB", 4)) { NSString *src =':
        '''if (len < 4 || memcmp(m, "MTLB", 4)) {
        id<MTLLibrary> cached = shader_ir_load(g_dev, path, m, len, false);
        if (cached) { munmap(m, len); return cached; }
        NSString *src =''',
        'id<MTLLibrary> l = [g_dev newLibraryWithSource:src options:nil error:err];':
        'MTLCompileOptions *math_options = nil; src = zelda_shader_math(src, &math_options);\n        id<MTLLibrary> l = [g_dev newLibraryWithSource:src options:math_options error:err];',
        '    g_stat_draw++;':
        '''    zelda_buffer_targets_written();
    for (int stage = 0; stage < 5 && (z_index_count || z_uniform_count); stage++) if (pr->sh[stage])
        zelda_buffer_shader_written(&pr->sh[stage]->rf, pr->sh[stage]->stage);
    zelda_profile_draw(pr->sh[0] ? pr->sh[0]->key : NULL, pr->sh[1] ? pr->sh[1]->key : NULL);
    g_stat_draw++;''',
        'g_enc || !tbdr_on() ? 3 : pass_ds_need()':
        'g_enc || z_depth_clear.pending ? 3 : pass_ds_need()',
        '    ds_st ds = S.ds; g_ds_why = -1; g_ds_scanned = 0; g_ds_need = need;':
        '''    // The needed depth-only format has no stencil plane; normal mode also avoids splitting on later stencil state.
    int format = R.rt.has_dview && R.rt.dview.set_format ? R.rt.dview.format : R.rt.depth->d.format;
    if (need == 1 && (format == NVN_FORMAT_DEPTH16 || format == NVN_FORMAT_DEPTH24 || format == NVN_FORMAT_DEPTH32F)) return 3;
    ds_st ds = S.ds; g_ds_why = -1; g_ds_scanned = 0; g_ds_need = need;''',
        'g_rt_merged && g_enc_mode != 0 &&':
        'g_enc && g_enc_mode != 0 &&',
        '((S.ds.dt || S.ds.dw) && !g_pass.hd)':
        '(S.ds.dt && !g_pass.hd)',
        '(R.clear_d || S.ds.dt || S.ds.dw ? 1 : 0)':
        '(R.clear_d || S.ds.dt ? 1 : 0)',
        '(ds->dt || ds->dw ? 1 : 0)':
        '(ds->dt ? 1 : 0)',
        'memset(&k, 0, sizeof k); k.ds = S.ds;':
        '''memset(&k, 0, sizeof k);
    // Guest state padding is not part of the depth/stencil descriptor.
    k.ds.dt = S.ds.dt; k.ds.dw = S.ds.dw; k.ds.df = S.ds.df; k.ds.st = S.ds.st;
    k.ds.sf_f = S.ds.sf_f; k.ds.sf_b = S.ds.sf_b;
    k.ds.fail_f = S.ds.fail_f; k.ds.zfail_f = S.ds.zfail_f; k.ds.zpass_f = S.ds.zpass_f;
    k.ds.fail_b = S.ds.fail_b; k.ds.zfail_b = S.ds.zfail_b; k.ds.zpass_b = S.ds.zpass_b;''',
        'void pg_commit_ext(id<MTLCommandBuffer> cb) { pg_commit(cb); }': '',
        'static inline void m4_end(id e) {\n    if ([e conformsToProtocol:@protocol(MTL4CommandEncoder)])':
        '''static void zelda_profile_begin(id encoder, MTL4RenderPassDescriptor *descriptor);
static void zelda_profile_end(id encoder);
static dispatch_block_t zelda_profile_commit(void);
static inline void m4_end(id e) {
    zelda_profile_end(e);
    if ([e conformsToProtocol:@protocol(MTL4CommandEncoder)])''',
        '        rp_end(); if (g_blit) { enc_b_end(g_blit); g_blit = nil; }\n        id<MTLComputeCommandEncoder> ce = pg_compute();':
        '        id<MTLComputeCommandEncoder> ce = (id)zelda_dispatch_encoder();',
        '        enc_c_end(ce);\n        return;\n    }\n    const uint16_t *tg = cs->rf.tg;':
        '        zelda_dispatch_end();\n        return;\n    }\n    const uint16_t *tg = cs->rf.tg;',
        '    rp_end(); if (g_blit) { enc_b_end(g_blit); g_blit = nil; }\n    id<MTLComputeCommandEncoder> ce = pg_compute();\n    enc_c_pso(ce, ps);\n    encode_args(ce, cs, 5, 0);':
        '    id<MTLComputeCommandEncoder> ce = (id)zelda_dispatch_encoder();\n    enc_c_pso(ce, ps);\n    encode_args(ce, cs, 5, 0);',
        '    enc_c_end(ce);\n    bright_lut(cs);':
        '    zelda_dispatch_end();\n    bright_lut(cs);',
        '[e setArgumentTable:g_ft atStages:MTLRenderStageFragment]; return e; }':
        '[e setArgumentTable:g_ft atStages:MTLRenderStageFragment]; zelda_profile_begin(e, d); return e; }',
        '(void)e; [g_vt setAddress:gpu atIndex:i];':
        '(void)e; zelda_encoder_buffer(g_vt, 0, i, gpu);',
        '(void)e; [(s == MTLRenderStageVertex ? g_vt : g_ft) setAddress:gpu atIndex:i];':
        '(void)e; zelda_encoder_buffer(s == MTLRenderStageVertex ? g_vt : g_ft, s == MTLRenderStageVertex ? 0 : 1, i, gpu);',
        '[e setArgumentTable:g_ct]; return e; }':
        '[e setArgumentTable:g_ct]; zelda_profile_begin(e, nil); return e; }',
        'static inline id enc_b_begin(id cb) { return [cb respondsToSelector:@selector(beginCommandBufferWithAllocator:)] ? [cb computeCommandEncoder] : [cb blitCommandEncoder]; }':
        '''static inline id enc_b_begin(id cb) {
    id encoder = [cb respondsToSelector:@selector(beginCommandBufferWithAllocator:)] ? [cb computeCommandEncoder] : [cb blitCommandEncoder];
    if (cb == g_cb) zelda_profile_begin(encoder, nil);
    return encoder;
}''',
        '    NSArray<dispatch_block_t> *commit_blocks = [g_commit_blocks copy];':
        '    NSArray<dispatch_block_t> *commit_blocks = [g_commit_blocks copy];\n    dispatch_block_t profile = zelda_profile_commit();',
        '        gpu_tag_done(gpu_f, cb.GPUStartTime, cb.GPUEndTime);':
        '''        gpu_tag_done(gpu_f, cb.GPUStartTime, cb.GPUEndTime);
        zelda_timeline_span(TL_GPU, (uint64_t)(cb.GPUStartTime * 1e9), zelda_timeline_now(), fence, (uint64_t)(cb.GPUEndTime * 1e9));
        if (profile) profile();''',
        # A render-target change ends the pass exactly like a barrier does, so the GPU can start on finished slices there too.
        # A clear inside a continued pass ends it before the next pass is set up; slice there before the clear is recorded.
        '                if (g_rt_merged) rp_end();   // \u7eed\u7528\u7684\u901a\u9053\u91cc\u6765\u4e86\u6e05\u9664：\u5728\u6b64\u7ed3\u675f，\u6e05\u9664\u7167\u65e7\u6302\u5230\u4e0b\u4e00\u4e2a\u901a\u9053\u5f00\u5934 | A clear arrived in a continued render pass: end it here, and the clear is attached to the start of the next render pass as usual':
        '                if (g_rt_merged) { rp_end(); zelda_slice_between_passes(); }   // \u7eed\u7528\u7684\u901a\u9053\u91cc\u6765\u4e86\u6e05\u9664：\u5728\u6b64\u7ed3\u675f，\u6e05\u9664\u7167\u65e7\u6302\u5230\u4e0b\u4e00\u4e2a\u901a\u9053\u5f00\u5934 | A clear arrived in a continued render pass: end it here, and the clear is attached to the start of the next render pass as usual',
        '                if (g_rt_merged) rp_end();\n                if (g_enc) { RED_ONCE(7,':
        '                if (g_rt_merged) { rp_end(); zelda_slice_between_passes(); }\n                if (g_enc) { RED_ONCE(7,',
        '                rp_flush(); R.rt = *r; R.has_rt = 1; break;':
        '                rp_flush(); zelda_submit_slice(); R.rt = *r; R.has_rt = 1; break;',
        'g_enc && g_enc_mode != 1 && !pend && R.has_rt && tbdr_on() && rt_same(&R.rt, r)':
        'g_enc && g_enc_mode != 1 && !pend && R.has_rt && (rt_same(&R.rt, r) || zelda_cube_same(&R.rt, r))',
        'else if (period > 0) [dr presentAfterMinimumDuration:period]; else [dr present];':
        '''else if (period > 0) {
        static double deadline; deadline = zelda_present_deadline(deadline, CACurrentMediaTime(), period);
        [dr presentAtTime:deadline];
    } else [dr present];''',
        'ops[i].type == 0 || ops[i].type == 0x10 ? t : 0':
        '0 /* Timestamp reports have a zero payload; conditional rendering compares this field. */',
        'void mtl_sub_begin(void) {':
        'static struct { int keep, ring; uint32_t used; } z_slice_vis;\nstatic id<MTLBuffer> z_slice_vis_buffer;\nstatic uint64_t z_queue_submit;\n'
        'void mtl_sub_begin(void) {\n    if (!z_slice_vis.keep) z_queue_submit++;',
        '    int pick = -1;\n    for (int i = 0; i < g_vis_ring_n; i++) if (g_vis_ring[i].fence <= g_done) { pick = i; break; }\n':
        '''    // Slices of one queue submission share its visibility buffer, so unreported query slots are never reused.
    if (z_slice_vis.keep) {
        g_vis_ring_i = z_slice_vis.ring; if (g_vis_ring_i >= 0) g_vis_ring[g_vis_ring_i].fence = UINT64_MAX;
        g_vis = z_slice_vis_buffer; g_vis_n = z_slice_vis.used;
    } else {
    int pick = -1;
    for (int i = 0; i < g_vis_ring_n; i++) if (g_vis_ring[i].fence <= g_done) { pick = i; break; }
''',
        '    g_vis = g_vis_ring[pick].buf;\n': '    g_vis = g_vis_ring[pick].buf;\n    }\n',
        '} post_op;': '} post_op;\nstatic void zelda_conditional_post_commit(const post_op *, int, uint64_t);',
        '    if (n) memcpy(ops, g_post, n * sizeof *ops);':
        '    if (n) memcpy(ops, g_post, n * sizeof *ops);\n    zelda_conditional_post_commit(g_post, n, fence);',
        'uint64_t mtl_sub_commit(void) {': 'static void zelda_rendered_writeback(void);\nstatic void zelda_uniform_flush(void);\nstatic void zelda_write_flush(void);\nstatic void zelda_query_flush(void);\nstatic void zelda_inline_flush(void);\nstatic void zelda_counters_log(void);\nuint64_t mtl_sub_commit(void) {\n    uint64_t z_timeline = zelda_timeline_now();',
        '    [g_q4 commit:buffers count:1 options:o];\n    [g_q4 signalEvent:g_ev value:fence];':
        '    [g_q4 commit:buffers count:1 options:o];\n    zelda_timeline(TL_COMMIT, z_timeline, fence, gpu_f);\n    [g_q4 signalEvent:g_ev value:fence];',
        '    if (g_blit) { enc_b_end(g_blit); g_blit = nil; }\n    up_flush();':
        '    zelda_inline_flush(); zelda_uniform_flush(); zelda_write_flush(); zelda_query_flush(); zelda_counters_log();\n    zelda_rendered_writeback();\n    if (g_blit) { enc_b_end(g_blit); g_blit = nil; }\n    up_flush();',
        '        const void *a = p + 8;\n        switch (h.op) {\n            case OP_END: g_exec_cur = NULL;':
        '''        const void *a = p + 8;
        // Constant updates waiting for the next pass are copied before any command that could observe them.
        if (z_write_n && !zelda_write_passthrough(h.op, a)) zelda_write_flush();
        if (h.op == OP_CLEAR_COLOR) {
            int slot = ((const p_clear_color *)a)->index;
            if (slot >= 0 && slot < 8 && zelda_pending_clear_conflicts(1u << slot)) rp_flush();
        }
        // Pending conditional colour clears are drawn before any command that could observe them.
        if (z_color_clear.count && !zelda_color_clear_passthrough(h.op, a)) rp_flush();
        // Deferred inline uniforms land right after their pass, before any command not checked against them.
        if (z_inline_count && (!g_enc || !zelda_inline_passthrough(h.op, a))) zelda_inline_flush();
        if (!zelda_uniform_passthrough(h.op, a) || ((h.op == OP_DRAW || h.op == OP_DRAW_IDX) && z_render.address))
            zelda_uniform_flush();
        zelda_update_log_op(h.op, a);
        if (zelda_buffer_write_op(h.op, a)) { p += h.size; continue; }
        if (h.op == OP_UPDATE_UB && zelda_uniform_defer(a, h.size)) { p += h.size; continue; }
        if (h.op == OP_UPDATE_UB && zelda_inline_defer(a)) { p += h.size; continue; }
        if (h.op == OP_UPDATE_UB) { zelda_inline_flush(); zelda_uniform_flush(); }
        if (h.op == OP_CLEAR_COLOR || h.op == OP_CLEAR_DS) zelda_buffer_targets_written();
        if (z_depth_clear.pending && (h.op == OP_DISPATCH || h.op == OP_COPY_B2B || h.op == OP_COPY_T2B ||
            h.op == OP_COPY_B2T || h.op == OP_COPY_T2T || h.op == OP_CLEAR_TEX || h.op == OP_UPDATE_UB ||
            (h.op == OP_REPORT_COUNTER && !zelda_depth_report_deferred(a)) || h.op == OP_RESET_COUNTER || h.op == OP_SIGNAL_EVENT ||
            h.op == OP_CLEAR_COLOR || h.op == OP_CLEAR_DS)) rp_flush();
        if (z_query_first >= 0 && !zelda_query_passthrough(h.op, a)) zelda_query_flush();
        switch (h.op) {
            case OP_END: g_exec_cur = NULL;''',
        '            else if (ops[i].kind == 3) {':
        '''            else if (ops[i].kind == 4) { uint64_t time = gpu_ticks(); memcpy((uint8_t *)ops[i].addr + 8, &time, 8); }
            else if (ops[i].kind == 3) {''',
        '                if (t == NVN_COUNTER_TYPE_SAMPLES_PASSED) { post((post_op){3,':
        '                if (zelda_gpu_counter(ad, (unsigned)t)) break;\n                if (t == NVN_COUNTER_TYPE_SAMPLES_PASSED) { post((post_op){3,',
        'static void defer_drain(void) {':
        '''static void defer_drain(void) { @autoreleasepool {
    // Completion callbacks and pre-submit cleanup can run outside the submit autorelease pool.''',
        '        d->object = nil;\n    }\n}': '        d->object = nil;\n    }\n} }',
        'uint64_t mtl_present(const tex_t *t, double period, const int crop[4]) {':
        '''uint64_t mtl_present(const tex_t *t, double period, const int crop[4]) {
    @autoreleasepool { zelda_trim_texture_views(); }''',
        'void *view; uint64_t view_id, view_rid, view_ser, view_dirty;':
        'void *view, *array_view; uint64_t array_rid, view_id, view_rid, view_ser, view_dirty;',
        'pthread_mutex_lock(&g_res_mu); int ok = [g_res_objects containsObject:a]; pthread_mutex_unlock(&g_res_mu);':
        'int ok = zelda_resource_resident(r);',
        'void mtl_reg_tex(int tid, const tex_t *t, const view_t *v) {':
        '#include "nvn_texture_cache.inc"\nvoid mtl_reg_tex(int tid, const tex_t *t, const view_t *v) {',
        '    if (g_treg[id_].view) { defer_obj_unref':
        '    zelda_sampled_view_forget(&g_treg[id_]);\n    if (g_treg[id_].view) { defer_obj_unref',
        '        defer_obj_unref(v); CFRelease(rp->view);':
        '        zelda_sampled_view_forget(rp);\n        defer_obj_unref(v); CFRelease(rp->view);',
        '    if (have) return have;\n    if (t->d.target == 10)':
        '    if (have) return [have isKindOfClass:[NvPkg class]] ? ((NvPkg *)have)->tex : have;\n    if (t->d.target == 10)',
        '''    MTLTextureDescriptor *d = tex_desc(t, 0);
    if (!d) return nil;
    id<MTLTexture> mt = [g_dev newTextureWithDescriptor:d];
    if (!mt) { RED_ONCE(2, "\u5efa MTLTexture \u5931\u8d25 %ux%u \u683c\u5f0f %#x \u76ee\u6807 %d", t->d.width, t->d.height, t->d.format, t->d.target); return nil; }
    res_add(mt);
    serial_put(t->serial, mt);
    return mt;''':
        '''    MTLTextureDescriptor *d = tex_desc(t, 0);
    if (!d) return nil;
    id<MTLTexture> mt = zelda_device_texture(t, d);
    if (!mt) MDIE("Cannot allocate device texture storage");
    return mt;''',
        '''    uint64_t rs[9]; int nr = 0;
    for (int k = 0; k < R.rt.n && k < 8; k++) if (R.rt.color[k]) rs[nr++] = R.rt.color[k]->serial;
    if (R.rt.depth) rs[nr++] = R.rt.depth->serial;''':
        '''    const tex_t *targets[9]; int nr = 0;
    for (int k = 0; k < R.rt.n && k < 8; k++) if (R.rt.color[k]) targets[nr++] = R.rt.color[k];
    if (R.rt.depth) targets[nr++] = R.rt.depth;''',
        '''            pthread_mutex_lock(&g_rmu); uint64_t ser = tid < g_ntreg && g_treg[tid].ok ? g_treg[tid].t.serial : 0; pthread_mutex_unlock(&g_rmu);
            for (int j = 0; j < nr; j++) if (ser && ser == rs[j]) return 1;''':
        '''            pthread_mutex_lock(&g_rmu);
            int overlap = 0;
            if (tid < g_ntreg && g_treg[tid].ok)
                for (int j = 0; j < nr; j++) overlap |= zelda_texture_same_storage(&g_treg[tid].t, targets[j]) &&
                    !zelda_sample_mip_disjoint(sh, b, tid, h, targets[j], views[j], has_view[j]);
            pthread_mutex_unlock(&g_rmu);
            if (overlap) return 1;''',
        'static MTLSamplerAddressMode wrapm(int w) {':
        '#include "nvn_sampled_view.inc"\nstatic MTLSamplerAddressMode wrapm(int w) {',
        '''        } else if (b->kind == NVN_RB_TEX) {
            int tid = reg < 128 ? (int)(S.tex[nvn_stage][reg] & 0xfffff) : 0;
            void *tv = NULL; uint64_t trid = tid ? tex_fast_rid(tid, &tv) : 0;''':
        '''        } else if (b->kind == NVN_RB_TEX) {
            int tid = reg < 128 ? (int)(S.tex[nvn_stage][reg] & 0xfffff) : 0;
            void *tv = NULL; uint64_t trid = tid ? zelda_sampled_texture(tid, b->dtt, &tv) : 0;''',
        'int packaged = t->has_layout && t->mem;': 'int packaged = ' + cpu_texture + ';',
        'if (t->has_layout && t->mem) {': 'if (' + cpu_texture + ') {',
        'int use_gpu = (!unp || gdec) && g_nvn_deswz_gpu &&':
        'int use_gpu = !(t->d.flags & (NVN_TEXTURE_FLAGS_LINEAR | NVN_TEXTURE_FLAGS_LINEAR_RENDER_TARGET)) && (!unp || gdec) && g_nvn_deswz_gpu &&',
        'if (g_nvn_deswz_gpu && !use_gpu && !unp)':
        'if (!(t->d.flags & (NVN_TEXTURE_FLAGS_LINEAR | NVN_TEXTURE_FLAGS_LINEAR_RENDER_TARGET)) && g_nvn_deswz_gpu && !use_gpu && !unp)',
        'MTLTextureDescriptor *d = tex_desc(t, 1);':
        'MTLTextureDescriptor *d = tex_desc(t, t->has_layout);',
        'char key[24]; id<MTLBuffer> c1buf;': 'char key[32]; id<MTLBuffer> c1buf;',
        'int stage, range_ro;': 'int stage, range_ro, z_cube_uniform, z_layered, z_wide_line; NSString *z_fetch_identity;',
        '@interface NvProg : NSObject { @public NvShader *sh[6]; }':
        '@interface NvProg : NSObject { @public NvShader *sh[6]; NvProg *z_cube, *z_line; NSMutableDictionary *z_fetch; }',
        'static NvShader *shader_load(NSString *pc, int stage) {':
        '#include "nvn_uniform_layout.h"\n#include "nvn_pointer_storage.inc"\n#include "nvn_depth_output.inc"\nstatic NvShader *shader_load(NSString *pc, int stage) {',
        '    if (nvn_refl_from_json(j, &o->rf, err, sizeof err))':
        '    if (zelda_refl_from_json(j, &o->rf, err, sizeof err))',
        'static int encode_args(id<MTLCommandEncoder> en, NvShader *sh, int nvn_stage, int metal_vertex) {':
        '#define ZELDA_POINTER_STORAGE_BIND 1\n#include "nvn_pointer_storage.inc"\nstatic int encode_args(id<MTLCommandEncoder> en, NvShader *sh, int nvn_stage, int metal_vertex) {',
        '        } else if (b->kind == NVN_RB_C1) {':
        '''        } else if (b->kind == ZELDA_RB_SBUF_CB) {
            void *mb = NULL; uint64_t gpu = zelda_pointer_storage(nvn_stage, b, sh->key, &mb);
            if (gpu) { slots[aid] = gpu; USE((__bridge id<MTLBuffer>)mb, MTLResourceUsageRead); }
            else { slots[aid] = dummy_buf_gpu(); USE(dummy_buf(), MTLResourceUsageRead); }
        } else if (b->kind == NVN_RB_C1) {''',
        'strlcpy(o->key, pc.UTF8String, sizeof o->key);':
        'strlcpy(o->key, pc.UTF8String, sizeof o->key); o->z_cube_uniform = zelda_cube_uniform_layout(lp, pc); zelda_function_source(o->fn, lp, nil);',
        'static __unsafe_unretained NvProg *prog_get(': '#include "nvn_compile_shaders.inc"\nstatic __unsafe_unretained NvProg *prog_get(',
        'static MTLRenderPipelineDescriptor *pso_desc(': '#include "nvn_shader_variant.inc"\nstatic MTLRenderPipelineDescriptor *pso_desc(',
        'static int encode_args(': '#include "nvn_driver.inc"\n#include "nvn_buffer_cache.inc"\n#include "nvn_buffer_writes.inc"\n#include "nvn_uniform_snapshot.inc"\n#include "nvn_inline_uniform.inc"\nstatic int encode_args(',
        '            slots[aid] = gpu;\n            USE((__bridge id<MTLBuffer>)mb, MTLResourceUsageRead);':
        '            slots[aid] = z_uniform_gpu && nvn_stage < 2 && reg == 0 ? z_uniform_gpu :\n'
        '                nvn_stage < 5 && z_inline_override[nvn_stage][reg] ? z_inline_override[nvn_stage][reg] : gpu;\n            USE((__bridge id<MTLBuffer>)mb, MTLResourceUsageRead);',
        '        op_hdr h; memcpy(&h, p, sizeof h);\n        g_exec_cur': '''        if ((uintptr_t)p & 7) MDIE("Unaligned command pointer=%p", p);
        op_hdr h; memcpy(&h, p, sizeof h);
        g_exec_cur''',
        '        g_exec_cur = p;': '''        if (!nvn_command_valid(p, h)) {
            MDIE("Invalid command record address=%p op=%u size=%u depth=%d", p, h.op, h.size, g_ret_n);
        }
        g_exec_cur = p;''',
        # Pass dumps also mark every copy/compute encoder that opens outside a render pass, so gaps can be attributed.
        'static void blit_note(void) { if (g_enc) PD("E %llu \u62f7\u8d1d / \u7f13\u51b2\u66f4\u65b0\u7ed3\u675f\u6e32\u67d3\u901a\u9053", g_stat_pass); }':
        'static void blit_note(void) { if (g_enc) PD("E %llu \u62f7\u8d1d / \u7f13\u51b2\u66f4\u65b0\u7ed3\u675f\u6e32\u67d3\u901a\u9053", g_stat_pass); else if (!g_blit) PD("b %llu \u6253\u5f00\u62f7\u8d1d / \u8ba1\u7b97\u7f16\u7801\u5668", g_stat_pass); }',
        'static void dispatch(const p_dispatch *dp) {': '''#include "nvn_dispatch.inc"
static void dispatch(const p_dispatch *dp) {
    if (dp->x < 0 || dp->y < 0 || dp->z < 0) MDIE("Invalid negative compute group count");
    if (!dp->x || !dp->y || !dp->z) return;''',
        '    const void *k = (__bridge const void *)cs;':
        '''    if (cs->builtin) zelda_buffer_forget();
    else zelda_buffer_shader_written(&cs->rf, 5);
    const void *k = (__bridge const void *)cs;''',
        '                if (bank == 0) {':
        '                if (bank == 0) {\n                    if (nvn_stage == 5) zelda_compute_uniform_table(d, S.ubo[5]);',
        'd.vertexFunction = vs->fn;': 'd.vertexFunction = zelda_vertex_function(vs, k->attr, fs); if (vs->z_layered) d.inputPrimitiveTopology = MTLPrimitiveTopologyClassTriangle; else if (vs->z_wide_line) d.inputPrimitiveTopology = MTLPrimitiveTopologyClassLine;',
        'static void pass_dims(': '#include "nvn_attachmentless.inc"\nstatic void zelda_attachmentless_begin(MTL4RenderPassDescriptor *pass);\nstatic void pass_dims(',
        '    if (!*w) *w = *h = 1;':
        '    if (!*w) { *w = MAX(pd.renderTargetWidth, 1); *h = MAX(pd.renderTargetHeight, 1); }',
        '    if (!any) { RED_ONCE(4, "\u6e32\u67d3\u76ee\u6807\u90fd\u5efa\u4e0d\u51fa MTLTexture"); return nil; }': '''    if (!any) {
        if (clear_only == 1) return nil;
        zelda_attachmentless_begin(pd);
    }''',
        '    id<MTLTexture> base = tex_mtl(t); a.texture = base;':
        '    id<MTLTexture> base = tex_mtl(t); a.texture = base;\n    if (!a.texture) MDIE("Cannot create render target format=%x", t->d.format);\n    zelda_texture_gpu_written(t);',
        # Upstream's own format-view branch falls back to the base format when the texture lacks view usage;
        # zelda_attach_view recreates such textures and also covers compressed targets, so it stays the only path.
        '    if (!color || !base) return;\n    MTLPixelFormat pf = rt_color_fmt(base, v, has_v, 1);\n    if (pf == base.pixelFormat) return;\n    id<MTLTexture> vw = [base newTextureViewWithPixelFormat:pf];\n    if (!vw) { RED_ONCE(49, "\u6e32\u67d3\u76ee\u6807\u89c6\u56fe\u5efa\u4e0d\u51fa：\u683c\u5f0f %#x（\u5e95\u56fe %lu），\u6309\u5e95\u56fe\u683c\u5f0f\u753b（\u9996\u4f8b）", v->format, (unsigned long)base.pixelFormat); return; }\n    a.texture = vw; defer_obj_unref(vw);\n    { static int once; if (!once++) MLOG("\u6e32\u67d3\u76ee\u6807\u7ecf\u6362\u683c\u5f0f\u7684\u89c6\u56fe\u6302\u4e0a：\u89c6\u56fe\u683c\u5f0f %#x，\u5e95\u56fe %lu → \u6302 %lu（\u9996\u4f8b）", v->format, (unsigned long)base.pixelFormat, (unsigned long)pf); }\n':
        '    (void)color;\n',
        'd.fragmentFunction = fsfn ? fsfn : fs ? fs->fn : nil;':
        'd.fragmentFunction = fsfn ? fsfn : zelda_fragment_function(fs, k->cfmt);\n'
        '    if (!k->dfmt) d.fragmentFunction = zelda_depthless_function(d.fragmentFunction, fs);',
        # The readable-tile copy cannot drop a depth output; such a draw ends the merge and gets its own pass.
        '    *fn = bf_fn(fs, fx, fbmask, lmap);':
        '    if (!g_pass.dfmt && zelda_function_writes_depth(fs->fn, fs)) { PD("F %llu cannot merge: fragment writes depth without a depth attachment", g_stat_pass); return 0; }\n'
        '    *fn = bf_fn(fs, fx, fbmask, lmap);',
        'static void pso_predict_rest(void) {\n    if (!g_exec_next) return;':
        '''static void pso_predict_rest(void) {
    if (!g_exec_next) return;
    typeof(R) Rsave = R;''',
        'memcpy(&S, &Ssave, sizeof S); args_dirty_all(); g_pso_dirty = pso_dirty; g_dss_dirty = dss_dirty;':
        'memcpy(&S, &Ssave, sizeof S); R = Rsave; args_dirty_all(); g_pso_dirty = pso_dirty; g_dss_dirty = dss_dirty;',
        'fz_cursor cur = {.p = g_exec_next};\n    // \u5148\u6536\u9f50\u518d\u4e22：': '''fz_cursor cur = {.p = g_exec_next, .ret_n = g_ret_n};
    if (g_ret_n > (int)(sizeof cur.ret / sizeof *cur.ret)) return;
    memcpy(cur.ret, g_ret, g_ret_n * sizeof *g_ret);
    // \u5148\u6536\u9f50\u518d\u4e22：''',
        '    (void)df; (void)sf;': '''    (void)df; (void)sf;
    for (int attempt = 0; attempt < 2; attempt++) {
        z_variant_collect = 1;
        for (int i = 0; i < na + nb; i++) {
            typeof(*qa) *pending = i < na ? &qa[i] : &qb[i - na];
            zelda_vertex_function((__bridge NvShader *)pending->vs, pending->k.attr, (__bridge NvShader *)pending->fs);
            zelda_fragment_function((__bridge NvShader *)pending->fs, pending->k.cfmt);
        }
        z_variant_collect = 0;
        if (!z_variant_missing) break;
        if (attempt) { MLOG("BLOCKED shader variants remain unsupported after compilation"); exit(78); }
        MLOG("VARIANT_SCAN draws=%d primary=%d fallback=%d", draws, na, nb);
        zelda_compile_shaders(); z_variant_missing = 0;
    }''',
        '                    g_vis_on = 1; g_vis_counting = 1; vis_clear(); }':
        '                    if (g_enc && !g_enc_has_vis) rp_end();\n                    g_vis_on = 1; g_vis_counting = 1; vis_clear(); }',
        'if (!ps) { MLOG("【\u7ea2】\u5efa\u6e32\u67d3\u7ba1\u7ebf\u5931\u8d25：%s", e.localizedDescription.UTF8String); return nil; }':
        'if (!ps) MDIE("Render pipeline failed vs=%s fs=%s: %s", vs->key, fs ? fs->key : "none", e.localizedDescription.UTF8String);',
        'if (g_enc || c->mask != 0xf || !scissor_full(R.rt.color[c->index], &R.rt.cview[c->index], R.rt.has_cview[c->index])) { RED_ONCE(6, "\u901a\u9053\u4e2d\u9014 / \u90e8\u5206\u63a9\u7801 / \u90e8\u5206\u526a\u88c1\u7684\u6e05\u8272\u672a\u5b9e\u73b0（\u9996\u4f8b \u63a9\u7801 %#x）", c->mask); break; }':
        '''if (!c->mask) break;
                // shortcut: partial channel masks stop explicitly; add a masked draw when the game requests one.
                if (c->mask != 0xf) MDIE("Partial clear channel mask unsupported mask=%x", c->mask);
                if (!scissor_full(R.rt.color[c->index], &R.rt.cview[c->index], R.rt.has_cview[c->index])) { zelda_partial_clear(c); break; }
                if (g_enc) { rp_end(); zelda_slice_between_passes(); }''',
        'static void rp_end(void) { if (g_enc) {':
        '#include "nvn_render_view.inc"\n#include "nvn_depth_clear.h"\n#include "nvn_depth16_clear.h"\n#include "nvn_color_clear.h"\n#include "nvn_pass_writes.h"\n#include "nvn_color_targets.inc"\n#include "nvn_cube_pass.h"\nstatic void rp_end(void) { if (g_enc) {',
        '    g_enc_mode = clear_only;':
        '    if (z_depth_clear.pending) clear_only = 0;\n    g_enc_mode = clear_only;',
        '    return g_enc;\n}\n// \u5f00\u901a\u9053\u524d\u7684\u7a7a\u526a\u88c1\u9884\u68c0\u67e5':
        '    zelda_write_encode();\n    zelda_depth_clear_encode();\n    zelda_color_clear_encode();\n    return g_enc;\n}\n// \u5f00\u901a\u9053\u524d\u7684\u7a7a\u526a\u88c1\u9884\u68c0\u67e5',
        '    int pend = R.clear_d || R.clear_s;\n    for (int i = 0; i < 8; i++) pend |= R.clear_c[i];':
        '    int pend = R.clear_d || R.clear_s || z_depth_clear.pending || z_color_clear.count;\n    for (int i = 0; i < 8; i++) pend |= R.clear_c[i];',
        '    RPD *pd = [RPD new];\n    int any = 0;\n': '    RPD *pd = [RPD new];\n    int any = 0;\n    zelda_begin_color_targets(clear_only);\n',
        'static int draw_samples_rt(NvProg *pr) {':
        'static int zelda_sample_mip_disjoint(NvShader *, const nvn_rbind *, int, uint64_t, const tex_t *, const view_t *, int);\n'
        'static int draw_samples_rt_mask(NvProg *pr, unsigned colors, int depth);\nstatic int draw_samples_rt(NvProg *pr) { return draw_samples_rt_mask(pr, 0xff, 1); }\nstatic int draw_samples_rt_mask(NvProg *pr, unsigned colors, int depth) {',
        '    for (int k = 0; k < R.rt.n && k < 8; k++) if (R.rt.color[k]) targets[nr++] = R.rt.color[k];\n    if (R.rt.depth) targets[nr++] = R.rt.depth;':
        '''    const view_t *views[9]; int has_view[9];
    for (int k = 0; k < R.rt.n && k < 8; k++) if (R.rt.color[k] && (colors >> k & 1)) {
        views[nr] = &R.rt.cview[k]; has_view[nr] = R.rt.has_cview[k]; targets[nr++] = R.rt.color[k];
    }
    if (R.rt.depth && depth) { views[nr] = &R.rt.dview; has_view[nr] = R.rt.has_dview; targets[nr++] = R.rt.depth; }''',
        'k->blend_en = S.color.blend_en; k->chmask = S.chmask.mask; k->a2c = S.ms.a2c;':
        'k->blend_en = S.color.blend_en; k->chmask = zelda_color_write_mask(S.chmask.mask); k->a2c = S.ms.a2c;',
        'if (!R.rt.color[i] || (clear_only == 1 && !R.clear_c[i])) continue;':
        'if (!R.rt.color[i] || !zelda_use_color_target(i, clear_only, R.clear_c[i] || (zelda_color_clear_targets() >> i & 1))) continue;',
        'g_enc = nil; g_rt_merged = 0;':
        'g_enc = nil; z_cube_active = 0; zelda_flush_block_targets(); g_rt_merged = 0;',
        '    if (has_v && v->set_layers) { if (t->d.target == 2) a.depthPlane = v->min_layer; else a.slice = v->min_layer; }':
        '    if (has_v && v->set_layers) { if (t->d.target == 2) a.depthPlane = v->min_layer; else a.slice = v->min_layer; }\n    zelda_attach_view(a, t, v, has_v);',
        '    g_enc = enc_r_begin(pd);':
        '    if (g_blit) { enc_b_end(g_blit); g_blit = nil; }\n    zelda_cube_descriptor(pd, clear_only != 1 && (g_fd_dw || g_fd_di));\n    zelda_color_clear_unfit(pd);\n    g_enc = enc_r_begin(pd);',
        'cf[i] = (uint32_t)rt_color_fmt(t, &r->cview[i], r->has_cview[i], 0); samples = (uint32_t)t.sampleCount;':
        '''cf[i] = (uint32_t)t.pixelFormat; samples = (uint32_t)t.sampleCount;
                if (r->has_cview[i] && r->cview[i].set_format) {
                    uint8_t swizzle[4];
                    if (!nvn_fmt_to_mtl(r->cview[i].format, &cf[i], swizzle) || !cf[i]) MDIE("Unsupported predicted render view format");
                }''',
        # 8/16-bit *_UI2F / *_I2F: integer components converted to float. Metal converts these when the shader input
        # is float (zelda_vertex_input_class keeps them in the float variant); it rejects 32-bit integers read as float,
        # so the 32-bit forms stay unmapped and reported.
        '        case 0x3D: return MTLVertexFormatUInt1010102Normalized;':
        '''        case 0x51: return MTLVertexFormatUChar; case 0x52: return MTLVertexFormatChar; case 0x53: return MTLVertexFormatUShort;
        case 0x54: return MTLVertexFormatShort; case 0x57: return MTLVertexFormatUChar2; case 0x58: return MTLVertexFormatChar2;
        case 0x59: return MTLVertexFormatUShort2; case 0x5A: return MTLVertexFormatShort2; case 0x5D: return MTLVertexFormatUChar3;
        case 0x5E: return MTLVertexFormatChar3; case 0x5F: return MTLVertexFormatUShort3; case 0x60: return MTLVertexFormatShort3;
        case 0x63: return MTLVertexFormatUChar4; case 0x64: return MTLVertexFormatChar4; case 0x65: return MTLVertexFormatUShort4;
        case 0x66: return MTLVertexFormatShort4;
        case 0x69: return MTLVertexFormatInt1010102Normalized;\n        case 0x3D: return MTLVertexFormatUInt1010102Normalized;''',
        # Counter timestamps may run on a scaled clock (dynamic resolution budget, nvn_gpu_clock.inc).
        'static uint64_t gpu_ticks(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return ((uint64_t)t.tv_sec * 1000000000ull + t.tv_nsec) * 12 / 625; }':
        '#include "nvn_gpu_clock.inc"',
        # Diagnostic ZELDA_PASSDUMP_GUEST=<dir>: while the pass dump records bindings, save each sampled texture's guest
        # storage once (<serial>.bin plus a <serial>.txt descriptor) to check layouts offline.
        '            if (bl) PD("B %llu st%d tex reg%d tid=%d %s ser=%llu fmt=%lu':
        '''            if (bl && t && tid < g_ntreg && g_treg[tid].t.mem) {
                static const char *gd; static int gd_init; static NSMutableSet *gd_seen;
                if (!gd_init) { gd_init = 1; gd = getenv("ZELDA_PASSDUMP_GUEST"); gd_seen = [NSMutableSet set]; }
                const tex_t *gt = &g_treg[tid].t;
                // ZELDA_TEXRAW_SHADER=<key prefix>: the DM_NVN_TEXDUMP frame also saves the GPU contents of every texture this shader samples.
                static const char *rk; static int rk_init; extern __strong NSMutableOrderedSet *z_texraw_noted;
                if (!rk_init) { rk_init = 1; rk = getenv("ZELDA_TEXRAW_SHADER"); if (rk) z_texraw_noted = [NSMutableOrderedSet orderedSet]; }
                if (rk && !strncmp(sh->key, rk, strlen(rk)) && ![z_texraw_noted containsObject:@(gt->serial)]) {
                    [z_texraw_noted addObject:@(gt->serial)]; id holder = serial_get(gt->serial);
                    MLOG("[texraw] note ser=%llu reg=%d mem=%p flags=%#x pool=%p/%#x off=%#llx lv%u holder=%s gpu_written=%d", gt->serial, reg, gt->mem, gt->d.flags, (void *)gt->pool, gt->pool ? gt->pool->flags : 0, (unsigned long long)gt->off, gt->d.levels,
                         holder ? object_getClassName(holder) : "none", [holder isKindOfClass:[NvPkg class]] && ((NvPkg *)holder)->z_rendered_pages != nil);
                }
                if (gd && gt->L.size <= (8u << 20) && ![gd_seen containsObject:@(gt->serial)]) {
                    [gd_seen addObject:@(gt->serial)]; char p[1024];
                    snprintf(p, sizeof p, "%s/%llu.bin", gd, (unsigned long long)gt->serial);
                    FILE *f = fopen(p, "wb"); if (f) { fwrite(gt->mem, 1, gt->L.size, f); fclose(f); }
                    snprintf(p, sizeof p, "%s/%llu.txt", gd, (unsigned long long)gt->serial);
                    f = fopen(p, "w"); if (f) { fprintf(f, "shader=%s st=%d reg=%d target=%d format=%#x flags=%#x w=%u h=%u d=%u levels=%u stride=%u pkg_bh=%d size=%llu tileH=%u layout=%d pool_flags=%#x\\n",
                        sh->key, nvn_stage, reg, gt->d.target, gt->d.format, gt->d.flags, gt->d.width, gt->d.height, gt->d.depth, gt->d.levels, gt->d.stride, gt->d.pkg_block_h,
                        (unsigned long long)gt->L.size, gt->L.tileH, gt->has_layout, gt->pool ? gt->pool->flags : 0); fclose(f); }
                }
            }
            if (bl) PD("B %llu st%d tex reg%d tid=%d %s ser=%llu fmt=%lu''',
        # Diagnostic: raw dumps of the textures noted through ZELDA_TEXRAW_SHADER join the DM_NVN_TEXRAW list.
        '    for (const char *q = getenv("DM_NVN_TEXRAW"); q && *q; q = strchr(q, \',\') ? strchr(q, \',\') + 1 : q + strlen(q)) {\n        uint64_t ser = (uint64_t)atoll(q); id o = serial_get(ser);':
        '    NSMutableArray<NSNumber *> *raw = [NSMutableArray array];\n    for (const char *q = getenv("DM_NVN_TEXRAW"); q && *q; q = strchr(q, \',\') ? strchr(q, \',\') + 1 : q + strlen(q)) [raw addObject:@(atoll(q))];\n    if (z_texraw_noted) [raw addObjectsFromArray:z_texraw_noted.array];\n    for (NSNumber *n in raw) {\n        uint64_t ser = n.unsignedLongLongValue; id o = serial_get(ser);',
        # GPU writes into a packaged texture keep it from being re-uploaded over (nvn_texture_rendered.inc).
        'if (passdump()) PD("W %llu st%d reg%d tid=%d %s ser=%llu", g_stat_pass, nvn_stage, reg, tid, t ? "\u5b9e" : "\u54d1", t && tid < g_ntreg ? g_treg[tid].t.serial : 0ull);':
        'if (t && tid < g_ntreg) zelda_texture_gpu_written(&g_treg[tid].t);\n            if (passdump()) PD("W %llu st%d reg%d tid=%d %s ser=%llu", g_stat_pass, nvn_stage, reg, tid, t ? "\u5b9e" : "\u54d1", t && tid < g_ntreg ? g_treg[tid].t.serial : 0ull);',
        'static void copy_b2t(const p_copy_b2t *c) {\n    if (c->tex) zskip_del(c->tex->serial);\n    id<MTLTexture> t = tex_mtl(c->tex);':
        'static void copy_b2t(const p_copy_b2t *c) {\n    if (c->tex) zskip_del(c->tex->serial);\n    id<MTLTexture> t = tex_mtl(c->tex); zelda_texture_gpu_written(c->tex);',
        'static void clear_tex(const p_clear_tex *c) {\n    if (c->tex) zskip_del(c->tex->serial);\n    id<MTLTexture> t = tex_mtl(c->tex);':
        'static void clear_tex(const p_clear_tex *c) {\n    if (c->tex) zskip_del(c->tex->serial);\n    id<MTLTexture> t = tex_mtl(c->tex); zelda_texture_gpu_written(c->tex);',
        # Depth16 load-clears rounded like fragment depth writes (nvn_depth16_clear.h).
        '            pd.depthAttachment.clearDepth = R.cd; pd.depthAttachment.storeAction = MTLStoreActionStore;\n            MTLPixelFormat pf = pd.depthAttachment.texture.pixelFormat;':
        '            MTLPixelFormat pf = pd.depthAttachment.texture.pixelFormat;\n            pd.depthAttachment.clearDepth = zelda_depth_clear_value(pf, R.cd); pd.depthAttachment.storeAction = MTLStoreActionStore;',
        '        if (depth) { rp.depthAttachment.clearDepth = c->c.f[0];':
        '        if (depth) { rp.depthAttachment.clearDepth = zelda_depth_clear_value(t.pixelFormat, c->c.f[0]);',
        'id<MTLTexture> s = tex_mtl(c->src), d = tex_mtl(c->dst);':
        'id<MTLTexture> s = tex_mtl(c->src), d = tex_mtl(c->dst); zelda_texture_gpu_written(c->dst);',
        'static long g_td_frame = -2;': '__strong NSMutableOrderedSet *z_texraw_noted;   // serials noted through ZELDA_TEXRAW_SHADER\nstatic long g_td_frame = -2;',
        # DM_NVN_TEXDUMP=dump:<path> dumps the first whole frame of the input script's "dump N" window (N >= 2) instead of a fixed frame.
        '    if (g_td_frame < 0 || g_td_done) return;\n    long f = (long)atomic_load(&g_cur_frame);\n    if (f > g_td_frame) { td_dump(); return; }':
        '''    static int z_td_follow = -1;
    if (z_td_follow < 0) { const char *e = getenv("DM_NVN_TEXDUMP"); z_td_follow = e && !strncmp(e, "dump:", 5); if (z_td_follow) g_td_frame = LONG_MAX; }
    if (g_td_frame < 0 || g_td_done) return;
    long f = (long)atomic_load(&g_cur_frame);
    if (z_td_follow == 1 && passdump()) { g_td_frame = f + 1; z_td_follow = 2; }   // the window opens mid-frame: take the next, whole frame
    if (f > g_td_frame) { td_dump(); return; }''',
        # Diagnostic ZELDA_PSO_LOG=1: every render pipeline built at draw time, by kind (what the startup warm-up misses).
        '        ps = [g_dev newRenderPipelineStateWithDescriptor:d error:&e];\n    }\n    g_bp_pso_n++; g_bp_pso_ns += bp_now() - bt;':
        '''        ps = [g_dev newRenderPipelineStateWithDescriptor:d error:&e];
    }
    g_bp_pso_n++; g_bp_pso_ns += bp_now() - bt;
    { static int log = -1; if (log < 0) log = getenv("ZELDA_PSO_LOG") != NULL;
      if (log) MLOG("[pso] frame=%llu kind=%s vs=%s fs=%s dfmt=%u ms=%.1f", (unsigned long long)atomic_load(&g_cur_frame),
                    g_pso_lmap ? "merged" : !zelda_warm_canonical(vs, fs) ? "variant" : "canonical", vs->key, fs ? fs->key : "-", k.dfmt, (bp_now() - bt) / 1e6); }''',
        # Updates between passes wait for the next pass (nvn_pass_writes.inc).
        'mtl_sub_copy((uint64_t)((const uint8_t *)a + 16), d, n); break; }':
        'if (!zelda_write_defer((uint64_t)((const uint8_t *)a + 16), d, n)) mtl_sub_copy((uint64_t)((const uint8_t *)a + 16), d, n); break; }',
        # Resident textures by format and kind in the DM_RES_CENSUS line (nvn_residency.inc).
        '             nl[0], bl[0] >> 20, nl[1], bl[1] >> 20, nr[0], br[0] >> 20, nr[1], br[1] >> 20);\n    }':
        '             nl[0], bl[0] >> 20, nl[1], bl[1] >> 20, nr[0], br[0] >> 20, nr[1], br[1] >> 20);\n        zelda_texture_census();\n    }',
        # Startup warm-up of recorded shaders and pipelines at the first submission (nvn_shader_warm.inc).
        'static void warm_claim(void);': 'static void warm_claim(void);\nstatic void zelda_shader_warm(void);',
        '    warm_claim();\n    if (g_cb) MDIE(': '    warm_claim(); zelda_shader_warm();\n    if (g_cb) MDIE(',
        'static void warm_claim(void) {': '#include "nvn_shader_warm.inc"\nstatic void warm_claim(void) {',
        '    pso_user_record("R\\t%s\\n", shex);':
        '''    if (zelda_warm_canonical(vs, fs)) pso_user_record("R\\t%s\\n", shex);
    else if (!g_pso_lmap && g_shcache[@(vs->key)] == vs && fs && fs->z_fetch_identity.length)
        pso_user_record("F\\t%s\\t%s\\n", shex, fs->z_fetch_identity.UTF8String);''',
        # Optional MetalFX spatial upscale before the present draw (nvn_present_scale.inc).
        'static int present_encode_m4(CBUF cb, id<MTLTexture> src, id<MTLTexture> dst, const int crop[4], int uv_i);':
        '#include "nvn_present_scale.inc"\nstatic int present_encode_m4(CBUF cb, id<MTLTexture> src, id<MTLTexture> dst, const int crop[4], int uv_i);',
        '    int full = vp.originX == 0 && vp.originY == 0 && vp.width == dw && vp.height == dh;\n    RPD *rp = [RPD new];':
        '''    int full = vp.originX == 0 && vp.originY == 0 && vp.width == dw && vp.height == dh;
#if !DM_NVN_M3
    {   id<MTLTexture> z_up = zelda_present_upscale(cb, src, cx, cy, cw, ch, (NSUInteger)vp.width, (NSUInteger)vp.height);
        if (z_up) { src = z_up; cx = cy = 0; cw = z_up.width; ch = z_up.height; } }
#endif
    RPD *rp = [RPD new];''',
        # 8-bit index conversions are drawn inside the draw's pass (nvn_index.inc).
        '    id<MTLRenderCommandEncoder> en = rp_begin_ex(mode);':
        '    id<MTLRenderCommandEncoder> en = rp_begin_ex(mode);\n    zelda_index_encode(en);',
        # Report every unmapped vertex format (with its shader), not only the first one of the run.
        'if (a < 16 && k->attr[a].fmt && vf == MTLVertexFormatInvalid) RED_ONCE(15, "\u9876\u70b9\u5c5e\u6027\u683c\u5f0f %#x \u672a\u6620\u5c04", k->attr[a].fmt);':
        '''if (a < 16 && k->attr[a].fmt && vf == MTLVertexFormatInvalid) {
            static _Atomic uint8_t reported[256];
            if (!atomic_exchange(&reported[k->attr[a].fmt & 0xff], 1)) MLOG("ERROR: Unmapped vertex format %#x vs=%s attribute=%d", k->attr[a].fmt, vs->key, a);
        }''',
        'void mtl_exec(const uint8_t *p) {':
        '''#include "nvn_conditional_mtl.inc"
#include "nvn_query_gpu.inc"
#include "nvn_color_clear.inc"
#include "nvn_depth_clear.inc"
#include "nvn_clear_region.inc"
#include "nvn_pass_writes.inc"
#include "nvn_conditional_draw.inc"
#include "nvn_submit.inc"
static void zelda_partial_clear(const p_clear_color *c) {
    rp_flush();
    MTLRenderPassColorAttachmentDescriptor *attachment = [MTLRenderPassColorAttachmentDescriptor new];
    attach(attachment, R.rt.color[c->index], &R.rt.cview[c->index], R.rt.has_cview[c->index], 1);
    zelda_clear_region(attachment, R.sc, MTLClearColorMake(c->rgba[0], c->rgba[1], c->rgba[2], c->rgba[3]));
    zelda_flush_block_targets(); args_dirty_all(); g_pso_dirty = g_dss_dirty = 1;
}
void mtl_exec(const uint8_t *p) {''',
        '            case OP_BEGIN: break;':
        '            case OP_BEGIN: break;\n            case OP_RENDER_ENABLE: z_render = *(const p_render_enable *)a;\n'
        '                PD("W enable addr=%#llx mode=%d enabled=%d", (unsigned long long)z_render.address, z_render.mode, z_render.enabled); break;',
        '            case OP_BARRIER: case OP_SET_TEX_POOL: case OP_SET_SAMP_POOL: break;':
        '''            case OP_BARRIER:
                PD("Z barrier bits=%#x", *(const uint32_t *)a);
                rp_flush();
                if (g_blit) zelda_blit_barrier();
                zelda_submit_slice();
                break;
            case OP_SET_TEX_POOL: case OP_SET_SAMP_POOL: break;''',
        '            case OP_DRAW: g_exec_next = p + h.size; draw(a, NULL); g_exec_next = NULL; break;':
        '            case OP_DRAW: g_exec_next = p + h.size; if (zelda_conditional_draw(a, NULL)) draw(a, NULL); z_conditional_gpu = 0; g_exec_next = NULL; break;',
        '            case OP_DRAW_IDX: g_exec_next = p + h.size; draw(NULL, a); g_exec_next = NULL; break;':
        '            case OP_DRAW_IDX: g_exec_next = p + h.size; if (zelda_conditional_draw(NULL, a)) draw(NULL, a); z_conditional_gpu = 0; g_exec_next = NULL; break;',
        '            case OP_CLEAR_COLOR: {\n                const p_clear_color *c = a;':
        '            case OP_CLEAR_COLOR: {\n                if (zelda_gpu_conditional_clear(a) || !zelda_render_enabled()) break;\n                const p_clear_color *c = a;',
        '            case OP_CLEAR_DS: {\n                const p_clear_ds *c = a;':
        '            case OP_CLEAR_DS: {\n                if (zelda_gpu_depth_clear(a) || !zelda_render_enabled()) break;\n                const p_clear_ds *c = a;',
        '#import <CommonCrypto/CommonDigest.h>': '#include "shader_key.h"',
        '    float blend_color[4]; int64_t copy_stride;':
        '    float blend_color[4]; int64_t copy_stride; float point_size, line_width; uint8_t depth_clamp, restart; uint32_t restart_index;',
        '} S;': '''} S = {.point_size = 1, .line_width = 1};
static void zelda_attachmentless_begin(MTL4RenderPassDescriptor *pass) {
    zelda_attachmentless_pass(pass, S.vp[0], S.has_vp);
}''',
        '    memset(&S, 0, sizeof S);': '    memset(&S, 0, sizeof S); S.point_size = S.line_width = 1;',
        '                case 5: dst = &S.ds; n = sizeof S.ds; break;':
        '''                case 5: dst = &S.ds; n = sizeof S.ds; break;
                case 6: S.depth_clamp = b->bytes[0]; break;
                case 7: memcpy(&S.point_size, b->bytes, sizeof S.point_size); break;
                case 8: S.restart = b->bytes[0]; memcpy(&S.restart_index, b->bytes + 4, sizeof S.restart_index); break;
                case 9: memcpy(&S.line_width, b->bytes, sizeof S.line_width); break;''',
        '    enc_r_scissor(en, sc);': '    enc_r_scissor(en, sc);\n    enc_r_depth_clip(en, !S.depth_clamp);',
        '#ifdef DM_SHELL': '#if 1 /* Compile Zelda MSL with the installed macOS Metal runtime. */',
        'static int stage_of(NSString *zh) {': '''static int stage_of(NSString *zh) {
    // New maps use English; the upstream cases below keep existing maps readable.
    if ([zh isEqualToString:@"vertex"]) return 0; if ([zh isEqualToString:@"pixel"]) return 1;
    if ([zh isEqualToString:@"compute"]) return 5; if ([zh isEqualToString:@"hull"]) return 3;
    if ([zh isEqualToString:@"domain"]) return 4;''',
        'uint8_t md[32]; CC_SHA256(p->sd[i].control, 0x880, md);':
        '''uint8_t md[32]; if (!zelda_shader_hash(p->sd[i].control, (const void *)(uintptr_t)p->sd[i].data, md)) {
            const void *ctl = p->sd[i].control, *code = (const void *)(uintptr_t)p->sd[i].data;
            if (ctl && code) MLOG("SHADER_LAYOUT magic=%x major=%u version=%u length=%u program=%u constants=%u c1=%u total=%u offset=%u stage=%u code_magic=%x",
                shader_word(ctl, 0), shader_word(ctl, 4), shader_word(ctl, 8), shader_word(ctl, 0x20),
                shader_word(ctl, 0x6f8), shader_word(ctl, 0x6fc), shader_word(ctl, 0x700), shader_word(ctl, 0x704),
                shader_word(ctl, 0x708), shader_word(ctl, 0x714), shader_word(code, 0));
            MDIE("Zelda shader data layout is unsupported");
        }''',
        'MLOG("【\u7ea2】\u7740\u8272\u5668\u63a7\u5236\u6bb5 %s \u4e0d\u5728\u6620\u5c04\u8868\u91cc", k.UTF8String); continue;':
        '''for (unsigned j = 0; j < p->nstage; j++) {
            const NVNshaderData *sd = &p->sd[j]; uint8_t hash[32];
            if (!zelda_shader_hash(sd->control, (const void *)(uintptr_t)sd->data, hash)) MDIE("Invalid runtime shader");
            NSString *key = [NSString stringWithFormat:@"%02x%02x%02x%02x%02x%02x%02x%02x", hash[0], hash[1], hash[2], hash[3], hash[4], hash[5], hash[6], hash[7]];
            if (g_shmap[key]) continue;
            NSMutableData *record = [NSMutableData dataWithBytes:sd->control length:shader_word(sd->control, 0x20)];
            [record appendBytes:(const void *)(uintptr_t)sd->data length:shader_word(sd->control, 0x704)];
            NSString *path = [NSString stringWithFormat:@"work/shaders/source/runtime-%@.nvn", key];
            if (![record writeToFile:path options:NSDataWritingAtomic error:NULL]) MDIE("Cannot capture runtime shader");
        }
        MLOG("Captured Zelda shader %s", k.UTF8String);
        zelda_compile_shaders();
        g_shmap = nil; shmap_load(); zh = g_shmap[k];
        if (!zh) { MLOG("BLOCKED shader %s absent after indexing", k.UTF8String); exit(78); }''',
        'else MLOG("【\u7ea2】\u7740\u8272\u5668 %s（%s）\u52a0\u8f7d\u5931\u8d25：\u5148\u8dd1 dm/gfx/tools/dmsh_build.py \u51fa\u5305", k.UTF8String, zh.UTF8String);':
        'else MDIE("Zelda shader %s could not be loaded", k.UTF8String);',
        'for (int r = 0; r < 128; r++) { uint32_t th = (uint32_t)S.tex[nvn_stage][r], sh_ = (uint32_t)S.samp[nvn_stage][r];':
        'for (int r = 0; r < 32; r++) memcpy(d + 0x20 + 8 * r, &S.tex[nvn_stage][r], 8);\n                    for (int r = 0; r < 128; r++) { uint32_t th = (uint32_t)S.tex[nvn_stage][r], sh_ = (uint32_t)S.samp[nvn_stage][r];',
        'static void draw(const p_draw *dw, const p_draw_idx *di) {':
        '''#include "nvn_index.inc"
#include "nvn_cube_draw.inc"
#include "nvn_framebuffer_fetch.inc"
#include "nvn_sample_range.inc"
#include "nvn_line_width.h"
#include "nvn_wide_line.inc"
#include "nvn_point_params.inc"
static void zelda_buffer_targets_written(void) {
    if (!z_index_count && !z_uniform_count) return;
    for (int i = 0; i < R.rt.n && i < 8; i++) if (R.rt.color[i])
        zelda_buffer_texture_written(R.rt.color[i]);
    if (R.rt.depth) zelda_buffer_texture_written(R.rt.depth);
}
static uint64_t z_conditional_gpu;
#include "nvn_vertex_streams.h"
static uint64_t z_vertex_bindings, z_vertex_skipped;
#include "nvn_fullscreen_compute.inc"
#include "nvn_texture_writeback.inc"
static void draw(const p_draw *dw, const p_draw_idx *di) {
    if (dw ? !dw->count || !dw->instances : !di->count || !di->instances) return;''',
        '    int prim = dw ? dw->prim : di->prim;': '''    int prim = dw ? dw->prim : di->prim;
    if (S.line_width != 1 && zelda_uses_line_width(prim, S.poly.mode) && !pr->sh[0]->z_wide_line)
        MDIE("Unsupported wide-line draw width=%g primitive=%d polygon=%d vs=%s fs=%s", S.line_width, prim, S.poly.mode,
            pr->sh[0] ? pr->sh[0]->key : "-", pr->sh[1] ? pr->sh[1]->key : "-");''',
        '    enc_r_vbuf(en, 30, zero_gpu);': '''    enc_r_vbuf(en, 30, zero_gpu);
    uint32_t face = pr->sh[0] && pr->sh[0]->z_layered && z_cube_active ? (uint32_t)z_cube_requested : 0;
    uint64_t point_gpu = zelda_point_params(S.point_size, face);
    enc_r_vbuf(en, 15, point_gpu);
    if (pr->sh[0] && pr->sh[0]->z_layered) enc_r_vbuf(en, 14, point_gpu + 4);''',
        '    if (dw && prim == 7) {': '''    if (pr->sh[0] && pr->sh[0]->z_wide_line) {
        zelda_line_draw(en, &z_line, dw ? dw->instances : di->instances,
            S.has_vp ? S.vp[0][2] : tw, (S.has_vp ? S.vp[0][3] : th) * vs);
    } else if (dw && prim == 7) {''',
        '    if (S.poly.cull == NVN_FACE_FRONT_AND_BACK) return;\n    // \u7eed\u7528\u7684\u901a\u9053':
        '    if (S.poly.cull == NVN_FACE_FRONT_AND_BACK && (dw ? dw->prim : di->prim) >= NVN_DRAW_PRIMITIVE_TRIANGLES) return;\n    // \u7eed\u7528\u7684\u901a\u9053',
        '    if (S.poly.cull == NVN_FACE_FRONT_AND_BACK) return;\n    enc_r_cull(en,':
        '    if (S.poly.cull == NVN_FACE_FRONT_AND_BACK && (dw ? dw->prim : di->prim) >= NVN_DRAW_PRIMITIVE_TRIANGLES) return;\n    enc_r_cull(en,',
        '    for (int s_ = 0; s_ < 16; s_++) {\n        if (!S.vbuf[s_].address) continue;':
        '''    static int z_filter_streams = -1;
    if (z_filter_streams < 0) { const char *e = getenv("ZELDA_VERTEX_STREAM_FILTER"); z_filter_streams = !e || atoi(e); }
    unsigned z_streams = z_filter_streams && pr->sh[0] && !pr->sh[0]->z_wide_line ? zelda_vertex_streams(&pr->sh[0]->rf, S.attr, S.nattr) : 0xffff;
    for (int s_ = 0; s_ < 16; s_++) {
        if (!S.vbuf[s_].address) continue;
        if (!(z_streams & (1u << s_))) { z_vertex_skipped++; continue; }
        z_vertex_bindings++;''',
        '#include "nvn_pso_skey.h"':
        '#include "nvn_pso_skey.h"\n#include "nvn_pso_key.h"',
        '    for (int v = 0; v < rf->nv; v++) { int a = rf->v[v].nvn; if (a < 16 && a < S.nattr) { k->attr[a] = S.attr[a]; int st = S.attr[a].stream; if (st < 16) k->stream[st] = S.stream[st]; } }':
        '    for (int v = 0; v < rf->nv; v++) { int a = rf->v[v].nvn; if (a < 16 && a < S.nattr) { k->attr[a] = S.attr[a]; int st = S.attr[a].stream; if (st < 16) k->stream[st] = S.stream[st]; } }\n    zelda_pso_normalize(k, lmap);',
        'pso_key k = sk.k; k.vs = (__bridge void *)vs; k.fs = (__bridge void *)fs; k.fsfn = (__bridge void *)fsfn;':
        'pso_key k = sk.k; k.vs = (__bridge void *)vs; k.fs = (__bridge void *)fs; k.fsfn = (__bridge void *)fsfn; zelda_pso_normalize(&k, sk.merged);',
        'if (dw->count) enc_r_draw(en, mp, dw->first, dw->count);':
        '''if (dw->count && dw->instances) {
            if (z_conditional_gpu) [(id<MTL4RenderCommandEncoder>)en drawPrimitives:mp indirectBuffer:z_conditional_gpu];
            else enc_r_draw_instanced(en, mp, dw->first, dw->count, dw->instances, dw->base_instance);
        }''',
        'g_quad_gpu, g_quad_ib.length, 1, dw->first, 0);':
        'g_quad_gpu, g_quad_ib.length, dw->instances, dw->first, dw->base_instance);',
        'di->instances ? di->instances : 1, di->base_vertex, di->base_instance);':
        'di->instances, di->base_vertex, di->base_instance);',
        'static int attr_read(int a, const p_draw *dw, const p_draw_idx *di, float P[64][4]) {':
        '''static int attr_read(int a, const p_draw *dw, const p_draw_idx *di, float P[64][4]) {
    if (dw ? dw->instances != 1 || dw->base_instance : di->instances != 1 || di->base_instance) return 6;''',
        'frz_plain:;': '''frz_plain:;
    zelda_query_prepare_draw(pr, di);
    zelda_uniform_prepare_draw(pr, di);
    zelda_inline_prepare_draw(pr, di);
    // A fullscreen draw that would open a pass of its own can run as kernels in the copy/compute encoder.
    if (!z_conditional_gpu && zelda_fsc_draw(pr, dw, di)) return;
    zelda_line_params z_line = {0};
    pr = zelda_line_prepare(pr, dw, di, &z_line);
    pr = zelda_cube_prepare(pr, dw ? dw->prim : di->prim);
    // A draw that samples a target with a pending conditional clear reads memory, so the clear lands first.
    if (!g_enc && z_color_clear.count && draw_samples_rt_mask(pr, zelda_color_clear_targets(), 0)) rp_flush();
    unsigned z_fetched = 0;
    if (!z_depth_clear.pending) pr = zelda_fetch_prepare(pr, z_pass_written, &z_fetched);
    if ((g_enc || z_depth_clear.pending) && draw_samples_rt_mask(pr, z_pass_written & ~z_fetched, z_pass_depth_written || z_depth_clear.pending)) rp_flush();
    zelda_color_targets(S.chmask.mask, pr->sh[1] ? pr->sh[1]->rf.color_mask : 0, z_depth_clear.pending || z_color_clear.count);
    zelda_note_draw_writes((S.ds.dt && S.ds.dw) || S.ds.st);
    uint64_t z_index_gpu = di && !pr->sh[0]->z_wide_line && di->itype == 0 && di->count ? zelda_expand_indices(di) : 0;''',
        'R.clear_c[c->index] = 1; R.cc[c->index] = MTLClearColorMake(c->rgba[0], c->rgba[1], c->rgba[2], c->rgba[3]);':
        'PD("C c%d rgba=%g,%g,%g,%g mask=%x", c->index, c->rgba[0], c->rgba[1], c->rgba[2], c->rgba[3], c->mask);\n                R.clear_c[c->index] = 1; R.cc[c->index] = MTLClearColorMake(c->rgba[0], c->rgba[1], c->rgba[2], c->rgba[3]);',
        'PD("R %llu st%d ser=%llu", g_stat_pass, nvn_stage, g_treg[tid].t.serial);':
        'PD("R %llu st%d ser=%llu mem=%p size=%llu swizzle=%#x/%#x", g_stat_pass, nvn_stage, g_treg[tid].t.serial, g_treg[tid].t.mem, (unsigned long long)g_treg[tid].t.L.size, g_treg[tid].t.swizzle, g_treg[tid].has_v ? g_treg[tid].v.swizzle : 0);\n            zelda_note_render_input(sh->key, reg, t);',
        'sid=%d mag=%d min=%d（0 \u6700\u8fd1\u70b9 / 1 \u53cc\u7ebf\u6027；2–5 \u5e26 mip）", g_stat_pass, nvn_stage, reg, sid, (int)sp.magf, (int)sp.minf);':
        'sid=%d mag=%d min=%d lod=%g..%g aniso=%g", g_stat_pass, nvn_stage, reg, sid, (int)sp.magf, (int)sp.minf, sp.lod_min, sp.lod_max, sp.aniso);',
        'PD("P %llu c%d ser=%llu fmt=%lu %lux%lu ms%lu lv%lu sl%lu %s",':
        'PD("A %llu c%d ser=%llu mem=%p size=%llu", g_stat_pass + 1, i, R.rt.color[i]->serial, R.rt.color[i]->mem, (unsigned long long)R.rt.color[i]->L.size);\n        PD("P %llu c%d ser=%llu fmt=%lu %lux%lu ms%lu lv%lu sl%lu %s",',

        '        if (di->itype == 0) { RED_ONCE(21, "8 \u4f4d\u7d22\u5f15\u672a\u5b9e\u73b0"); return; }': '',
        # build_shaders.py POINT_SIZE_LIMIT: vertex shaders clamp gl_PointSize to c0[0x610].x. The Switch driver's value
        # is not measured; the host leaves the limit to the Metal rasterizer's own maximum point size.
        '                    if (base < 0) RED_ONCE(45,':
        '                    if (nvn_stage == 0) { const float point_limit = FLT_MAX; memcpy(d + 0x610, &point_limit, sizeof point_limit); }\n'
        '                    if (base < 0) RED_ONCE(45,',
        '        if (!mtl_gpu_addr(di->ibuf, 1, &mb, &mo, &gpu))':
        '        if (di->itype == 0) { gpu = z_index_gpu; mb = NULL; mo = 0; }\n        else if (!mtl_gpu_addr(di->ibuf, (uint64_t)di->count * (di->itype == 1 ? 2 : 4), &mb, &mo, &gpu))',
        '        use_rec("buf", di->ibuf, (uint64_t)di->count * (di->itype == 1 ? 2 : 4), 0, NULL);':
        '        use_rec("buf", di->ibuf, (uint64_t)di->count * (di->itype == 0 ? 1 : di->itype == 1 ? 2 : 4), 0, NULL);',
        'di->itype == 1 ? MTLIndexTypeUInt16 : MTLIndexTypeUInt32, gpu, ((__bridge id<MTLBuffer>)mb).length - mo,':
        'di->itype <= 1 ? MTLIndexTypeUInt16 : MTLIndexTypeUInt32, gpu, di->itype == 0 ? (uint64_t)di->count * 2 : ((__bridge id<MTLBuffer>)mb).length - mo,',
        # Fullscreen draws as kernels (nvn_fullscreen_compute.inc): their argument buffers bind into the kernels'
        'if (g_lp.count) lp_finish(); fz_end();': 'if (g_lp.count) lp_finish(); fz_end(); zelda_fsc_after_pass();',
        'static void rp_end(void);\n': 'static void rp_end(void);\nstatic void zelda_fsc_after_pass(void);\n',
        # tables, and small colour targets get ShaderWrite.
        'static void bind_args(id<MTLCommandEncoder> en, int nvn_stage, int metal_vertex, id<MTLBuffer> ab, uint64_t gpu, size_t off, NSUInteger ai) {':
        '''static int z_fsc_route;
static void zelda_fsc_bind(int stage, uint64_t gpu, NSUInteger index);
static void bind_args(id<MTLCommandEncoder> en, int nvn_stage, int metal_vertex, id<MTLBuffer> ab, uint64_t gpu, size_t off, NSUInteger ai) {''',
        '    if (nvn_stage == 5) enc_c_args((id<MTLComputeCommandEncoder>)en, gpu, ai);':
        '    if (z_fsc_route) { zelda_fsc_bind(nvn_stage, gpu, ai); return; }\n    if (nvn_stage == 5) enc_c_args((id<MTLComputeCommandEncoder>)en, gpu, ai);',
        'static MTLTextureDescriptor *tex_desc(const tex_t *t, int packaged) {':
        'static MTLTextureUsage zelda_fsc_usage(MTLTextureDescriptor *d, int not_renderable);\nstatic MTLTextureDescriptor *tex_desc(const tex_t *t, int packaged) {',
        '            | (t->d.flags & NVN_TEXTURE_FLAGS_IMAGE ? MTLTextureUsageShaderWrite : 0)':
        '            | (t->d.flags & NVN_TEXTURE_FLAGS_IMAGE ? MTLTextureUsageShaderWrite : 0) | zelda_fsc_usage(d, fmt_compressed(t->d.format) || packaged)',
    }
    for original, replacement in replacements.items():
        if source.count(original) != 1:
            raise ValueError('NVN Metal shader implementation changed')
        source = source.replace(original, replacement)
    source, shader_builds = re.subn(
        r'\[g_dev new(?:LibraryWithSource|RenderPipelineStateWithDescriptor|ComputePipelineStateWithFunction):.*?\berror:&?\w+\]',
        lambda match: 'ZELDA_SHADER_BUILD(' + match[0] + ')', source, flags=re.S)
    if shader_builds != 20:
        raise ValueError('NVN shader compilation call sites changed')
    source = '#include "timeline.h"\n#include "shader_status.h"\n' + source
    start = source.index('static inline void enc_r_viewport(')
    end = source.index('static inline void enc_r_visibility(', start)
    source = source[:start] + source[end:]
    anchor = '#if DM_NVN_M3\nstatic inline RENC enc_r_begin('
    if source.count(anchor) != 1:
        raise ValueError('NVN render encoder entry changed')
    source = source.replace(anchor, '#include "nvn_encoder_state.inc"\n' + anchor)
    source = source.replace('static inline RENC enc_r_begin(RPD *d) {',
                            'static inline RENC enc_r_begin(RPD *d) { zelda_encoder_state_reset();')
    start = source.index('// ---------- DM_NVN_PASSGPU=<\u6587\u4ef6>')
    end = source.index('void ts_after(void);', start)
    source = source[:start] + '#include "nvn_profile_gpu.inc"\n' + source[end:]
    source = source.replace('dw ? 1 : di->instances', 'dw ? dw->instances : di->instances')
    # Every draw and lookahead reader must observe the borrowed array at submission.
    source, changed = re.subn(r'\bb->h\b', 'nvn_bound_handles(b)', source)
    if changed < 10:
        raise ValueError('NVN deferred binding readers changed')
    anchor = '    rp->view = (void *)CFBridgingRetain(v);'
    view_function = 'static id<MTLTexture> tex_by_id(int tid) {'
    if source.count(view_function) != 1:
        raise ValueError('NVN texture lookup entry changed')
    source = source.replace(view_function,
                            '#include "nvn_texture_swizzle.inc"\nstatic id<MTLTexture> tex_by_id(int tid) {')
    swizzle = '''    unsigned s = r.has_v && r.v.swizzle ? r.v.swizzle : r.t.swizzle;
    int format = r.has_v && r.v.set_format ? r.v.format : r.t.d.format;
    v = zelda_texture_swizzle(v, format, s);
'''
    if source.count(anchor) != 1:
        raise ValueError('NVN Metal texture view implementation changed')
    return source.replace(anchor, swizzle + anchor)


def adapt_deswizzle(source):
    anchor = '''    uint32_t row_bytes, rows, slices, bpb; uint64_t base; unsigned th, td;
    if (nvn_deswizzle_geom'''
    if source.count(anchor) != 1:
        raise ValueError('NVN texture copy layout changed')
    geometry = '(d->flags & 0x10) || d->samples > 1) return -1;   // MS / BUFFER / LINEAR'
    if source.count(geometry) != 1:
        raise ValueError('NVN texture copy layout changed')
    source = source.replace(geometry, '(d->flags & 0x50) || d->samples > 1) return -1;   // MS / BUFFER / LINEAR / LINEAR_RENDER_TARGET')
    return source.replace(anchor, '''    if (d->flags & 0x50) {   // LINEAR / LINEAR_RENDER_TARGET: pitch-linear rows
        int bw, bh, bytes = nvn_fmt_bpb(d->format, &bw, &bh);
        if (d->target != 1 || level || layer || d->levels > 1 || d->depth > 1 ||
            d->samples > 1 || !d->width || !d->height || !bytes || bw != 1 || bh != 1) return -1;
        nvn_texlayout layout; nvn_tex_layout(d, &layout);
        size_t row = (size_t)d->width * bytes, stride = layout.size / d->height;
        if (pitch < row || stride < row || pitch > SIZE_MAX / d->height) return -1;
        for (uint32_t y = 0; y < d->height; y++) {
            if (to_linear) memcpy(lin + y * pitch, bl + y * stride, row);
            else memcpy(bl + y * stride, lin + y * pitch, row);
        }
        return 0;
    }
''' + anchor)



def adapt_texture_layout(source):
    # NVN_TEXTURE_FLAGS_LINEAR_RENDER_TARGET (0x40) storage is pitch linear even without LINEAR (0x10): the game
    # sets only 0x40 plus an explicit stride and writes the rows with the CPU.
    replacements = {
        '    if (d->flags & 0x10) {                                            // LINEAR':
        '    if (d->flags & 0x50) {                                            // LINEAR / LINEAR_RENDER_TARGET',
        'if (!t.bpb || (d->flags & 0x10) || d->target == 10) return 0;':
        'if (!t.bpb || (d->flags & 0x50) || d->target == 10) return 0;',
        '    o->layer_size = ls;':
        '''    // MINIMAL_LAYOUT uses one-GOB blocks only when they save space; explicit packaged layouts win.
    // v1.4.3 SDK 4b74dc compares the complete mip-chain layer sizes, retaining the default on ties.
    if ((d->flags & 0x200) && !d->pkg_block_h) {
        uint64_t minimal = level_offset(d, levels, dpl, w, h, z, 0, 0, t);
        if (minimal < ls) { o->tileH = o->tileD = 0; ls = minimal; }
    }
    o->layer_size = ls;''',
    }
    for old, new in replacements.items():
        if source.count(old) != 1:
            raise ValueError('NVN texture layout changed')
        source = source.replace(old, new)
    return source


if __name__ == '__main__':
    gfx = Path(__file__).resolve().parents[1] / 'core/gfx'
    target = Path('work/build/hle_nvn.c')
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(adapt_interface((gfx / 'nvn/hle_nvn.c').read_text()))
    target.with_name('hle_nv.c').write_text(adapt_glue((gfx / 'hle_nv.c').read_text()))
    objects = target.with_name('nvn_obj.h')
    objects.write_text(adapt_objects((gfx / 'nvn/nvn_obj.h').read_text()))
    backend = target.with_name('nvn_mtl.m')
    backend.write_text(adapt_backend((gfx / 'nvn/nvn_mtl.m').read_text()))
    deswizzle = target.with_name('nvn_deswizzle.c')
    deswizzle.write_text(adapt_deswizzle((gfx / 'tex/nvn_deswizzle.c').read_text()))
    layout = target.with_name('nvn_tex.c')
    layout.write_text(adapt_texture_layout((gfx / 'nvn/nvn_tex.c').read_text()))
    from build_shaders import chain
    shader_chain = target.with_name('dmsh_chain.h')
    shader_chain.write_text(f'#define DMSH_CHAIN "{chain()}"\n')
    from build_shader_ir import contract
    ir_identity = target.with_name('shader_ir_identity.h')
    ir_identity.write_text(f'#define ZELDA_SHADER_IR_CONTRACT "{contract()}"\n')
    # Overlay all includes consistently, including those inside the unmodified Metal backend.
    target.with_name('graphics-overlay.json').write_text(json.dumps({'version': 0, 'roots': [
        {'type': 'file', 'name': str((gfx / 'nvn/nvn_obj.h').resolve()), 'external-contents': str(objects.resolve())},
        {'type': 'file', 'name': str((gfx / 'nvn/nvn_mtl.m').resolve()), 'external-contents': str(backend.resolve())},
        {'type': 'file', 'name': str((gfx / 'tex/nvn_deswizzle.c').resolve()), 'external-contents': str(deswizzle.resolve())},
        {'type': 'file', 'name': str((gfx / 'nvn/nvn_tex.c').resolve()), 'external-contents': str(layout.resolve())},
        {'type': 'file', 'name': str((gfx / 'nvn/dmsh_chain.h').resolve()), 'external-contents': str(shader_chain.resolve())},
        {'type': 'file', 'name': str((gfx / 'nvn/shader_ir_identity.h').resolve()), 'external-contents': str(ir_identity.resolve())}
    ]}) + '\n')
