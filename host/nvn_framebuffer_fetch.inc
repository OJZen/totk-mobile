#include "shader_status.h"
// Framebuffer-fetch variants for draws that read, at their own pixel, a colour attachment written earlier in the pass.
// v1.4.3 G-buffer decals sample c1/c3 at the current pixel and write them back. A texture read cannot see what the pass
// has written so far, so each such draw used to get a render pass of its own (about 65 single-draw 1600x896 passes per
// frame in the post-cutscene cave). A fragment function that reads the attachment as [[color(k)]] sees the earlier
// fragments' results in API order on Apple GPUs, so the draws can stay in one pass with the same result.
// A texture read is replaced only when it provably is the current pixel:
//  - the fragment shader samples a texture2d<float> at in.m.xy * (1 / in.m.w) of a perspective-interpolated input m,
//  - the vertex shader writes that output as ((w + x) * 0.5, (w - y) * 0.5, -, w) of its final position, which makes
//    m.xy / m.w the viewport position in [0, 1] with the top row at 0, and
//  - at the draw, the viewport is exactly the attachment, the texture bound there is the attachment's own single-level
//    2D subresource with the same format and no swizzle, and the draw writes that attachment.
// Every other draw keeps its texture reads and the pass split. ZELDA_FRAMEBUFFER_FETCH=0 disables the variants.
#include "nvn_fetch_record.h"

// Values of SSA temporaries defined exactly once and never reassigned.
static NSDictionary<NSString *, NSString *> *zelda_fetch_values(NSString *source) {
    static NSRegularExpression *definition, *assignment;
    if (!definition) {
        definition = [NSRegularExpression regularExpressionWithPattern:@"^ +(?:float|float2|float3|float4|int|uint) (_\\d+) = (.+);$" options:NSRegularExpressionAnchorsMatchLines error:nil];
        assignment = [NSRegularExpression regularExpressionWithPattern:@"^ +(_\\d+)(?:\\.\\w+)? = " options:NSRegularExpressionAnchorsMatchLines error:nil];
    }
    NSMutableDictionary<NSString *, NSString *> *values = [NSMutableDictionary dictionary];
    NSMutableSet<NSString *> *bad = [NSMutableSet set];
    for (NSTextCheckingResult *m in [definition matchesInString:source options:0 range:NSMakeRange(0, source.length)]) {
        NSString *name = [source substringWithRange:[m rangeAtIndex:1]];
        if (values[name]) [bad addObject:name]; else values[name] = [source substringWithRange:[m rangeAtIndex:2]];
    }
    for (NSTextCheckingResult *m in [assignment matchesInString:source options:0 range:NSMakeRange(0, source.length)])
        [bad addObject:[source substringWithRange:[m rangeAtIndex:1]]];
    [values removeObjectsForKeys:bad.allObjects];
    return values;
}
static NSString *zelda_fetch_match(NSString *pattern, NSString *text, NSUInteger group) {
    if (!text) return nil;
    NSRegularExpression *re = [NSRegularExpression regularExpressionWithPattern:pattern options:0 error:nil];
    NSTextCheckingResult *m = [re firstMatchInString:text options:NSMatchingAnchored range:NSMakeRange(0, text.length)];
    return m && m.range.length == text.length ? [text substringWithRange:[m rangeAtIndex:group]] : nil;
}
static NSString *zelda_fetch_call_pattern(NSString *name) {
    return [NSString stringWithFormat:@"spvDescriptorSet0\\.%@\\.sample\\(spvDescriptorSet0\\.%@Smplr, select\\((_\\d+), float2\\(0\\.0\\), isnan\\(\\1\\)\\)\\)", name, name];
}

// Fragment shader: texture bindings whose every use samples at a projected input. Result maps the argument id to
// {name, locations}. Empty when the shader has none or already reads attachments.
static NSDictionary<NSNumber *, NSArray *> *zelda_fetch_reads_in(NSString *source) {
    NSMutableDictionary<NSNumber *, NSArray *> *reads = [NSMutableDictionary dictionary];
    if (!source) return reads;
    NSRegularExpression *signature = [NSRegularExpression regularExpressionWithPattern:@"^fragment \\w+ main0\\(([^\\n]*)\\)$" options:NSRegularExpressionAnchorsMatchLines error:nil];
    NSArray *signatures = [signature matchesInString:source options:0 range:NSMakeRange(0, source.length)];
    if (signatures.count != 1 || [[source substringWithRange:[signatures[0] rangeAtIndex:1]] containsString:@"[[color("]) return reads;
    NSMutableDictionary<NSString *, NSNumber *> *inputs = [NSMutableDictionary dictionary];
    NSRegularExpression *input = [NSRegularExpression regularExpressionWithPattern:@"^    float4 (m_\\d+) \\[\\[user\\(locn(\\d+)\\)\\]\\];$" options:NSRegularExpressionAnchorsMatchLines error:nil];
    NSRange in_struct = [source rangeOfString:@"struct main0_in\n{\n"];
    if (in_struct.location == NSNotFound) return reads;
    NSRange in_end = [source rangeOfString:@"\n};" options:0 range:NSMakeRange(NSMaxRange(in_struct), source.length - NSMaxRange(in_struct))];
    if (in_end.location == NSNotFound) return reads;
    NSRange in_body = NSMakeRange(in_struct.location, NSMaxRange(in_end) - in_struct.location);
    for (NSTextCheckingResult *m in [input matchesInString:source options:0 range:in_body])
        inputs[[source substringWithRange:[m rangeAtIndex:1]]] = @([[source substringWithRange:[m rangeAtIndex:2]] intValue]);
    NSDictionary<NSString *, NSString *> *values = zelda_fetch_values(source);
    NSRegularExpression *texture = [NSRegularExpression regularExpressionWithPattern:@"^    texture2d<float> (\\w+) \\[\\[id\\((\\d+)\\)\\]\\];$" options:NSRegularExpressionAnchorsMatchLines error:nil];
    for (NSTextCheckingResult *t in [texture matchesInString:source options:0 range:NSMakeRange(0, source.length)]) {
        NSString *name = [source substringWithRange:[t rangeAtIndex:1]];
        int aid = [[source substringWithRange:[t rangeAtIndex:2]] intValue];
        NSRegularExpression *use = [NSRegularExpression regularExpressionWithPattern:[NSString stringWithFormat:@"spvDescriptorSet0\\.%@\\b", name] options:0 error:nil];
        NSRegularExpression *call = [NSRegularExpression regularExpressionWithPattern:zelda_fetch_call_pattern(name) options:0 error:nil];
        NSArray *uses = [use matchesInString:source options:0 range:NSMakeRange(0, source.length)];
        NSArray *calls = [call matchesInString:source options:0 range:NSMakeRange(0, source.length)];
        if (!calls.count || uses.count != calls.count) continue;
        NSMutableSet<NSNumber *> *locations = [NSMutableSet set];
        unsigned components = 0;
        for (NSTextCheckingResult *c in calls) {
            // Components the shader takes from the sample (a swizzle suffix, or all four).
            NSString *rest = [source substringFromIndex:NSMaxRange(c.range)];
            NSString *suffix = zelda_fetch_match(@"\\.([xyzw]+)[^\\w][\\s\\S]*", rest, 1);
            if (!suffix) components |= 15;
            for (NSUInteger i = 0; i < suffix.length; i++) components |= 1u << ([@"xyzw" rangeOfString:[suffix substringWithRange:NSMakeRange(i, 1)]].location);
            NSString *coordinate = values[[source substringWithRange:[c rangeAtIndex:1]]];
            NSString *x = zelda_fetch_match(@"float2\\((_\\d+), (_\\d+)\\)", coordinate, 1), *y = zelda_fetch_match(@"float2\\((_\\d+), (_\\d+)\\)", coordinate, 2);
            NSString *px = zelda_fetch_match(@"spvFMul\\((_\\d+), (_\\d+)\\)", values[x], 1), *rx = zelda_fetch_match(@"spvFMul\\((_\\d+), (_\\d+)\\)", values[x], 2);
            NSString *py = zelda_fetch_match(@"spvFMul\\((_\\d+), (_\\d+)\\)", values[y], 1), *ry = zelda_fetch_match(@"spvFMul\\((_\\d+), (_\\d+)\\)", values[y], 2);
            NSString *w = zelda_fetch_match(@"1\\.0 / (_\\d+)", values[rx], 1);
            NSString *mx = zelda_fetch_match(@"in\\.(m_\\d+)\\.x", values[px], 1), *my = zelda_fetch_match(@"in\\.(m_\\d+)\\.y", values[py], 1);
            NSString *mw = zelda_fetch_match(@"in\\.(m_\\d+)\\.w", values[w], 1);
            if (!mx || !rx || ![rx isEqualToString:ry] || ![mx isEqualToString:my] || ![mx isEqualToString:mw] || !inputs[mx]) { locations = nil; break; }
            [locations addObject:inputs[mx]];
        }
        if (locations.count) reads[@(aid)] = @[name, locations.allObjects, @(components)];
    }
    return reads;
}
// Vertex shader: output locations written as ((w + x) * 0.5, (w - y) * 0.5, -, w) of the final position.
static NSSet<NSNumber *> *zelda_fetch_screen_outputs_in(NSString *source) {
    NSMutableSet<NSNumber *> *result = [NSMutableSet set];
    // Whole-vector or multi-component writes are not followed; such a shader proves nothing.
    NSRegularExpression *wide = [NSRegularExpression regularExpressionWithPattern:@"out\\.(?:gl_Position|m_\\d+)(?:\\.\\w{2,})? = " options:0 error:nil];
    if (!source || [wide numberOfMatchesInString:source options:0 range:NSMakeRange(0, source.length)]) return result;
    NSDictionary<NSString *, NSString *> *values = zelda_fetch_values(source);
    NSString *(^last)(NSString *) = ^NSString *(NSString *target) {
        NSString *escaped = [NSRegularExpression escapedPatternForString:target];
        NSRegularExpression *top = [NSRegularExpression regularExpressionWithPattern:[NSString stringWithFormat:@"^    out\\.%@ = (_\\d+);$", escaped] options:NSRegularExpressionAnchorsMatchLines error:nil];
        NSRegularExpression *any = [NSRegularExpression regularExpressionWithPattern:[NSString stringWithFormat:@"^ +out\\.%@ = ", escaped] options:NSRegularExpressionAnchorsMatchLines error:nil];
        NSRegularExpression *nested = [NSRegularExpression regularExpressionWithPattern:[NSString stringWithFormat:@"^ {8,}out\\.%@ = ", escaped] options:NSRegularExpressionAnchorsMatchLines error:nil];
        NSArray *tops = [top matchesInString:source options:0 range:NSMakeRange(0, source.length)];
        NSArray *anys = [any matchesInString:source options:0 range:NSMakeRange(0, source.length)];
        if (!tops.count || [nested numberOfMatchesInString:source options:0 range:NSMakeRange(0, source.length)]) return nil;
        // The last assignment must be the SSA one: constant defaults may only precede it.
        if ([anys.lastObject range].location != [tops.lastObject range].location) return nil;
        return [source substringWithRange:[tops.lastObject rangeAtIndex:1]];
    };
    NSString *x = last(@"gl_Position.x"), *y = last(@"gl_Position.y"), *w = last(@"gl_Position.w");
    if (!x || !y || !w) return result;
    NSRegularExpression *output = [NSRegularExpression regularExpressionWithPattern:@"^    float4 (m_\\d+) \\[\\[user\\(locn(\\d+)\\)\\]\\];$" options:NSRegularExpressionAnchorsMatchLines error:nil];
    for (NSTextCheckingResult *o in [output matchesInString:source options:0 range:NSMakeRange(0, source.length)]) {
        NSString *member = [source substringWithRange:[o rangeAtIndex:1]];
        if ([source rangeOfString:[NSString stringWithFormat:@"out.%@ = ", member]].location != NSNotFound) continue;
        NSString *ox = last([member stringByAppendingString:@".x"]), *oy = last([member stringByAppendingString:@".y"]), *ow = last([member stringByAppendingString:@".w"]);
        if (!ox || !oy || ![ow isEqualToString:w]) continue;
        NSString *sx = zelda_fetch_match(@"spvFMul\\((_\\d+), 0\\.5\\)", values[ox], 1), *sy = zelda_fetch_match(@"spvFMul\\((_\\d+), 0\\.5\\)", values[oy], 1);
        NSString *ax = zelda_fetch_match(@"spvFAdd\\((_\\d+), (_\\d+)\\)", values[sx], 1), *bx = zelda_fetch_match(@"spvFAdd\\((_\\d+), (_\\d+)\\)", values[sx], 2);
        NSString *ay = zelda_fetch_match(@"spvFAdd\\((_\\d+), (_\\d+)\\)", values[sy], 1), *by = zelda_fetch_match(@"spvFAdd\\((_\\d+), (_\\d+)\\)", values[sy], 2);
        int okx = ax && (([ax isEqualToString:w] && [bx isEqualToString:x]) || ([ax isEqualToString:x] && [bx isEqualToString:w]));
        NSString *negated = ay && [ay isEqualToString:w] ? by : by && [by isEqualToString:w] ? ay : nil;
        int oky = negated && [values[negated] isEqualToString:[@"-" stringByAppendingString:y]];
        if (okx && oky) [result addObject:@([[source substringWithRange:[o rangeAtIndex:2]] intValue])];
    }
    return result;
}
// The variant reads attachment k as [[color(k)]] wherever the shader sampled the mapped texture.
static NSString *zelda_fetch_source(NSString *source, NSDictionary<NSString *, NSNumber *> *mapping) {
    NSMutableString *result = [source mutableCopy];
    NSMutableString *parameters = [NSMutableString string];
    for (NSString *name in [mapping.allKeys sortedArrayUsingSelector:@selector(compare:)]) {
        NSRegularExpression *call = [NSRegularExpression regularExpressionWithPattern:zelda_fetch_call_pattern(name) options:0 error:nil];
        NSString *value = [NSString stringWithFormat:@"zfb_%d", mapping[name].intValue];
        if (![call replaceMatchesInString:result options:0 range:NSMakeRange(0, result.length) withTemplate:value]) return nil;
    }
    for (NSNumber *k in [[NSSet setWithArray:mapping.allValues].allObjects sortedArrayUsingSelector:@selector(compare:)])
        [parameters appendFormat:@", float4 zfb_%d [[color(%d)]]", k.intValue, k.intValue];
    NSRegularExpression *signature = [NSRegularExpression regularExpressionWithPattern:@"^(fragment \\w+ main0\\([^\\n]*)\\)$" options:NSRegularExpressionAnchorsMatchLines error:nil];
    NSString *template = [NSString stringWithFormat:@"$1%@)", [NSRegularExpression escapedTemplateForString:parameters]];
    if ([signature replaceMatchesInString:result options:0 range:NSMakeRange(0, result.length) withTemplate:template] != 1) return nil;
    return result;
}

#ifndef ZELDA_FETCH_ANALYSIS_ONLY
static int zelda_fetch_enabled(void) {
    static int enabled = -1;
    if (enabled < 0) { const char *value = getenv("ZELDA_FRAMEBUFFER_FETCH"); enabled = !value || strcmp(value, "0"); }
    return enabled;
}
static uint64_t z_fetch_draws, z_fetch_variants;
static NSString *zelda_fetch_shader_text(NvShader *shader) {
    NSString *path = [g_msl_dir stringByAppendingPathComponent:[@(shader->key) stringByAppendingString:@".metallib"]];
    return [NSString stringWithContentsOfFile:path encoding:NSUTF8StringEncoding error:nil];
}
static NSDictionary<NSNumber *, NSArray *> *zelda_fetch_reads(NvShader *shader) {
    static NSMutableDictionary<NSString *, NSDictionary *> *cache;
    if (!cache) cache = [NSMutableDictionary dictionary];
    NSString *key = @(shader->key);
    NSDictionary *reads = cache[key];
    if (!reads) cache[key] = reads = zelda_fetch_reads_in(zelda_fetch_shader_text(shader));
    return reads;
}
static NSSet<NSNumber *> *zelda_fetch_screen_outputs(NvShader *shader) {
    static NSMutableDictionary<NSString *, NSSet *> *cache;
    if (!cache) cache = [NSMutableDictionary dictionary];
    NSString *key = @(shader->key);
    NSSet *outputs = cache[key];
    if (!outputs) cache[key] = outputs = zelda_fetch_screen_outputs_in(zelda_fetch_shader_text(shader));
    return outputs;
}
// Builds a variant's shader object from the base shader alone, so it may run on a worker thread.
static NvShader *zelda_fetch_build(NvShader *base, NSDictionary<NSString *, NSNumber *> *mapping, NSString *identity) {
    NSString *source = zelda_fetch_source(zelda_fetch_shader_text(base), mapping);
    if (!source) { MLOG("ERROR: Framebuffer-fetch variant rewrite failed %s (%s)", base->key, identity.UTF8String); return nil; }
    MTLCompileOptions *options = nil; NSString *text = source; source = zelda_shader_math(source, &options); NSError *error = nil;
    id<MTLLibrary> library = ZELDA_SHADER_BUILD([g_dev newLibraryWithSource:source options:options error:&error]);
    id<MTLFunction> function = library ? [library newFunctionWithName:base->fn.name] : nil;
    if (!function) { MLOG("ERROR: Framebuffer-fetch variant compilation failed %s: %s", base->key, error.localizedDescription.UTF8String); return nil; }
    function.label = [NSString stringWithFormat:@"%s fetch %@", base->key, identity];
    zelda_function_source(function, nil, text);
    if (base->rf.nb && [function newArgumentEncoderWithBufferIndex:base->rf.abi].encodedLength != base->arg_len)
        MDIE("Framebuffer-fetch variant changed the argument buffer layout");
    NvShader *shader = [NvShader new];
    shader->fn = function; shader->rf = base->rf; shader->ae = base->ae; shader->arg_len = base->arg_len;
    shader->stage = base->stage; shader->range_ro = base->range_ro; shader->z_cube_uniform = base->z_cube_uniform;
    shader->z_fetch_identity = identity;
    shader->c1buf = base->c1buf; shader->c1_gpu = base->c1_gpu; shader->c1off = base->c1off;
    strlcpy(shader->key, base->key, sizeof shader->key);
    return shader;
}
// With `pending` and background preparation on (shader_async.h), a variant not built yet compiles on a worker thread:
// *pending is set and the draw keeps its texture reads until a later draw finds the variant ready.
static pthread_mutex_t z_fetch_mu = PTHREAD_MUTEX_INITIALIZER;
static NSMutableDictionary<NSString *, NSArray *> *z_fetch_built;   // key -> @[shader or NSNull, build ns], from workers
static NvShader *zelda_fetch_shader_ready(NvShader *base, NSDictionary<NSString *, NSNumber *> *mapping, NSString *identity, int *pending) {
    static NSMutableDictionary<NSString *, id> *cache;
    if (!cache) cache = [NSMutableDictionary dictionary];
    NSString *key = [NSString stringWithFormat:@"%s|%@", base->key, identity];
    id cached = cache[key];
    NvShader *shader = nil; uint64_t elapsed = 0; int background = 0;
    if ([cached isEqual:@YES]) {
        pthread_mutex_lock(&z_fetch_mu); NSArray *built = z_fetch_built[key]; [z_fetch_built removeObjectForKey:key]; pthread_mutex_unlock(&z_fetch_mu);
        if (!built) { *pending = 1; return nil; }
        shader = built[0] == NSNull.null ? nil : built[0]; elapsed = [built[1] unsignedLongLongValue]; background = 1;
    } else if (cached) return cached == NSNull.null ? nil : cached;
    else if (pending && zelda_shader_background()) {
        cache[key] = @YES; *pending = 1;
        NSDictionary<NSString *, NSNumber *> *frozen = [mapping copy];
        dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
            uint64_t started = bp_now(); NvShader *made = zelda_fetch_build(base, frozen, identity);
            NSArray *built = @[made ?: (id)NSNull.null, @(bp_now() - started)];
            pthread_mutex_lock(&z_fetch_mu);
            if (!z_fetch_built) z_fetch_built = [NSMutableDictionary dictionary];
            z_fetch_built[key] = built;
            pthread_mutex_unlock(&z_fetch_mu);
        });
        return nil;
    } else {
        uint64_t started = bp_now();
        cache[key] = NSNull.null;
        shader = zelda_fetch_build(base, mapping, identity); elapsed = bp_now() - started;
    }
    cache[key] = shader ?: (id)NSNull.null;
    if (!shader) return nil;
    z_fetch_variants++;
    if (!z_warming) {
        if (!background) { g_bp_lib_n++; g_bp_lib_ns += elapsed; }
        pso_user_record("V\t%s\t%s\n", base->key, identity.UTF8String);
    }
    MLOG("Framebuffer-fetch variant: %s attachments=%s build_ms=%.1f%s", base->key, identity.UTF8String, elapsed / 1e6, background ? " (background)" : "");
    return shader;
}
static NvShader *zelda_fetch_shader(NvShader *base, NSDictionary<NSString *, NSNumber *> *mapping, NSString *identity) {
    return zelda_fetch_shader_ready(base, mapping, identity, NULL);
}
static int zelda_fetch_subresource(const treg_t *bound, unsigned k, unsigned components) {
    const tex_t *a = R.rt.color[k], *t = &bound->t;
    if (!a || t->mem != a->mem || t->d.target != NVN_TEXTURE_TARGET_2D || a->d.target != NVN_TEXTURE_TARGET_2D ||
        t->d.width != a->d.width || t->d.height != a->d.height || t->d.format != a->d.format || t->d.samples > 1 || a->d.samples > 1) return 0;
    const view_t *av = R.rt.has_cview[k] ? &R.rt.cview[k] : NULL, *tv = bound->has_v ? &bound->v : NULL;
    int alevel = av && av->set_levels ? av->base_level : 0, tlevel = tv && tv->set_levels ? tv->base_level : 0;
    int alayer = av && av->set_layers ? av->min_layer : 0, tlayer = tv && tv->set_layers ? tv->min_layer : 0;
    int aformat = av && av->set_format ? av->format : a->d.format, tformat = tv && tv->set_format ? tv->format : t->d.format;
    int levels = tv && tv->set_levels && tv->nlevels ? tv->nlevels : (int)MAX(t->d.levels, 1u) - tlevel;
    unsigned swizzle = tv && tv->swizzle ? tv->swizzle : t->swizzle;
    if (tv && tv->set_target && tv->target != NVN_TEXTURE_TARGET_2D) return 0;
    // Swizzle selectors are 3 bits per component (2..5 = R..A; bit 15 only marks the swizzle as set). Components the
    // shader uses must come from their own channel, as an attachment read returns them.
    for (unsigned i = 0; swizzle && i < 4; i++) if ((components >> i & 1) && (swizzle >> (3 * i) & 7) != 2 + i) return 0;
    return alevel == tlevel && alayer == tlayer && aformat == tformat && levels == 1;
}
// Returns the program to draw with; *fetched gets the attachments it now reads as [[color(k)]].
static NvProg *zelda_fetch_prepare(NvProg *pr, unsigned written, unsigned *fetched) {
    *fetched = 0;
    if (!zelda_fetch_enabled() || !g_enc || !written || !pr || !pr->sh[0] || !pr->sh[1] || pr->sh[0]->z_layered || g_fz.active) return pr;
    for (int i = 0; i < 8; i++) if (zelda_fragment_class((MTLPixelFormat)g_pass.cfmt[i])) return pr;
    NvShader *vs = pr->sh[0], *fs = pr->sh[1];
    unsigned writes = 0;
    for (unsigned k = 0; k < 8; k++) if ((S.chmask.mask >> (4 * k) & 15) && (fs->rf.color_mask >> k & 1)) writes |= 1u << k;
    NSMutableDictionary<NSString *, NSNumber *> *mapping = nil;
    for (int st = 0; st < 5; st++) {
        __unsafe_unretained NvShader *sh = pr->sh[st]; if (!sh) continue;
        for (int i = 0; i < sh->rf.nb; i++) {
            const nvn_rbind *b = &sh->rf.b[i]; uint64_t h;
            if (b->kind == NVN_RB_IMG) h = b->reg < 32 && sh->stage < 6 ? S.img[sh->stage][b->reg] : 0;
            else if (b->kind == NVN_RB_TEX || b->kind == NVN_RB_TBUF) h = b->reg < 128 && sh->stage < 6 ? S.tex[sh->stage][b->reg] : 0;
            else continue;
            int tid = (int)(h & 0xfffff); if (!tid) continue;
            treg_t bound; int ok;
            pthread_mutex_lock(&g_rmu); ok = tid < g_ntreg && g_treg[tid].ok; if (ok) bound = g_treg[tid]; pthread_mutex_unlock(&g_rmu);
            if (!ok) continue;
            int target = -1, hits = 0;
            for (int k = 0; k < R.rt.n && k < 8; k++)
                if ((written >> k & 1) && R.rt.color[k] && zelda_texture_same_storage(&bound.t, R.rt.color[k])) { target = k; hits++; }
            if (!hits) continue;
            if (hits != 1 || sh != fs || b->kind != NVN_RB_TEX || !(writes >> target & 1)) return pr;
            NSArray *read = zelda_fetch_reads(fs)[@(b->aid)];
            if (!read || !zelda_fetch_subresource(&bound, (unsigned)target, [read[2] unsignedIntValue])) return pr;
            NSSet<NSNumber *> *screen = zelda_fetch_screen_outputs(vs);
            for (NSNumber *location in read[1]) if (![screen containsObject:location]) return pr;
            if (!mapping) mapping = [NSMutableDictionary dictionary];
            NSNumber *previous = mapping[read[0]];
            if (previous && previous.intValue != target) return pr;
            mapping[read[0]] = @(target); *fetched |= 1u << target;
        }
    }
    if (!mapping) return pr;
    unsigned tw = g_pass.tw, th = g_pass.th;
    for (unsigned k = 0; k < 8; k++) if (*fetched >> k & 1) {
        const view_t *v = R.rt.has_cview[k] ? &R.rt.cview[k] : NULL; int level = v && v->set_levels ? v->base_level : 0;
        uint32_t w = MAX(R.rt.color[k]->d.width >> level, 1u), h = MAX(R.rt.color[k]->d.height >> level, 1u);
        int full = S.has_vp ? S.vp[0][0] == 0 && S.vp[0][1] == 0 && S.vp[0][2] == (float)w && S.vp[0][3] == (float)h : tw == w && th == h;
        if (!full || g_ui_rect_fn) { *fetched = 0; return pr; }
    }
    if (!pr->z_fetch) pr->z_fetch = [NSMutableDictionary dictionary];
    // NSDictionary keys are copied: lookup compares the exact mapping without rebuilding its persistent text.
    id cached = pr->z_fetch[mapping];
    if (!cached) {
        NSString *identity = zelda_fetch_record_identity(mapping);
        int pending = 0;
        NvShader *shader = zelda_fetch_shader_ready(fs, mapping, identity, &pending);
        if (pending) { *fetched = 0; return pr; }
        NvProg *variant = nil;
        if (shader) { variant = [NvProg new]; for (int i = 0; i < 6; i++) variant->sh[i] = pr->sh[i]; variant->sh[1] = shader; }
        pr->z_fetch[mapping] = cached = variant ?: (id)NSNull.null;
    }
    if (cached == NSNull.null) { *fetched = 0; return pr; }
    z_fetch_draws++;
    return cached;
}
#endif
