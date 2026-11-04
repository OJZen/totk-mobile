// Background render pipelines (shader_async.h). A pipeline the draw path lacks goes to the parallel precompile table
// (pso_pend_kick) instead of compiling on the submit thread. The draw waits for it from a per-frame budget
// (ZELDA_SHADER_ASYNC_BUDGET_MS, default 3) and is skipped once the budget is spent; later draws take the pipeline as
// soon as it is built. Merged readable-tile pipelines depend on the replay's plan and still compile at once.
static uint64_t z_async_frame, z_async_budget_ns, z_async_skips, z_async_waited_ns, z_async_frames_skipping, z_async_last_frame;

// The shaders a pipeline links must be loaded: a variant still being translated, compiled or loaded, or a depth-free
// fragment copy still compiling, keeps it from starting.
static int zelda_pso_inputs_ready(const pso_key *k, NvShader *vs, NvShader *fs) {
    if (!zelda_shader_background()) return 1;
    if (!zelda_vertex_function(vs, k->attr, fs)) return 0;
    id<MTLFunction> fragment = fs ? zelda_fragment_function(fs, k->cfmt) : nil;
    return !fs || (fragment && (k->dfmt || zelda_depthless_ready(fragment, fs)));
}

// Predicted pipelines nobody drew stay in the table; when it is full the finished ones move into the pipeline cache.
static void zelda_pso_pend_harvest(void) {
    pthread_mutex_lock(&g_pp_mu);
    for (int i = 0; i < PSO_PEND_MAX; i++) {
        if (!g_pp[i].used || !g_pp[i].done) continue;
        if (g_pp[i].ps && !hm_get_id(&g_pso, &g_pp[i].k)) hm_put_id(&g_pso, &g_pp[i].k, g_pp[i].ps);
        g_pp[i].ps = nil; g_pp[i].used = 0;
    }
    pthread_mutex_unlock(&g_pp_mu);
}

static void zelda_pso_async_note(int skipped, uint64_t waited) {
    uint64_t frame = atomic_load(&g_cur_frame);
    z_async_waited_ns += waited;
    if (!skipped) return;
    if (!z_async_skips++ || frame != z_async_last_frame) z_async_frames_skipping++;
    z_async_last_frame = frame;
    if (z_async_skips == 1 || z_async_skips % 500 == 0)
        MLOG("SHADER_ASYNC skipped_draws=%llu frames=%llu waited_ms=%.1f", (unsigned long long)z_async_skips,
             (unsigned long long)z_async_frames_skipping, z_async_waited_ns / 1e6);
}

// Returns the built pipeline, or nil with *skip set while it is still being prepared. Nil without *skip means the
// table had no room, and the caller compiles at once as before.
static id<MTLRenderPipelineState> zelda_pso_background(const pso_key *k, NvShader *vs, NvShader *fs, int *skip) {
    static uint64_t budget = UINT64_MAX;
    if (budget == UINT64_MAX) {
        const char *setting = getenv("ZELDA_SHADER_ASYNC_BUDGET_MS");
        budget = (uint64_t)((setting && *setting ? MAX(0, atof(setting)) : 3) * 1e6);
    }
    uint64_t frame = atomic_load(&g_cur_frame);
    if (frame != z_async_frame) { z_async_frame = frame; z_async_budget_ns = budget; }
    static int log = -1; if (log < 0) log = getenv("ZELDA_PSO_LOG") != NULL;
    uint64_t t0 = bp_now();
    if (g_pp_submit_mark != g_sub_serial) { g_pp_submit_mark = g_sub_serial; pso_predict_rest(); }
    uint64_t t1 = bp_now();
    if (log && t1 - t0 > 5000000) MLOG("[pso-async] frame=%llu predict_ms=%.1f", (unsigned long long)frame, (t1 - t0) / 1e6);
    pthread_mutex_lock(&g_pp_mu); int at = pso_pend_find(k); pthread_mutex_unlock(&g_pp_mu);
    if (at < 0) {
        int ready = zelda_pso_inputs_ready(k, vs, fs);
        uint64_t t2 = bp_now();
        if (log && t2 - t1 > 5000000) MLOG("[pso-async] frame=%llu inputs_ms=%.1f vs=%s fs=%s", (unsigned long long)frame, (t2 - t1) / 1e6, vs->key, fs ? fs->key : "-");
        if (!ready) { *skip = 1; zelda_pso_async_note(1, 0); return nil; }
        pso_pend_kick(k, vs, fs);
        if (log && bp_now() - t2 > 5000000) MLOG("[pso-async] frame=%llu kick_ms=%.1f vs=%s", (unsigned long long)frame, (bp_now() - t2) / 1e6, vs->key);
        pthread_mutex_lock(&g_pp_mu); at = pso_pend_find(k); pthread_mutex_unlock(&g_pp_mu);
        if (at < 0) {
            zelda_pso_pend_harvest(); pso_pend_kick(k, vs, fs);
            pthread_mutex_lock(&g_pp_mu); at = pso_pend_find(k); pthread_mutex_unlock(&g_pp_mu);
            if (at < 0) return nil;
        }
    }
    uint64_t start = bp_now();
    id<MTLRenderPipelineState> ps = nil;
    pthread_mutex_lock(&g_pp_mu);
    while (!g_pp[at].done) {
        uint64_t spent = bp_now() - start;
        if (spent >= z_async_budget_ns) break;
        struct timespec until; clock_gettime(CLOCK_REALTIME, &until);
        uint64_t left = z_async_budget_ns - spent, nanos = (uint64_t)until.tv_nsec + left;
        until.tv_sec += (time_t)(nanos / 1000000000ull); until.tv_nsec = (long)(nanos % 1000000000ull);
        pthread_cond_timedwait(&g_pp_cv, &g_pp_mu, &until);
    }
    int done = g_pp[at].done;
    if (done) { ps = g_pp[at].ps; g_pp[at].ps = nil; g_pp[at].used = 0; }
    pthread_mutex_unlock(&g_pp_mu);
    uint64_t waited = bp_now() - start;
    z_async_budget_ns -= MIN(waited, z_async_budget_ns);
    // A failed background build is repeated at once so the failure is reported with its error.
    *skip = !done;
    zelda_pso_async_note(!done, waited);
    return ps;
}
