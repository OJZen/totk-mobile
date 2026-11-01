// Barriers inside an open render pass. v1.4.3 shrine G-buffer decals (vertex cae815517d948074, fragment
// 68fe6a17b2db5c8d) issue ORDER_FRAGMENTS | INVALIDATE_TEXTURE between every draw, so that each decal sees the previous
// one's G-buffer writes. Ending the pass at each barrier gave about 390 single-draw 1536x864 passes per frame, each
// loading and storing every attachment on a tile GPU (~15 ms of an M4's ~28 ms GPU frame). Within one Metal pass a
// fragment that reads an attachment as [[color(k)]] already sees the earlier draws' results at its pixel in API order
// (nvn_framebuffer_fetch.inc), and depth / stencil tests see earlier draws, so such a barrier keeps the pass when:
//  - it only orders primitives / fragments and invalidates texture caches,
//  - no draw in the pass may have stored to memory (image or writable storage-buffer bindings), and
//  - no copy encoder, merged pass, or pending clear is involved.
// A later draw that samples an attachment written in the pass still ends it unless its reads became framebuffer
// fetches (the existing check after zelda_fetch_prepare), so texture reads never see stale contents.
// ZELDA_BARRIER_IN_PASS=0 ends the pass at every barrier.
static int zelda_program_stores(NvProg *pr) {
    if (!pr) return 0;
    for (int st = 0; st < 5; st++) {
        __unsafe_unretained NvShader *sh = pr->sh[st]; if (!sh) continue;
        for (int i = 0; i < sh->rf.nb; i++)
            if (sh->rf.b[i].kind == NVN_RB_IMG || (sh->rf.b[i].kind == NVN_RB_SBUFP && sh->rf.b[i].rw)) return 1;
    }
    return 0;
}
static int zelda_barrier_keeps_pass(uint32_t bits) {
    static int enabled = -1;
    if (enabled < 0) { const char *value = getenv("ZELDA_BARRIER_IN_PASS"); enabled = !value || atoi(value); }
    const uint32_t ordering = NVN_BARRIER_ORDER_PRIMITIVES_BIT | NVN_BARRIER_ORDER_FRAGMENTS_BIT |
        NVN_BARRIER_ORDER_FRAGMENTS_TILED_BIT | NVN_BARRIER_INVALIDATE_TEXTURE_BIT;
    if (!enabled || !g_enc || g_blit || (bits & ~ordering) || z_pass_stores || g_fz.active ||
        z_depth_clear.pending || z_color_clear.count) return 0;
    z_barriers_kept++;
    PD("Z %llu barrier kept in pass", g_stat_pass);
    return 1;
}
