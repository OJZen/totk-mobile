#include <math.h>
#include <stdatomic.h>
NVN(nvnColorStateSetLogicOp, void, NVNcolorState *state, NVNlogicOp op) {
    // shortcut: Metal's normal color path implements COPY; other Boolean operations need shader support.
    if (op != NVN_LOGIC_OP_COPY) { LOG("BLOCKED NVN color logic operation=%d", op); exit(78); }
    ((color_st *)state)->logic_op = op;
}
// shortcut: the alpha test (drop a fragment whose colour 0 alpha fails the comparison with the reference) is recorded
// but not applied: the translated pixel shaders are built with the test off and Metal has no fixed-function one, so
// alpha-tested draws keep their failing fragments. TotK selects it in some cutscenes; drawing on beats stopping there.
static void zelda_alpha_test_note(const char *what, double value) {
    static _Atomic int noted;
    (void)what; (void)value;   // LOG may expand to nothing
    if (!atomic_exchange(&noted, 1)) LOG("NVN alpha test %s=%g requested; drawn without the test", what, value);
}
NVN(nvnColorStateSetAlphaTest, void, NVNcolorState *state, NVNalphaFunc func) {
    if (func < NVN_ALPHA_FUNC_NEVER || func > NVN_ALPHA_FUNC_ALWAYS) { LOG("BLOCKED NVN alpha function=%d", func); exit(78); }
    ((color_st *)state)->alpha_func = func;
    if (func != NVN_ALPHA_FUNC_ALWAYS) zelda_alpha_test_note("function", func);
}
NVN(nvnColorStateGetAlphaTest, NVNalphaFunc, const NVNcolorState *state) { return (NVNalphaFunc)((const color_st *)state)->alpha_func; }
NVN(nvnCommandBufferSetAlphaRef, void, NVNcommandBuffer *c, float ref) { (void)c; zelda_alpha_test_note("reference", ref); }
NVN(nvnCommandBufferSetDepthClamp, void, NVNcommandBuffer *c, uint32_t enable) {
    const uint8_t clamp = !!(enable & 255);
    bind_state(c, 6, &clamp, sizeof clamp);
}
// Metal restarts indexed strips at the largest value of the index type and cannot turn that off; the draw checks
// the recorded NVN state against it (v1.4.3 enables restart at 0xFFFF after the opening movie).
typedef struct { uint8_t enable, pad[3]; uint32_t index; } zelda_restart_st;
NVN(nvnCommandBufferSetPrimitiveRestart, void, NVNcommandBuffer *c, uint32_t enable, int32_t index) {
    const zelda_restart_st restart = {.enable = !!(enable & 255), .index = (uint32_t)index};
    bind_state(c, 8, &restart, sizeof restart);
}
NVN(nvnCommandBufferSetDepthBounds, void, NVNcommandBuffer *c, uint32_t enable, float near, float far) {
    (void)c; (void)near; (void)far;
    // shortcut: disabled depth-bounds tests are exact; enabled tests need a shader or device capability.
    if (enable & 255) { LOG("BLOCKED NVN enabled depth bounds"); exit(78); }
}
NVN(nvnCommandBufferSetRasterizerDiscard, void, NVNcommandBuffer *c, uint32_t enable) {
    (void)c;
    // shortcut: ordinary rasterization is supported; discarded geometry needs a separate pipeline variant.
    if (enable & 255) { LOG("BLOCKED NVN rasterizer discard"); exit(78); }
}
NVN(nvnDeviceGetTimestampInNanoseconds, uint64_t, const NVNdevice *d, const NVNcounterData *counter) {
    (void)d;
    // Shared Metal counter reports use the Tegra 19.2 MHz clock.
    return (uint64_t)((__uint128_t)counter->timestamp * 625 / 12);
}
NVN_NOPROTO(nvnCommandBufferSetCommandMemoryCallbackEnabled, void, NVNcommandBuffer *c, NVNboolean enable) {
    ((cmd_t *)c)->command_callback_disabled = !enable;
}
NVN(nvnCommandBufferSetLineWidth, void, NVNcommandBuffer *c, float width) {
    if (!isfinite(width) || width <= 0) { LOG("BLOCKED NVN invalid line width=%g", width); exit(78); }
    bind_state(c, 9, &width, sizeof width);
}
NVN(nvnCommandBufferSetPointSize, void, NVNcommandBuffer *c, float size) {
    if (!isfinite(size) || size <= 0) { LOG("BLOCKED NVN invalid point size"); exit(78); }
    bind_state(c, 7, &size, sizeof size);
}
NVN(nvnCommandBufferSetTiledCacheAction, void, NVNcommandBuffer *c, NVNtiledCacheAction action) {
    (void)c;
    // Tile-cache policy is a Tegra performance hint; Metal owns tile storage and pass boundaries.
    if (action < NVN_TILED_CACHE_ACTION_ENABLE || action > NVN_TILED_CACHE_ACTION_DISABLE_RENDER_TARGET_BINNING) {
        LOG("BLOCKED NVN invalid tile cache action=%d", action); exit(78);
    }
}
NVN(nvnCommandBufferSetTiledCacheTileSize, void, NVNcommandBuffer *c, int width, int height) {
    (void)c;
    if (width <= 0 || height <= 0) { LOG("BLOCKED NVN invalid tile size"); exit(78); }
}
