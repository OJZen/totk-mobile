// Colour attachments of a render pass. A draw writes only the targets its channel mask and fragment outputs cover;
// unwritten slots may alias a written one (Zelda binds one texture to several slots), which Metal cannot attach twice.
// Attaching only the current draw's targets split v1.4.3's G-buffer into about 60 single-draw full-resolution passes per
// frame (draws alternate between c3 and c1+c3), each loading and storing every attachment on a tile GPU. Each target
// configuration therefore remembers the union of targets drawn into it and a pass attaches that union up front, minus
// slots that would duplicate an attached subresource; draws keep writing only their own targets (zelda_color_write_mask).
static unsigned z_color_targets = 0xff, z_pass_attached;
static void rp_flush(void);
enum { Z_TARGET_SETS = 256 };
static struct { uint64_t key; uint8_t used; } z_target_sets[Z_TARGET_SETS];
static uint64_t zelda_target_key(void) {
    uint64_t h = 1469598103934665603ull ^ (uint64_t)R.rt.n;
    for (int i = 0; i < R.rt.n && i < 8; i++) {
        const view_t *v = &R.rt.cview[i]; int has = R.rt.has_cview[i];
        uint64_t parts[] = {(uintptr_t)R.rt.color[i], R.rt.color[i] ? R.rt.color[i]->serial : 0,
            has && v->set_levels ? (uint64_t)v->base_level : 0, has && v->set_layers ? (uint64_t)v->min_layer : 0};
        for (unsigned k = 0; k < 4; k++) h = (h ^ parts[k]) * 1099511628211ull;
    }
    return (h ^ (uintptr_t)R.rt.depth) * 1099511628211ull;
}
static uint8_t *zelda_target_set(void) {
    uint64_t key = zelda_target_key();
    unsigned slot = (unsigned)(key ^ key >> 29) % Z_TARGET_SETS;
    if (z_target_sets[slot].key != key) { z_target_sets[slot].key = key; z_target_sets[slot].used = 0; }
    return &z_target_sets[slot].used;
}
static int zelda_same_subresource(int a, int b) {
    if (!zelda_texture_same_storage(R.rt.color[a], R.rt.color[b])) return 0;
    const view_t *x = &R.rt.cview[a], *y = &R.rt.cview[b]; int hx = R.rt.has_cview[a], hy = R.rt.has_cview[b];
    return (hx && x->set_levels ? x->base_level : 0) == (hy && y->set_levels ? y->base_level : 0) &&
           (hx && x->set_layers ? x->min_layer : 0) == (hy && y->set_layers ? y->min_layer : 0);
}
static int zelda_pending_clear_conflicts(unsigned targets) {
    unsigned pending = zelda_color_clear_targets();
    for (int i = 0; i < 8; i++) if (R.clear_c[i]) pending |= 1u << i;
    if (!pending) return 0;
    for (int i = 0; i < R.rt.n && i < 8; i++) if ((pending >> i & 1) && R.rt.color[i])
        for (int j = 0; j < R.rt.n && j < 8; j++)
            if (i != j && (targets >> j & 1) && R.rt.color[j] && zelda_same_subresource(i, j)) return 1;
    return 0;
}
static void zelda_color_targets(uint32_t channels, unsigned outputs, int pending_depth_clear) {
    unsigned targets = 0;
    for (unsigned i = 0; i < 8; i++) if ((channels >> (4 * i) & 15) && (outputs >> i & 1)) targets |= 1u << i;
    // A clear of an aliased slot must finish before the draw attaches this slot instead.
    if (zelda_pending_clear_conflicts(targets)) rp_flush();
    if (targets != z_color_targets) {
        // Finish pending clears with their original attachments before changing the draw's attachment set; a pass
        // that already holds every target this draw writes continues.
        if (!pending_depth_clear && !(g_enc && !(targets & ~z_pass_attached))) rp_flush();
        z_color_targets = targets;
    }
    if (R.has_rt) *zelda_target_set() |= (uint8_t)targets;
}
// Slots attached when a pass opens: this draw's targets, then the configuration's other known targets unless they
// would attach the same subresource twice. clear_only == 1 opens a pass for pending clears only.
static unsigned zelda_pass_targets(int clear_only) {
    unsigned want = z_color_targets & 0xff;
    if (clear_only == 1) return 0;
    unsigned extra = R.has_rt ? *zelda_target_set() & ~want : 0;
    for (int i = 0; i < R.rt.n && i < 8; i++) {
        if (!(extra >> i & 1) || !R.rt.color[i]) continue;
        int duplicate = 0;
        for (int j = 0; j < R.rt.n && j < 8 && !duplicate; j++)
            duplicate = j != i && (want >> j & 1) && R.rt.color[j] && zelda_same_subresource(i, j);
        if (!duplicate) want |= 1u << i;
    }
    return want;
}
// Targets written since the pass opened (pending clears and encoded draws). A draw that samples an attachment needs a
// new pass only if that attachment was written in this pass; otherwise memory already holds its contents (v1.4.3
// decals sample the G-buffer with depth writes off, which cost one full-resolution pass per draw).
static unsigned z_pass_written, z_draw_written;
static int z_pass_depth_written, z_draw_depth_written;
// Whether a draw in this pass may have stored to memory (image or writable storage-buffer bindings); a barrier then
// has to end the pass (nvn_barrier.inc). Noted, like the targets, before the draw's pass opens.
static int z_pass_stores, z_draw_stores;
static uint64_t z_barriers_kept;
static void zelda_note_draw_writes(int depth_or_stencil) {
    z_draw_written = z_color_targets & 0xff; z_draw_depth_written = depth_or_stencil;
    if (g_enc) { z_pass_written |= z_draw_written; z_pass_depth_written |= depth_or_stencil; }
}
static void zelda_note_draw_stores(int stores) {
    z_draw_stores = stores;
    if (g_enc) z_pass_stores |= stores;
}
static void zelda_begin_color_targets(int clear_only) {
    z_pass_attached = zelda_pass_targets(clear_only);
    z_pass_written = 0; z_pass_depth_written = R.clear_d || R.clear_s; z_pass_stores = 0;
    for (int i = 0; i < 8; i++) if (R.clear_c[i]) z_pass_written |= 1u << i;
    if (clear_only != 1) { z_pass_written |= z_draw_written; z_pass_depth_written |= z_draw_depth_written; z_pass_stores = z_draw_stores; }
}
static int zelda_use_color_target(unsigned index, int clear_only, int clear) {
    (void)clear_only;
    if (clear) z_pass_attached |= 1u << index;
    return (int)(z_pass_attached >> index & 1);
}
// Pipeline write mask: attached slots this draw does not write stay untouched.
static uint32_t zelda_color_write_mask(uint32_t channels) {
    uint32_t mask = 0;
    for (unsigned i = 0; i < 8; i++) if (z_color_targets >> i & 1) mask |= channels & (15u << (4 * i));
    return mask;
}
