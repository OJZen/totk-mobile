#include "shader_status.h"
// Conditional colour clears inside game passes (see nvn_color_clear.h). Included after nvn_query_gpu.inc.
static int zelda_inline_partial_clear(const p_clear_color *clear) {
    static int enabled = -1;
    if (enabled < 0) { const char *value = getenv("ZELDA_INLINE_PARTIAL_CLEAR"); enabled = !value || atoi(value); }
    return enabled && !z_render.address && z_render.enabled && clear->mask == 15 && clear->index >= 0 &&
        clear->index < R.rt.n && R.rt.color[clear->index] &&
        !scissor_full(R.rt.color[clear->index], &R.rt.cview[clear->index], R.rt.has_cview[clear->index]);
}
static void zelda_color_clear_params(const zelda_color_clear_t *c, uint64_t *gpu) {
    id<MTLBuffer> parameters; size_t start = arena_alloc(32, &parameters);
    uint8_t *p = (uint8_t *)parameters.contents + start;
    memcpy(p, c->rgba, 16); memcpy(p + 16, &c->mode, 4); memcpy(p + 20, &c->known, 4);
    *gpu = parameters.gpuAddress + start;
}
// The draw's attachments and formats are the open pass's; only the clear's slot is written, without depth or counting.
static void zelda_color_clear_draw(const zelda_color_clear_t *c) {
    static NSMutableDictionary<NSData *, id<MTLRenderPipelineState>> *pipelines;
    static id<MTLDepthStencilState> state;
    if (!g_enc || z_cube_active) MDIE("Conditional colour clear has no flat pass");
    zelda_query_init();
    if (!pipelines) {
        pipelines = [NSMutableDictionary dictionary];
        MTLDepthStencilDescriptor *d = [MTLDepthStencilDescriptor new]; d.depthCompareFunction = MTLCompareFunctionAlways;
        state = [g_dev newDepthStencilStateWithDescriptor:d];
        if (!state) MDIE("Conditional colour clear state creation failed");
    }
    uint32_t key[12]; memcpy(key, g_pass.cfmt, 8 * sizeof(uint32_t));
    key[8] = g_pass.dfmt; key[9] = g_pass.sfmt; key[10] = MAX(g_pass.samples, 1u); key[11] = (uint32_t)c->index;
    NSData *name = [NSData dataWithBytes:key length:sizeof key];
    id<MTLRenderPipelineState> pipeline = pipelines[name];
    if (!pipeline) {
        MTLRenderPipelineDescriptor *d = [MTLRenderPipelineDescriptor new];
        d.vertexFunction = [z_query_library newFunctionWithName:@"clear_vertex"];
        d.fragmentFunction = [z_query_library newFunctionWithName:[NSString stringWithFormat:@"clear_if%d", c->index]];
        for (int i = 0; i < 8; i++) {
            d.colorAttachments[i].pixelFormat = key[i];
            d.colorAttachments[i].writeMask = i == c->index ? MTLColorWriteMaskAll : MTLColorWriteMaskNone;
        }
        d.depthAttachmentPixelFormat = key[8]; d.stencilAttachmentPixelFormat = key[9]; d.rasterSampleCount = key[10];
        NSError *error = nil; pipeline = ZELDA_SHADER_BUILD([g_dev newRenderPipelineStateWithDescriptor:d error:&error]);
        if (!pipeline) MDIE("Inline colour clear pipeline failed: %s", error.localizedDescription.UTF8String);
        pipelines[name] = pipeline;
    }
    uint64_t parameters; zelda_color_clear_params(c, &parameters);
    [z_query_args setAddress:c->query atIndex:0]; [z_query_args setAddress:parameters atIndex:1];
    [g_enc setArgumentTable:z_query_args atStages:(MTLRenderStageVertex | MTLRenderStageFragment)];
    enc_r_pso(g_enc, pipeline); enc_r_dss(g_enc, state);
    enc_r_viewport(g_enc, (MTLViewport){0, 0, c->width, c->height, 0, 1});
    enc_r_scissor(g_enc, (MTLScissorRect){c->x, c->y, c->right - c->x, c->bottom - c->y});
    enc_r_cull(g_enc, MTLCullModeNone); enc_r_fill(g_enc, MTLTriangleFillModeFill);
    enc_r_depth_bias(g_enc, 0, 0, 0); enc_r_depth_clip(g_enc, YES);
    // Clears do not count samples. A counting slot is never reselected inside an encoder, so the next counted draw
    // takes a new slot (queries sum every slot since their reset).
    if (g_enc_vis) { enc_r_visibility(g_enc, MTLVisibilityResultModeDisabled, 0); g_vis_cur = -1; }
    [g_enc drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
    PD("c %llu inline conditional clear c%d %ldx%ld known=%u", g_stat_pass, c->index, c->width, c->height, c->known);
    [g_enc setArgumentTable:g_vt atStages:MTLRenderStageVertex];
    [g_enc setArgumentTable:g_ft atStages:MTLRenderStageFragment];
    g_enc_pso = nil; g_enc_dss = nil; g_enc_vis = 0;
    args_dirty_all(); g_pso_dirty = g_dss_dirty = 1;
    z_pass_written |= 1u << c->index;
    for (int i = 0; i < g_nsd; i++) if (g_sd[i].idx == c->index && !g_sd[i].depth) g_sd[i].wrote = 1;
}
static int zelda_color_clear_join(zelda_color_clear_t *c) {
    static int enabled = -1;
    if (enabled < 0) { const char *value = getenv("ZELDA_INLINE_CLEAR"); enabled = !value || atoi(value); }   // 0: own passes
    if (!enabled || tbdr_on() || z_depth_clear.pending || z_cube_active) return 0;
    if (c->predicate) { if (!zelda_query_forward(c->predicate, &c->known)) return 0; }
    else c->known = 3; // An unconditional partial clear uses the same quad, with both predicate words fixed at zero.
    if (g_enc) {
        if (!(z_pass_attached >> c->index & 1) || (long)g_pass.tw != c->width || (long)g_pass.th != c->height || g_pass.samples > 1) return 0;
        zelda_color_clear_draw(c); z_color_clear_joined++;
        return 1;
    }
    if (z_color_clear.count == Z_COLOR_CLEAR_MAX) return 0;
    z_color_clear.item[z_color_clear.count++] = *c; z_color_clear_deferred++;
    return 1;
}
// Before the pass that takes the pending clears opens: a clear whose target is not the whole render area of that pass
// gets its own pass first (stream order is kept, the game pass has not started).
static void zelda_color_clear_unfit(RPD *pass) {
    int kept = 0;
    for (int i = 0; i < z_color_clear.count; i++) {
        zelda_color_clear_t *c = &z_color_clear.item[i];
        if (pass.colorAttachments[c->index].texture && (long)g_pass.tw == c->width && (long)g_pass.th == c->height && g_pass.samples <= 1) {
            z_color_clear.item[kept++] = *c; continue;
        }
        MTLRenderPassColorAttachmentDescriptor *attachment = [MTLRenderPassColorAttachmentDescriptor new];
        attach(attachment, R.rt.color[c->index], &R.rt.cview[c->index], R.rt.has_cview[c->index], 1);
        zelda_color_clear_alone(attachment, c);
    }
    z_color_clear.count = kept;
}
static void zelda_color_clear_encode(void) {
    int count = z_color_clear.count;
    z_color_clear.count = 0;
    if (g_enc_mode == 1) z_color_clear_flushed += (uint64_t)count;   // Landed in a pass opened only for them.
    for (int i = 0; i < count; i++) zelda_color_clear_draw(&z_color_clear.item[i]);
}
static int zelda_color_clear_touches(uint64_t address, uint64_t length) {
    uint64_t source = nvn_canonical_address(address, length);
    if (!source) return 1;
    for (int i = 0; i < z_color_clear.count; i++) {
        const zelda_color_clear_t *c = &z_color_clear.item[i];
        const tex_t *target = R.rt.color[c->index];
        uint64_t predicate = nvn_canonical_address(c->predicate, 32);
        uint64_t memory = target ? nvn_canonical_address((uint64_t)target->mem, target->L.size) : 0;
        if ((c->predicate && !predicate) || !memory) return 1;
        if ((c->predicate && source < predicate + 32 && predicate < source + length) ||
            (source < memory + target->L.size && memory < source + length)) return 1;
    }
    return 0;
}
// Commands that may run before pending clears (whose quads open the next pass) without observing the difference.
static int zelda_color_clear_passthrough(unsigned op, const void *data) {
    switch (op) {
        case OP_BEGIN: case OP_END: case OP_JUMP: case OP_CALL: case OP_RESET_COUNTER: case OP_RENDER_ENABLE:
        case OP_SCISSOR: case OP_BIND_PROGRAM: case OP_BIND_STATE: case OP_BIND_VATTR: case OP_BIND_VSTREAM: case OP_BIND_VBUF:
        case OP_BIND_UBO: case OP_BIND_SSBO: case OP_BIND_TEX: case OP_BIND_SAMP: case OP_BIND_IMG: case OP_STENCIL:
        case OP_POLY_OFFSET: case OP_VIEWPORTS: case OP_DEPTH_RANGE: case OP_BLEND_COLOR: case OP_COPY_STRIDE:
        case OP_SET_TEX_POOL: case OP_SET_SAMP_POOL: case OP_DRAW: case OP_DRAW_IDX: return 1;
        case OP_CLEAR_COLOR: return z_render.address != 0 || zelda_inline_partial_clear(data);
        case OP_CLEAR_DS: {
            int keep = zelda_depth_color_join(); z_depth_color_kept += keep; return keep;
        }
        case OP_REPORT_COUNTER: {
            uint64_t type; memcpy(&type, data, 8);
            return type == NVN_COUNTER_TYPE_TIMESTAMP || type == NVN_COUNTER_TYPE_TIMESTAMP_TOP || type == NVN_COUNTER_TYPE_SAMPLES_PASSED;
        }
        case OP_UPDATE_UB: {
            uint64_t address, length; memcpy(&address, data, 8); memcpy(&length, (const uint8_t *)data + 8, 8);
            return length && !zelda_color_clear_touches(address, length);
        }
        default: return 0;
    }
}
// Pass-structure counters, about every 30 seconds at 60 FPS (called when a submission commits).
static void zelda_counters_log(void) {
    static uint64_t last;
    uint64_t frame = atomic_load(&g_cur_frame);
    if (frame < last + 1800) return;
    last = frame;
    MLOG("Encoder state frame=%llu sent=%llu reused=%llu", (unsigned long long)frame,
        (unsigned long long)z_encoder_state_sent, (unsigned long long)z_encoder_state_reused);
    MLOG("Inline partial clears frame=%llu count=%llu", (unsigned long long)frame, (unsigned long long)z_partial_clear_inline);
    MLOG("Texture lookup frame=%llu cached=%llu", (unsigned long long)frame, (unsigned long long)z_texture_lookup_hits);
    MLOG("Driver parameters frame=%llu allocated=%llu reused=%llu", (unsigned long long)frame,
        (unsigned long long)z_point_params_created, (unsigned long long)z_point_params_reused);
    MLOG("Texture edge snapshots frame=%llu created=%llu kept=%llu", (unsigned long long)frame,
        (unsigned long long)z_texture_edges_created, (unsigned long long)z_texture_edges_kept);
    MLOG("Lazy depth clears frame=%llu query_forwards=%llu reports_kept=%llu color_kept=%llu color_joined=%llu", (unsigned long long)frame,
        (unsigned long long)z_depth_query_forwards, (unsigned long long)z_depth_reports_kept,
        (unsigned long long)z_depth_color_kept, (unsigned long long)z_depth_color_joined);
    MLOG("Vertex streams frame=%llu bound=%llu unused=%llu", (unsigned long long)frame,
        (unsigned long long)z_vertex_bindings, (unsigned long long)z_vertex_skipped);
    if (getenv("DM_MEM_STAT_ALL")) {
        os_unfair_lock_lock(&g_c1_lock);
        MLOG("c1 constants: count=%llu full_buffers=%llu blocks=%lu allocated=%zu bytes original=%zu bytes reused=%llu",
            (unsigned long long)g_c1_count, (unsigned long long)g_c1_exclusive, (unsigned long)g_c1_banks.count,
            g_c1_alloc_bytes, g_c1_data_bytes, (unsigned long long)z_c1_hits);
        os_unfair_lock_unlock(&g_c1_lock);
    }
    MLOG("Pass counts through frame %llu: cube_batches=%llu draws=%llu; framebuffer_fetch_draws=%llu; conditional_clear joined=%llu deferred=%llu"
         " (new_passes=%llu) standalone=%llu; query_read_flushes=%llu; uniforms_deferred=%llu snapshot_draws=%llu flush_batches=%llu pass_breaks=%llu"
         " barriers_kept=%llu",
        (unsigned long long)frame, (unsigned long long)z_cube_passes, (unsigned long long)z_cube_draws, (unsigned long long)z_fetch_draws,
        (unsigned long long)z_color_clear_joined, (unsigned long long)z_color_clear_deferred, (unsigned long long)z_color_clear_flushed,
        (unsigned long long)z_color_clear_alone, (unsigned long long)z_query_read_flushes, (unsigned long long)z_inline_deferred,
        (unsigned long long)z_inline_snapshots, (unsigned long long)z_inline_flushes, (unsigned long long)z_inline_breaks,
        (unsigned long long)z_barriers_kept);
    MLOG("Between-pass uniforms through frame %llu: deferred=%llu replaced=%llu next_pass_writes=%llu blit_fallback=%llu", (unsigned long long)frame,
        (unsigned long long)z_write_deferred, (unsigned long long)z_write_replaced, (unsigned long long)z_write_in_pass, (unsigned long long)z_write_copied);
}
