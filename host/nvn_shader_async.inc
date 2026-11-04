// Background shader preparation (shader_async.h): the switch, captured shader variants and their shader objects.
// Included before nvn_shader_variant.inc, whose lookups use it.
#include "shader_async.h"
#include "settings.h"
static int z_warming;   // startup warm-up (nvn_shader_warm.inc) prepares everything at once
// -1 until the setting or ZELDA_SHADER_ASYNC is first read.
static _Atomic int z_background = -1;
void zelda_set_shader_wait(bool wait) { atomic_store(&z_background, !wait); }
static int zelda_shader_background(void) {
    int on = atomic_load_explicit(&z_background, memory_order_relaxed);
    if (on < 0) {
        const char *setting = getenv("ZELDA_SHADER_ASYNC");
        int unset = -1; on = setting && *setting ? atoi(setting) != 0 : !zelda_settings().shader_wait;
        if (!atomic_compare_exchange_strong(&z_background, &unset, on)) on = unset;
        MLOG("SHADER_ASYNC %s", on ? "background" : "wait");
    }
    return on && !z_warming;
}

// Captured variants (names with the _b suffix) wait here until their library exists and its prefetch compile, which
// warms the system compiler cache for the draw-time load, has finished. One background run at a time translates every
// captured request; requests arriving meanwhile trigger another run.
static pthread_mutex_t z_variant_mu = PTHREAD_MUTEX_INITIALIZER;
static NSMutableSet<NSString *> *z_variant_waiting;
static int z_variant_running, z_variant_again;
static void zelda_variant_compile_run(void) {
    for (;;) @autoreleasepool {
        pthread_mutex_lock(&z_variant_mu); z_variant_again = 0; pthread_mutex_unlock(&z_variant_mu);
        zelda_compile_shaders();
        pthread_mutex_lock(&z_variant_mu); NSArray<NSString *> *names = z_variant_waiting.allObjects; pthread_mutex_unlock(&z_variant_mu);
        NSString *missing = nil;
        for (NSString *name in names) {
            NSString *library = [g_msl_dir stringByAppendingPathComponent:[name stringByAppendingString:@".metallib"]];
            if (access(library.fileSystemRepresentation, R_OK)) { missing = name; continue; }
            zelda_prefetch(name);
            pthread_mutex_lock(&z_variant_mu); [z_variant_waiting removeObject:name]; pthread_mutex_unlock(&z_variant_mu);
        }
        pthread_mutex_lock(&z_variant_mu);
        if (z_variant_again) { pthread_mutex_unlock(&z_variant_mu); continue; }
        if (missing) { MLOG("BLOCKED shader variant %s remains unsupported", missing.UTF8String); exit(78); }
        z_variant_running = 0; pthread_mutex_unlock(&z_variant_mu);
        return;
    }
}
static void zelda_variant_request(NSString *name) {
    pthread_mutex_lock(&z_variant_mu);
    if (!z_variant_waiting) z_variant_waiting = [NSMutableSet set];
    [z_variant_waiting addObject:name];
    int start = !z_variant_running; z_variant_running = 1; z_variant_again = 1;
    pthread_mutex_unlock(&z_variant_mu);
    if (start) dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{ zelda_variant_compile_run(); });
}
// A variant's shader object loads on a worker thread (shader_load is also used by the startup warm-up workers) and
// joins the shader cache on the submit thread. Sets *pending while the load runs.
static NSMutableDictionary<NSString *, id> *z_variant_loads;   // name -> @YES while loading, then the NvShader or @NO
static NvShader *zelda_variant_load(NSString *name, int stage, int *pending) {
    NvShader *loaded = g_shcache[name];
    if (loaded) return loaded;
    pthread_mutex_lock(&z_variant_mu);
    if (!z_variant_loads) z_variant_loads = [NSMutableDictionary dictionary];
    id state = z_variant_loads[name];
    if (!state) z_variant_loads[name] = @YES;
    else if (![state isEqual:@YES]) [z_variant_loads removeObjectForKey:name];
    pthread_mutex_unlock(&z_variant_mu);
    if (!state) dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        NvShader *made = shader_load(name, stage);
        pthread_mutex_lock(&z_variant_mu); z_variant_loads[name] = made ?: (id)@NO; pthread_mutex_unlock(&z_variant_mu);
    });
    if (!state || [state isEqual:@YES]) { *pending = 1; return nil; }
    if (![state isKindOfClass:[NvShader class]]) return shader_get(name, stage);   // failed: the load at once reports why
    loaded = state; g_shcache[name] = loaded; atomic_fetch_add(&g_shcache_n, 1);
    pso_record("S\t%s\t%d\n", name.UTF8String, stage);
    pso_user_record("S\t%s\t%d\n", name.UTF8String, stage);
    return loaded;
}
static int zelda_variant_busy(NSString *name) {
    pthread_mutex_lock(&z_variant_mu); int waiting = [z_variant_waiting containsObject:name]; pthread_mutex_unlock(&z_variant_mu);
    if (waiting) return 1;
    pthread_mutex_lock(&z_prefetch_mu); int state = z_prefetch_state[name].intValue; pthread_mutex_unlock(&z_prefetch_mu);
    return state == Z_PREFETCH_QUEUED || state == Z_PREFETCH_RUNNING;
}
