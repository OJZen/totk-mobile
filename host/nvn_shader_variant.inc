static unsigned zelda_fragment_class(MTLPixelFormat format) {
    switch (format) {
        case MTLPixelFormatR8Uint: case MTLPixelFormatR16Uint: case MTLPixelFormatR32Uint:
        case MTLPixelFormatRG8Uint: case MTLPixelFormatRG16Uint: case MTLPixelFormatRG32Uint:
        case MTLPixelFormatRGBA8Uint: case MTLPixelFormatRGBA16Uint: case MTLPixelFormatRGBA32Uint:
        case MTLPixelFormatRGB10A2Uint: return 1;
        case MTLPixelFormatR8Sint: case MTLPixelFormatR16Sint: case MTLPixelFormatR32Sint:
        case MTLPixelFormatRG8Sint: case MTLPixelFormatRG16Sint: case MTLPixelFormatRG32Sint:
        case MTLPixelFormatRGBA8Sint: case MTLPixelFormatRGBA16Sint: case MTLPixelFormatRGBA32Sint: return 2;
        default: return 0;
    }
}

static unsigned zelda_vertex_class(MTLVertexFormat format) {
    switch (format) {
        case MTLVertexFormatUChar: case MTLVertexFormatUChar2: case MTLVertexFormatUChar3: case MTLVertexFormatUChar4:
        case MTLVertexFormatUShort: case MTLVertexFormatUShort2: case MTLVertexFormatUShort3: case MTLVertexFormatUShort4:
        case MTLVertexFormatUInt: case MTLVertexFormatUInt2: case MTLVertexFormatUInt3: case MTLVertexFormatUInt4: return 1;
        case MTLVertexFormatChar: case MTLVertexFormatChar2: case MTLVertexFormatChar3: case MTLVertexFormatChar4:
        case MTLVertexFormatShort: case MTLVertexFormatShort2: case MTLVertexFormatShort3: case MTLVertexFormatShort4:
        case MTLVertexFormatInt: case MTLVertexFormatInt2: case MTLVertexFormatInt3: case MTLVertexFormatInt4: return 2;
        default: return 0;
    }
}

// NVN *_UI2F / *_I2F formats (0x51-0x68) fetch integers that the shader reads as floats: keep them in the float class.
static unsigned zelda_vertex_input_class(int format) {
    return format >= 0x51 && format <= 0x68 ? 0 : zelda_vertex_class(vfmt(format));
}

static int z_variant_collect, z_variant_missing;
static int z_warming;   // startup warm-up (nvn_shader_warm.inc): a missing variant is skipped, never captured or compiled
static void pso_predict_rest(void);
static inline int zelda_uniform_linked_source(int layout) {
    NSString *key = layout == 1 ? @"48e3901f9b4eb169_b" : @"c3960b272d349125_b";
    NvShader *shader = shader_get(key, 0);
    return shader && shader->z_cube_uniform == layout;
}

static id<MTLFunction> zelda_shader_variant(NvShader *shader, unsigned mask, int stage, NvShader *next) {
    if (!shader) return nil;
    if (!mask && !next) return shader->fn;
    NSString *base = [@(shader->key) substringToIndex:16];
    NSString *key = [NSString stringWithFormat:@"%@-%0*x", base, stage == 0 ? 8 : 4, mask];
    NSMutableDictionary *request = [@{@"base":base, @"mask":@(mask)} mutableCopy];
    if (next) {
        NSString *fragment = [@(next->key) substringToIndex:16];
        NSString *identity = [NSString stringWithFormat:@"%@-%08x-%@", base, mask, fragment];
        unsigned char hash[CC_SHA256_DIGEST_LENGTH]; char name[17];
        CC_SHA256(identity.UTF8String, (CC_LONG)strlen(identity.UTF8String), hash);
        for (unsigned i = 0; i < 8; i++) snprintf(name + 2 * i, 3, "%02x", hash[i]);
        key = @(name); request[@"next"] = fragment;
    }
    NSString *name = [key stringByAppendingString:@"_b"];
    NSString *library = [g_msl_dir stringByAppendingPathComponent:[name stringByAppendingString:@".metallib"]];
    int background = zelda_shader_background();
    if (background && zelda_variant_busy(name)) return nil;
    if (![NSFileManager.defaultManager fileExistsAtPath:library]) {
        if (z_warming) return nil;
        NSString *directory = @"work/shaders/variants";
        if (![NSFileManager.defaultManager createDirectoryAtPath:directory withIntermediateDirectories:YES attributes:nil error:nil])
            MDIE("Cannot create shader variant directory");
        NSData *data = [NSJSONSerialization dataWithJSONObject:request options:0 error:nil];
        NSString *path = [directory stringByAppendingPathComponent:[key stringByAppendingString:@".json"]];
        if (!data || ![data writeToFile:path options:NSDataWritingAtomic error:nil]) MDIE("Cannot save shader variant");
        MLOG("Captured Zelda %s variant %s batch=%d", stage == 0 ? "vertex" : "fragment", key.UTF8String, z_variant_collect);
        if (background) { zelda_variant_request(name); return nil; }
        z_variant_missing = 1;
        if (z_variant_collect) return nil;
        // A later miss can lie beyond this submit's earlier precompile scan.
        pso_predict_rest();
        if (![NSFileManager.defaultManager fileExistsAtPath:library]) zelda_compile_shaders();
        if (![NSFileManager.defaultManager fileExistsAtPath:library]) {
            MLOG("BLOCKED shader variant %s remains unsupported", key.UTF8String); exit(78);
        }
        z_variant_missing = 0;
    }
    int pending = 0;
    NvShader *variant = background ? zelda_variant_load(name, stage, &pending) : shader_get(name, stage);
    if (pending) return nil;
    if (!variant || variant->rf.nb != shader->rf.nb || variant->rf.abi != shader->rf.abi ||
        variant->arg_len != shader->arg_len || variant->rf.c1_len != shader->rf.c1_len ||
        memcmp(variant->rf.b, shader->rf.b, shader->rf.nb * sizeof(nvn_rbind)) ||
        memcmp(variant->rf.c1, shader->rf.c1, shader->rf.c1_len) ||
        memcmp(variant->rf.drv_read, shader->rf.drv_read, sizeof shader->rf.drv_read))
        MDIE("Shader interface variant changed resource bindings");
    return variant->fn;
}

static id<MTLFunction> zelda_fragment_function(NvShader *shader, const uint32_t formats[8]) {
    unsigned mask = 0;
    for (unsigned i = 0; i < 8; i++) mask |= zelda_fragment_class(formats[i]) << (2 * i);
    return zelda_shader_variant(shader, mask, 1, nil);
}

static id<MTLFunction> zelda_vertex_function(NvShader *shader, const va_st attributes[16], NvShader *fragment) {
    if (shader->z_layered || shader->z_wide_line) return shader->fn;
    unsigned mask = 0;
    for (int i = 0; i < shader->rf.nv; i++) {
        unsigned a = shader->rf.v[i].nvn, loc = shader->rf.v[i].loc;
        if (a < 16 && loc < 16) mask |= zelda_vertex_input_class(attributes[a].fmt) << (2 * loc);
    }
    return zelda_shader_variant(shader, mask, 0, fragment);
}
