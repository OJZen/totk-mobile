from pathlib import Path
import ast
import os
import sys
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / 'core'
sys.path.insert(0, str(ROOT / 'script'))
from prepare_graphics import adapt_interface, adapt_glue, adapt_objects, adapt_backend


class GraphicsTests(unittest.TestCase):
    def test_frame_probe_is_idle_by_default_and_bounds_slow_phase_logs(self):
        source = r'''#include <stdint.h>
#include <time.h>
#include <stdio.h>
#include <string.h>
#include <assert.h>
static uint64_t wall=1000,cpu=500;
static unsigned clocks,outputs;
static char line[256];
static uint64_t now_ns(void) { clocks++; return wall; }
static uint64_t test_cpu(clockid_t id) { assert(id==CLOCK_THREAD_CPUTIME_ID); clocks++; return cpu; }
#define clock_gettime_nsec_np test_cpu
#define LOG(...) do { outputs++; snprintf(line,sizeof line,__VA_ARGS__); } while(0)
#include "nvn_frame_probe.h"
int main(int argc,char **argv) {
    assert(argc==2);setenv("ZELDA_FRAME_PROBE",argv[1],1);int enabled=atoi(argv[1]);
    zelda_frame_probe p=zelda_frame_probe_start();
    zelda_frame_probe_note(42,"test",p);assert(!outputs);
    wall+=11000000;cpu+=1000000;zelda_frame_probe_note(42,"test",p);
    if(enabled) {
        assert(outputs==1 && strstr(line,"frame=42 phase=test wall_ms=11.000 cpu_ms=1.000"));
        for(int i=0;i<100;i++)zelda_frame_probe_note(42,"test",p);
        assert(outputs==64);
    } else assert(!clocks && !outputs && !p.wall);
}
'''
        with tempfile.TemporaryDirectory(dir=ROOT / 'work/tmp') as temp:
            code, executable = Path(temp) / 'frame-probe.c', Path(temp) / 'frame-probe'
            code.write_text(source)
            subprocess.run(['clang', '-Wall', '-Wextra', '-Werror', '-I', str(ROOT / 'host'),
                            str(code), '-o', str(executable)], check=True)
            for enabled in ('0', '1'):
                subprocess.run([str(executable), enabled], check=True)

    def test_cached_texture_lookup_borrows_only_current_owned_views(self):
        backend = adapt_backend((CORE / 'gfx/nvn/nvn_mtl.m').read_text())
        self.assertEqual(backend.count('zelda_cached_texture(tid, &cached)'), 2)
        cache = (ROOT / 'host/nvn_texture_cache.inc').read_text()
        current = cache[cache.index('static int zelda_texture_view_current'):cache.index('static void zelda_trim_texture_views')]
        source = '''#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <assert.h>
#include <stdlib.h>
@interface Texture : NSObject
@property(strong) id parentTexture;
@end
@implementation Texture @end
typedef struct { int t, ok; void *view; uint64_t view_ser, view_dirty, view_rid; } treg_t;
static treg_t g_treg[2]; static int g_ntreg=2, g_tpool_n=2, calls, bump;
static uint64_t g_ser_gen=7, dirty=11;
static id<MTLTexture> base;
static uint64_t nvn_dirty_now(void) { return dirty; }
static id<MTLTexture> tex_mtl(const int *t) { assert(t==&g_treg[0].t); calls++; if(bump)g_ser_gen++; return base; }
''' + current + '''
#include "nvn_texture_lookup.inc"
int main(int argc, char **argv) { @autoreleasepool {
    assert(argc==2);int enabled;
    if (!strcmp(argv[1],"default")) { unsetenv("ZELDA_TEXTURE_LOOKUP_CACHE"); enabled=1; }
    else { setenv("ZELDA_TEXTURE_LOOKUP_CACHE",argv[1],1); enabled=atoi(argv[1]); }
    @autoreleasepool {
        Texture *parent=[Texture new], *view=[Texture new]; view.parentTexture=parent; base=(id)parent;
        g_treg[0]=(treg_t){.ok=1,.view=(void *)CFBridgingRetain(view),.view_rid=123};
    }
    __weak id weak=(__bridge id)g_treg[0].view;
    void *out=(void *)0x123;
    assert(zelda_cached_texture(0,&out)==enabled);
    if (enabled) {
        assert(out==g_treg[0].view && calls==1 && g_treg[0].view_ser==7 && g_treg[0].view_dirty==11);
        assert(zelda_cached_texture(0,&out) && calls==1);
        g_ser_gen++; assert(zelda_cached_texture(0,&out) && calls==2);
        dirty++; assert(zelda_cached_texture(0,&out) && calls==3);
        id old=base; base=(id)[Texture new]; g_ser_gen++;
        uint64_t previous=g_treg[0].view_ser;
        assert(!zelda_cached_texture(0,&out) && calls==4 && g_treg[0].view_ser==previous);
        base=old; bump=1; assert(zelda_cached_texture(0,&out) && g_treg[0].view_ser!=g_ser_gen);
        bump=0; assert(zelda_cached_texture(0,&out) && g_treg[0].view_ser==g_ser_gen);
        base=nil; g_ser_gen++; assert(!zelda_cached_texture(0,&out)); base=old;
        g_treg[0].ok=0; assert(!zelda_cached_texture(0,&out)); g_treg[0].ok=1;
        g_tpool_n=1; assert(!zelda_cached_texture(1,&out));
        assert(!zelda_cached_texture(-1,&out) && !zelda_cached_texture(2,&out));
        assert(z_texture_lookup_hits==6);
    } else { assert(out==(void *)0x123 && !calls && !z_texture_lookup_hits); }
    assert(weak); CFRelease(g_treg[0].view); g_treg[0].view=NULL;
    assert(!weak); // The lookup did not add an autorelease lifetime beyond the descriptor owner.
    assert(!zelda_cached_texture(0,&out));
} }
'''
        with tempfile.TemporaryDirectory(dir=ROOT / 'work/tmp') as temp:
            code, executable = Path(temp) / 'texture-lookup.m', Path(temp) / 'texture-lookup'
            code.write_text(source)
            subprocess.run(['clang', '-fobjc-arc', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                            '-framework', 'Foundation', '-I', str(ROOT / 'host'), str(code), '-o', str(executable)], check=True)
            for enabled in ('0', '1', 'default'):
                subprocess.run([str(executable), enabled], check=True)

    def test_color_depth_join_rejects_resource_and_predicate_aliases(self):
        gpu = (ROOT / 'host/nvn_query_gpu.inc').read_text()
        clear = (ROOT / 'host/nvn_color_clear.inc').read_text()
        join = gpu[gpu.index('static int zelda_depth_color_join'):gpu.index('static uint64_t z_depth_query_forwards')]
        touches = clear[clear.index('static int zelda_color_clear_touches'):clear.index('// Commands that may run before pending clears')]
        source = '''#include "nvn_obj.h"
#include <assert.h>
#include <stdlib.h>
static int g_enc;
static struct { int pending; } z_depth_clear;
static struct { p_set_rt rt; } R;
static struct { uint64_t address; } z_render;
typedef struct { int index; uint64_t predicate; } zelda_color_clear_t;
static struct { int count; zelda_color_clear_t item[8]; } z_color_clear;
static int clear_conflict;
static int zelda_pending_clear_conflicts(unsigned targets) { assert(targets==255); return clear_conflict; }
static uint64_t nvn_canonical_address(uint64_t address, uint64_t bytes) {
    if (!address || !bytes || address+bytes<address) return 0;
    return address>=0x10000 ? address-0x10000 : address;
}
''' + touches + join + '''
int main(int argc, char **argv) {
    assert(argc==2); setenv("ZELDA_COLOR_DEPTH_CLEAR",argv[1],1); int enabled=atoi(argv[1]);
    tex_t color={0}, depth={0}; color.mem=(void *)0x1000; depth.mem=(void *)0x2000;
    color.L.size=depth.L.size=256; R.rt.n=1; R.rt.color[0]=&color; R.rt.depth=&depth;
    z_render.address=0x3000; z_color_clear.count=1; z_color_clear.item[0].predicate=0x4000;
    assert(zelda_depth_color_join()==enabled);
    depth.mem=(void *)0x11000; assert(!zelda_depth_color_join()); // Aliased color/depth storage.
    depth.mem=(void *)0x14000; assert(!zelda_depth_color_join()); // Depth writes an earlier predicate.
    depth.mem=(void *)0x2000; z_render.address=0x11000; assert(!zelda_depth_color_join());
    z_render.address=0x12000; assert(!zelda_depth_color_join());
    z_render.address=0x4000; assert(zelda_depth_color_join()==enabled); // Sharing read-only predicates is safe.
    z_render.address=0; assert(zelda_depth_color_join()==enabled); // Ordinary depth clear becomes a load action.
    clear_conflict=1; assert(!zelda_depth_color_join()); clear_conflict=0;
    g_enc=1; assert(!zelda_depth_color_join()); g_enc=0;
    z_depth_clear.pending=1; assert(!zelda_depth_color_join()); z_depth_clear.pending=0;
    depth.L.size=0; assert(!zelda_depth_color_join()); depth.L.size=256;
    color.mem=NULL; assert(!zelda_depth_color_join()); color.mem=(void *)0x1000;
    z_color_clear.count=0; assert(!zelda_depth_color_join());
}
'''
        with tempfile.TemporaryDirectory(dir=ROOT / 'work/tmp') as temp:
            code, executable = Path(temp) / 'color-depth.c', Path(temp) / 'color-depth'
            code.write_text(source)
            subprocess.run(['clang', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                            '-I', str(CORE / 'gfx/nvn'), '-I', str(CORE / 'gfx/nvn/include'),
                            str(code), '-o', str(executable)], check=True)
            for enabled in ('0', '1'):
                subprocess.run([str(executable), enabled], check=True)

    def test_lazy_depth_reports_keep_only_deferred_counter_types(self):
        gpu = (ROOT / 'host/nvn_query_gpu.inc').read_text()
        helper = gpu[gpu.index('static uint64_t z_depth_query_forwards'):gpu.index('static int zelda_gpu_depth_clear')]
        source = '''#include <assert.h>
#include <stdlib.h>
#include <string.h>
#include "nvn_obj.h"
''' + helper + '''
int main(int argc, char **argv) {
    assert(argc == 2); setenv("ZELDA_LAZY_DEPTH_CLEAR", argv[1], 1); int enabled=atoi(argv[1]);
    const uint64_t types[]={NVN_COUNTER_TYPE_TIMESTAMP,NVN_COUNTER_TYPE_TIMESTAMP_TOP,NVN_COUNTER_TYPE_SAMPLES_PASSED};
    for(unsigned i=0;i<3;i++)assert(zelda_depth_report_deferred(&types[i])==enabled);
    uint64_t other=UINT64_MAX;assert(!zelda_depth_report_deferred(&other));
    assert(z_depth_reports_kept==(enabled ? 3u : 0u));(void)z_depth_query_forwards;
}
'''
        with tempfile.TemporaryDirectory(dir=ROOT / 'work/tmp') as temp:
            code, executable = Path(temp) / 'depth-reports.c', Path(temp) / 'depth-reports'
            code.write_text(source)
            subprocess.run(['clang', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                            '-I', str(CORE / 'gfx/nvn'), '-I', str(CORE / 'gfx/nvn/include'),
                            str(code), '-o', str(executable)], check=True)
            for enabled in ('0', '1'):
                subprocess.run([str(executable), enabled], check=True)
        depth = gpu[gpu.index('static int zelda_gpu_depth_clear'):]
        self.assertLess(depth.index('rp_flush();'), depth.index('zelda_query_forward('))
        self.assertIn('else { rp_flush(); zelda_query_flush(); known = 0; join = 0; }', depth)
        self.assertIn('start + 20, &known, 4', depth)
        self.assertIn('z_pass_depth_written = 1;', (ROOT / 'host/nvn_depth_clear.inc').read_text())
        backend = adapt_backend((CORE / 'gfx/nvn/nvn_mtl.m').read_text())
        self.assertIn('(h.op == OP_REPORT_COUNTER && !zelda_depth_report_deferred(a))', backend)

    def test_pipeline_keys_ignore_only_inactive_blending_and_padding(self):
        source = r'''#include <assert.h>
typedef struct { char key[24]; int stage; } NvShader;
#include "nvn_pso_skey.h"
#include "nvn_pso_key.h"
int main(void) {
    pso_key a={0},b={0};
    a.cfmt[0]=b.cfmt[0]=70;a.blend_en=b.blend_en=0xff;a.chmask=b.chmask=0x12345678;
    memset(a.blend,0xa5,sizeof a.blend);memset(b.blend,0x5a,sizeof b.blend);
    a.blend[0].src_rgb=b.blend[0].src_rgb=2;a.blend[0].dst_rgb=b.blend[0].dst_rgb=1;
    a.blend[0].src_a=b.blend[0].src_a=5;a.blend[0].dst_a=b.blend[0].dst_a=6;
    a.blend[0].eq_rgb=b.blend[0].eq_rgb=3;a.blend[0].eq_a=b.blend[0].eq_a=4;
    a.attr[2]=b.attr[2]=(va_st){.fmt=0x2e,.offset=16,.stream=3};
    a.stream[3]=b.stream[3]=(vs_st){.stride=32,.divisor=2};a.dfmt=b.dfmt=252;
    zelda_pso_normalize(&a,0);zelda_pso_normalize(&b,0);
    assert(!memcmp(&a,&b,sizeof a));assert(a.blend_en==1 && a.chmask==8);
    assert(a.blend[0].src_a==5 && a.blend[0].eq_a==4 && !a.blend[0].target);
    assert(a.dfmt==252 && a.attr[2].offset==16 && a.stream[3].divisor==2);
    b.blend[0].src_rgb=3;zelda_pso_normalize(&b,0);assert(memcmp(&a,&b,sizeof a));
    a.blend_en=b.blend_en=0;zelda_pso_normalize(&a,0);zelda_pso_normalize(&b,0);
    assert(!memcmp(&a,&b,sizeof a));
    pso_key before=a;zelda_pso_normalize(&a,0);assert(!memcmp(&a,&before,sizeof a));
    pso_key optional={0};optional.cfmt[0]=70;optional.cfmt[1]=70;optional.cfmt[3]=70;optional.cfmt[5]=92;
    optional.chmask=15u<<4;
    before=optional;
    assert(zelda_pso_optional_colors(&optional,(1u<<1)|(1u<<3))==((1u<<0)|(1u<<5)));
    assert(!memcmp(&optional,&before,sizeof optional));
    optional.chmask|=1u<<20;assert(zelda_pso_optional_colors(&optional,0xff)==0);
    assert(zelda_pso_optional_colors(&optional,1u<<3)==1);
    optional.cfmt[0]=0;assert(zelda_pso_optional_colors(&optional,1u<<3)==0);
    optional=(pso_key){0};for(unsigned i=0;i<8;i++)optional.cfmt[i]=70;
    assert(zelda_pso_optional_colors(&optional,0)==0); // Bound speculative startup memory.
    assert(zelda_pso_optional_colors(&optional,0xfc)==3);
    memset(&a,0xa5,sizeof a);before=a;zelda_pso_normalize(&a,1);assert(!memcmp(&a,&before,sizeof a));
}
'''
        with tempfile.TemporaryDirectory(dir=ROOT / 'work/tmp') as temp:
            code, executable = Path(temp) / 'pipeline.c', Path(temp) / 'pipeline'
            code.write_text(source)
            subprocess.run(['clang', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                            '-I', str(ROOT / 'host'), '-I', str(CORE / 'gfx/nvn'), '-I', str(CORE / 'gfx/nvn/include'),
                            str(code), '-o', str(executable)], check=True)
            subprocess.run([str(executable)], check=True)
        backend = adapt_backend((CORE / 'gfx/nvn/nvn_mtl.m').read_text())
        self.assertIn('zelda_pso_normalize(k, lmap);', backend)
        self.assertIn('zelda_pso_normalize(&k, sk.merged);', backend)
        warm = (ROOT / 'host/nvn_shader_warm.inc').read_text()
        self.assertIn('zelda_pso_normalize(&k, 0);', warm)
        self.assertLess(warm.index('[pending_keys containsObject:identity]'), warm.index('pso_desc(&k'))

    def test_vertex_streams_follow_current_shader_attributes(self):
        source = r'''#include <assert.h>
#include "nvn_obj.h"
#include "nvn_refl.h"
#include "nvn_vertex_streams.h"
int main(void) {
    va_st a[16] = {0};
    a[0].fmt = a[2].fmt = a[7].fmt = 1;
    a[0].stream = 0; a[2].stream = 4; a[7].stream = 9;
    nvn_refl r = {.route_b = 1, .nv = 3};
    r.v[0].nvn = 0; r.v[1].nvn = r.v[2].nvn = 2;
    assert(zelda_vertex_streams(&r, a, 16) == 0x11); // Duplicate attribute, stale stream 9 excluded.
    assert(zelda_vertex_streams(&r, a, 1) == 1);
    a[0].fmt = 0; assert(zelda_vertex_streams(&r, a, 16) == 0x10); // Zero-format fallback uses buffer 30.
    r.nv = 1; r.v[0].nvn = 7;
    assert(zelda_vertex_streams(&r, a, 16) == 0x200); // Program switch must use its new stream.
    a[7].stream = 15; assert(zelda_vertex_streams(&r, a, 16) == 0x8000);
    a[7].stream = 20; assert(zelda_vertex_streams(&r, a, 16) == 0xffff);
    r.v[0].nvn = 31; assert(zelda_vertex_streams(&r, a, 16) == 0);
    r.nv = 0; assert(zelda_vertex_streams(&r, a, 16) == 0);
    r.route_b = 0; assert(zelda_vertex_streams(&r, a, 16) == 0xffff);
    r.route_b = 1; r.nv = 33; assert(zelda_vertex_streams(&r, a, 16) == 0xffff);
    r.nv = -1; assert(zelda_vertex_streams(&r, a, 16) == 0xffff);
    r.nv = 0; assert(zelda_vertex_streams(&r, a, -1) == 0xffff);
    assert(zelda_vertex_streams(&r, a, 17) == 0xffff);
    assert(zelda_vertex_streams(NULL, a, 16) == 0xffff);
}
'''
        with tempfile.TemporaryDirectory(dir=ROOT / 'work/tmp') as temp:
            code, executable = Path(temp) / 'streams.c', Path(temp) / 'streams'
            code.write_text(source)
            subprocess.run(['clang', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                            '-I', str(ROOT / 'host'), '-I', str(CORE / 'gfx/nvn'), '-I', str(CORE / 'gfx/nvn/include'),
                            str(code), '-o', str(executable)], check=True)
            subprocess.run([str(executable)], check=True)
        backend = adapt_backend((CORE / 'gfx/nvn/nvn_mtl.m').read_text())
        draw = backend[backend.index('static uint64_t z_conditional_gpu;'):backend.index('static nvn_hmap g_cps')]
        self.assertLess(draw.index('if (!(z_streams & (1u << s_)))'), draw.index('mtl_gpu_addr(S.vbuf[s_].address'))
        self.assertIn('enc_r_vbuf(en, 30, zero_gpu);', draw)
        self.assertIn('!pr->sh[0]->z_wide_line ? zelda_vertex_streams', draw)

    def test_adapter_replacements_do_not_silently_override_each_other(self):
        source = (ROOT / 'script/prepare_graphics.py').read_text()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Dict):
                keys = [k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)]
                self.assertEqual(len(keys), len(set(keys)), f'duplicate replacement near line {node.lineno}')

    def test_conditional_draw_lookahead_stops_before_writes_and_unknown_commands(self):
        planner = (ROOT / 'host/nvn_conditional_draw.inc').read_text().split('static uint64_t zelda_conditional_encode(')[0]
        backend = adapt_backend((CORE / 'gfx/nvn/nvn_mtl.m').read_text())
        self.assertIn('if (!z_conditional_gpu && zelda_fsc_draw(pr, dw, di)) return;', backend)
        self.assertIn('BLOCKED NVN primitive restart index=', backend)
        self.assertIn('indirectBuffer:z_conditional_gpu', backend)
        source = r'''#import <Metal/Metal.h>
#include <assert.h>
#include <pthread.h>
#include "nvn_obj.h"
#include "nvn_refl.h"
#include "nvn_line_width.h"
@interface NvShader : NSObject { @public nvn_refl rf; } @end
@implementation NvShader @end
@interface NvProg : NSObject { @public NvShader *sh[6]; } @end
@implementation NvProg @end
static NvProg *prog_get(const prog_t *p) { return (__bridge NvProg *)(const void *)p; }
static struct { p_set_rt rt; } R;
static const uint8_t *g_exec_cur, *g_exec_next;
static uint64_t alias, underlying;
typedef struct { int kind; volatile uint32_t *val; uint32_t mode,v; uint64_t addr; uint32_t type; void *vis; } post_op;
static pthread_mutex_t g_mu=PTHREAD_MUTEX_INITIALIZER;
static uint64_t g_done;
static post_op g_post[4];static int g_npost;
uint64_t nvn_canonical_address(uint64_t address, uint64_t size) { return !size ? 0 : address == alias ? underlying : address; }
''' + planner + r'''
static size_t emit(uint8_t *p, unsigned op, const void *data, size_t size) {
    size_t n=(size+15)&~7u; op_hdr h={.op=op,.size=n}; memcpy(p,&h,8); if(size)memcpy(p+8,data,size); return n;
}
int main(void) { @autoreleasepool {
    (void)zelda_conditional_msl; (void)z_conditional_draws; (void)z_conditional_batches; (void)z_conditional_fallbacks; (void)z_conditional_max_batch;
    setenv("ZELDA_GPU_CONDITIONAL","2",1);
    NvShader *shader=[NvShader new]; shader->rf.route_b=1; shader->rf.nb=1; shader->rf.b[0].kind=NVN_RB_CBUF;
    NvProg *program=[NvProg new];program->sh[0]=shader;program->sh[1]=shader;
    const prog_t *pg=(__bridge const void *)program; assert(zelda_conditional_readonly(pg));
    for(unsigned kind=NVN_RB_CBUF;kind<=NVN_RB_UBOP;kind++) {
        shader->rf.b[0].kind=kind;
        int allowed=kind==NVN_RB_CBUF||kind==NVN_RB_C1||kind==NVN_RB_SUPPORT||kind==NVN_RB_TEX||kind==NVN_RB_TBUF||kind==NVN_RB_SAMP||kind==NVN_RB_SAMPT;
        assert(zelda_conditional_readonly(pg)==allowed);
        shader->rf.b[0].rw=1;assert(!zelda_conditional_readonly(pg));shader->rf.b[0].rw=0;
    }
    shader->rf.b[0].kind=NVN_RB_CBUF; shader->rf.route_b=0;assert(!zelda_conditional_readonly(pg));shader->rf.route_b=1;
    program->sh[2]=shader;assert(!zelda_conditional_readonly(pg));program->sh[2]=nil;
    _Alignas(16) uint64_t counters[16]={0};
    g_post[0]=(post_op){.kind=5,.addr=(uintptr_t)counters};g_npost=1;
    assert(!zelda_conditional_cpu_pending((uintptr_t)counters));
    g_post[0]=(post_op){.kind=1,.val=(uint32_t *)(counters+1)};assert(!zelda_conditional_cpu_pending((uintptr_t)counters));
    g_post[0].val=(uint32_t *)(counters+2);assert(zelda_conditional_cpu_pending((uintptr_t)counters));
    zelda_conditional_post_commit(g_post,1,10);g_npost=0;assert(zelda_conditional_cpu_pending((uintptr_t)counters));
    g_done=10;assert(!zelda_conditional_cpu_pending((uintptr_t)counters));
    g_post[0]=(post_op){.kind=2,.addr=(uintptr_t)counters};g_npost=1;assert(zelda_conditional_cpu_pending((uintptr_t)counters));g_npost=0;

    zelda_conditional_state state={.program=pg,.predicate={.address=(uintptr_t)counters,.mode=1},.line_width=1};
    p_draw dw={.prim=4,.first=5,.count=3,.instances=2,.base_instance=7};
    p_draw_idx di={.prim=4,.itype=1,.count=3,.instances=2,.base_vertex=-5,.base_instance=7};
    zelda_conditional_input first,input;
    assert(zelda_conditional_input_make(&dw,NULL,&state,&first));assert(first.args[2]==5&&first.args[3]==7);
    assert(zelda_conditional_input_make(NULL,&di,&state,&input));assert(input.args[2]==0&&input.args[3]==(uint32_t)-5&&input.args[4]==7);
    di.itype=0;assert(!zelda_conditional_input_make(NULL,&di,&state,&input));di.itype=1;
    state.restart=1;state.restart_index=42;assert(!zelda_conditional_input_make(NULL,&di,&state,&input));state.restart_index=65535;
    assert(zelda_conditional_input_make(NULL,&di,&state,&input));state.restart=0;
    dw.base_instance=-1;assert(!zelda_conditional_input_make(&dw,NULL,&state,&input));dw.base_instance=7;
    state.line_width=4.5;dw.prim=3;assert(!zelda_conditional_input_make(&dw,NULL,&state,&input));dw.prim=4;
    assert(zelda_conditional_input_make(&dw,NULL,&state,&input));state.line_width=1;
    _Alignas(8) uint8_t commands[8192]={0};g_exec_cur=commands+8000;g_exec_next=commands;
    p_render_enable off={.enabled=1}, condition={.address=(uintptr_t)(counters+4),.mode=0};
    size_t n=emit(commands,OP_RENDER_ENABLE,&off,sizeof off);
    n+=emit(commands+n,OP_DRAW,&dw,sizeof dw);
    n+=emit(commands+n,OP_RENDER_ENABLE,&condition,sizeof condition);
    n+=emit(commands+n,OP_DRAW_IDX,&di,sizeof di);
    n+=emit(commands+n,OP_DRAW,&dw,sizeof dw);emit(commands+n,OP_END,NULL,0);
    assert(zelda_conditional_collect(&first,state)==3);
    assert(z_conditional_commands[0]==g_exec_cur&&z_conditional_inputs[1].predicate==condition.address&&z_conditional_inputs[1].mode==0);
    assert(z_conditional_inputs[2].args[2]==5);
    const unsigned boundaries[]={OP_REPORT_COUNTER,OP_RESET_COUNTER,OP_COPY_B2B,OP_COPY_B2T,OP_COPY_T2B,OP_COPY_T2T,
        OP_UPDATE_UB,OP_CLEAR_TEX,OP_CLEAR_COLOR,OP_CLEAR_DS,OP_DISPATCH,OP_BARRIER,OP_SET_RT,OP_SIGNAL_EVENT,OP_CALL,OP_JUMP,OP_END,OP_COUNT_};
    for(unsigned i=0;i<sizeof boundaries/sizeof *boundaries;i++) {
        n=emit(commands,boundaries[i],NULL,0);n+=emit(commands+n,OP_DRAW,&dw,sizeof dw);emit(commands+n,OP_END,NULL,0);
        assert(zelda_conditional_collect(&first,state)==1);
    }
    emit(commands,OP_BIND_STATE,NULL,0);assert(zelda_conditional_collect(&first,state)==1);
    ((op_hdr *)commands)->size=0;assert(zelda_conditional_collect(&first,state)==1);
    p_bind_state width={.kind=9};float w=4.5;memcpy(width.bytes,&w,4);dw.prim=3;
    n=emit(commands,OP_BIND_STATE,&width,sizeof width);n+=emit(commands+n,OP_DRAW,&dw,sizeof dw);emit(commands+n,OP_END,NULL,0);
    assert(zelda_conditional_collect(&first,state)==1);dw.prim=4;
    tex_t target={.mem=(uint8_t *)(counters+1),.L={.size=8}};R.rt=(p_set_rt){.n=1,.color={&target}};
    assert(!zelda_conditional_target_safe((uintptr_t)counters));
    alias=(uintptr_t)counters+0x100000;underlying=(uintptr_t)counters;assert(!zelda_conditional_target_safe(alias));R.rt=(p_set_rt){0};
    assert(zelda_conditional_target_safe(alias));alias=underlying=0;
    NvShader *writer=[NvShader new];writer->rf.route_b=1;writer->rf.nb=1;writer->rf.b[0].kind=NVN_RB_IMG;
    NvProg *writing=[NvProg new];writing->sh[0]=shader;writing->sh[1]=writer;
    p_bind_program bind={.prog=(__bridge const void *)writing};
    n=emit(commands,OP_BIND_PROGRAM,&bind,sizeof bind);n+=emit(commands+n,OP_DRAW,&dw,sizeof dw);
    bind.prog=pg;n+=emit(commands+n,OP_BIND_PROGRAM,&bind,sizeof bind);n+=emit(commands+n,OP_DRAW,&dw,sizeof dw);emit(commands+n,OP_END,NULL,0);
    assert(zelda_conditional_collect(&first,state)==1);
    n=0;for(unsigned i=0;i<70;i++)n+=emit(commands+n,OP_DRAW,&dw,sizeof dw);emit(commands+n,OP_END,NULL,0);
    assert(zelda_conditional_collect(&first,state)==Z_CONDITIONAL_MAX);
    shader->rf.b[0].kind=NVN_RB_IMG;assert(zelda_conditional_collect(&first,state)==1);
    puts("Conditional planner: state, readonly bindings, target aliases, write/control boundaries, malformed records and batch bound passed");
} }
'''
        with tempfile.TemporaryDirectory(dir=ROOT / 'work/tmp') as temp:
            code, executable = Path(temp) / 'conditional.m', Path(temp) / 'conditional'
            code.write_text(source)
            subprocess.run(['clang', '-Wall', '-Wextra', '-Werror', '-fobjc-arc', '-fsanitize=undefined',
                            '-ivfsoverlay', str(ROOT / 'work/build/graphics-overlay.json'),
                            '-I', str(ROOT / 'host'), '-I', str(CORE / 'gfx/nvn'), '-I', str(CORE / 'gfx/nvn/include'),
                            '-framework', 'Foundation', '-framework', 'Metal', str(code), '-o', str(executable)], check=True)
            subprocess.run([str(executable)], check=True)

    def test_depth_state_cache_ignores_bitfield_padding(self):
        backend = adapt_backend((CORE / 'gfx/nvn/nvn_mtl.m').read_text())
        start = backend.index('static id<MTLDepthStencilState> dss_get(')
        function = backend[start:backend.index('// \u53c2\u6570\u7f13\u51b2', start)]
        source = '''#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include "nvn_obj.h"
#include <assert.h>
static id<MTLDevice> g_dev;
static struct { ds_st ds; uint32_t st_vmask[2], st_wmask[2]; } S;
static struct { size_t klen; } g_dss;
static int g_rt_ovr;
static NSMutableDictionary *cache;
static id hm_get_id(const void *table, const void *key) {
    (void)table; return cache[[NSData dataWithBytes:key length:g_dss.klen]];
}
static void hm_put_id(const void *table, const void *key, id value) {
    (void)table; cache[[NSData dataWithBytes:key length:g_dss.klen]] = value;
}
''' + function + '''
int main(void) { @autoreleasepool {
    g_dev = MTLCreateSystemDefaultDevice(); assert(g_dev);
    cache = [NSMutableDictionary dictionary];
    S.ds = (ds_st){1, 1, NVN_DEPTH_FUNC_LESS, 1, NVN_STENCIL_FUNC_ALWAYS, NVN_STENCIL_FUNC_ALWAYS,
        NVN_STENCIL_OP_KEEP, NVN_STENCIL_OP_KEEP, NVN_STENCIL_OP_REPLACE,
        NVN_STENCIL_OP_KEEP, NVN_STENCIL_OP_KEEP, NVN_STENCIL_OP_REPLACE};
    S.st_vmask[0] = S.st_vmask[1] = S.st_wmask[0] = S.st_wmask[1] = 255;
    uint64_t raw; memcpy(&raw, &S.ds, sizeof raw); raw &= (1ull << 39) - 1;
    for (int mode = 0; mode < 4; mode++) {
        [cache removeAllObjects]; g_rt_ovr = mode == 3 ? 3 : mode == 2 ? 1 : 0;
        memcpy(&S.ds, &raw, sizeof raw);
        id expected = dss_get(1, mode != 0); assert(expected);
        for (unsigned i = 1; i < 4096; i++) {
            uint64_t bits = raw | ((uint64_t)i << 39); memcpy(&S.ds, &bits, sizeof bits);
            assert(dss_get(1, mode != 0) == expected);
        }
        assert(cache.count == 1);
        S.ds.df = NVN_DEPTH_FUNC_GREATER; assert(dss_get(1, mode != 0)); assert(cache.count == 2);
    }
    puts("Depth/stencil cache: padding ignored, active state changes and stencil overrides preserved");
} }
'''
        with tempfile.TemporaryDirectory(dir=ROOT / 'work/tmp') as temp:
            code, executable = Path(temp) / 'depth-state.m', Path(temp) / 'depth-state'
            code.write_text(source)
            subprocess.run(['clang', '-O2', '-fobjc-arc', '-Wall', '-Wextra', '-Werror',
                            '-ivfsoverlay', str(ROOT / 'work/build/graphics-overlay.json'),
                            '-I', str(CORE / 'gfx/nvn'), '-I', str(CORE / 'gfx/nvn/include'),
                            '-framework', 'Foundation', '-framework', 'Metal',
                            str(code), '-o', str(executable)], check=True)
            subprocess.run([str(executable)], env={**os.environ, 'MTL_DEBUG_LAYER': '1'}, check=True)

    def test_depth16_load_clears_match_fragment_depth_writes(self):
        backend = adapt_backend((CORE / 'gfx/nvn/nvn_mtl.m').read_text())
        self.assertIn('pd.depthAttachment.clearDepth = zelda_depth_clear_value(pf, R.cd);', backend)
        self.assertIn('rp.depthAttachment.clearDepth = zelda_depth_clear_value(t.pixelFormat, c->c.f[0]);', backend)
        source = r'''#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include "nvn_depth16_clear.h"
#include <assert.h>
#include <stdio.h>
static const char *msl =
    "#include <metal_stdlib>\nusing namespace metal;\n"
    "vertex float4 vs(uint id [[vertex_id]]) { return float4(id == 1 ? 3.0 : -1.0, id == 2 ? 3.0 : -1.0, 0.5, 1.0); }\n"
    "struct D { float depth [[depth(any)]]; };\n"
    "fragment D fs(constant float &depth [[buffer(0)]]) { return {depth}; }\n";
int main(void) { @autoreleasepool {
    id<MTLDevice> device = MTLCreateSystemDefaultDevice(); assert(device);
    NSError *error = nil;
    id<MTLLibrary> library = [device newLibraryWithSource:@(msl) options:nil error:&error]; assert(library);
    MTLRenderPipelineDescriptor *pd = [MTLRenderPipelineDescriptor new];
    pd.vertexFunction = [library newFunctionWithName:@"vs"]; pd.fragmentFunction = [library newFunctionWithName:@"fs"];
    pd.depthAttachmentPixelFormat = MTLPixelFormatDepth16Unorm;
    id<MTLRenderPipelineState> pipeline = [device newRenderPipelineStateWithDescriptor:pd error:&error]; assert(pipeline);
    MTLDepthStencilDescriptor *dd = [MTLDepthStencilDescriptor new];
    dd.depthCompareFunction = MTLCompareFunctionAlways; dd.depthWriteEnabled = YES;
    id<MTLDepthStencilState> write = [device newDepthStencilStateWithDescriptor:dd];
    MTLTextureDescriptor *td = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatDepth16Unorm width:1 height:1 mipmapped:NO];
    td.usage = MTLTextureUsageRenderTarget; td.storageMode = MTLStorageModePrivate;
    id<MTLTexture> depth = [device newTextureWithDescriptor:td];
    // Integer bins, half-bin boundaries and their neighbouring floats across the range, and 0.5 (32767 / 32768).
    enum { N = 3 * 4 * 683 + 1 };
    static float values[N]; int n = 0;
    for (int k = 1; k < 65535 && n + 12 <= N - 1; k += 96)
        for (int half = 0; half < 2; half++) {
            float v = (float)((k + 0.5 * half) / 65535.0);
            values[n++] = v; values[n++] = nextafterf(v, 0.0f); values[n++] = nextafterf(v, 1.0f);
            float w = (float)((k + 0.5 * half + 0.25) / 65535.0);
            values[n++] = w; values[n++] = nextafterf(w, 0.0f); values[n++] = nextafterf(w, 1.0f);
        }
    values[n++] = 0.5f;
    id<MTLBuffer> cleared = [device newBufferWithLength:4 * N options:MTLResourceStorageModeShared];
    id<MTLBuffer> plain = [device newBufferWithLength:4 * N options:MTLResourceStorageModeShared];
    id<MTLBuffer> drawn = [device newBufferWithLength:4 * N options:MTLResourceStorageModeShared];
    id<MTLCommandQueue> queue = [device newCommandQueue];
    id<MTLCommandBuffer> commands = [queue commandBuffer];
    for (int i = 0; i < n; i++)
        for (int path = 0; path < 3; path++) {
            MTLRenderPassDescriptor *rp = [MTLRenderPassDescriptor new];
            rp.depthAttachment.texture = depth; rp.depthAttachment.storeAction = MTLStoreActionStore;
            rp.depthAttachment.loadAction = MTLLoadActionClear;
            rp.depthAttachment.clearDepth = path == 0 ? zelda_depth_clear_value(MTLPixelFormatDepth16Unorm, values[i]) : path == 1 ? values[i] : 0.0;
            id<MTLRenderCommandEncoder> encoder = [commands renderCommandEncoderWithDescriptor:rp];
            if (path == 2) {
                [encoder setRenderPipelineState:pipeline]; [encoder setDepthStencilState:write];
                [encoder setFragmentBytes:&values[i] length:sizeof values[i] atIndex:0];
                [encoder drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
            }
            [encoder endEncoding];
            id<MTLBlitCommandEncoder> blit = [commands blitCommandEncoder];
            [blit copyFromTexture:depth sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0, 0, 0) sourceSize:MTLSizeMake(1, 1, 1)
                         toBuffer:path == 0 ? cleared : path == 1 ? plain : drawn destinationOffset:4 * i destinationBytesPerRow:2 destinationBytesPerImage:2];
            [blit endEncoding];
        }
    [commands commit]; [commands waitUntilCompleted]; assert(!commands.error);
    int plain_differs = 0;
    for (int i = 0; i < n; i++) {
        uint16_t c = *(uint16_t *)((uint8_t *)cleared.contents + 4 * i), p = *(uint16_t *)((uint8_t *)plain.contents + 4 * i);
        uint16_t d = *(uint16_t *)((uint8_t *)drawn.contents + 4 * i);
        if (c != d) { fprintf(stderr, "depth %.9g: clear %u, fragment %u\n", values[i], c, d); return 1; }
        plain_differs += p != d;
    }
    printf("Depth16 clears: %d depths equal to fragment writes (%d differ without the correction)\n", n, plain_differs);
} }
'''
        with tempfile.TemporaryDirectory(dir=ROOT / 'work/tmp') as temp:
            code, executable = Path(temp) / 'depth16-clear.m', Path(temp) / 'depth16-clear'
            code.write_text(source)
            subprocess.run(['clang', '-fobjc-arc', '-Wall', '-Wextra', '-Werror', '-I', str(ROOT / 'host'),
                            '-framework', 'Foundation', '-framework', 'Metal', str(code), '-o', str(executable)], check=True)
            subprocess.run([str(executable)], env={**os.environ, 'MTL_DEBUG_LAYER': '1'}, check=True)

    def test_depth_demand_follows_nested_commands_and_keeps_uncertain_planes(self):
        backend = adapt_backend((CORE / 'gfx/nvn/nvn_mtl.m').read_text())
        scan = backend[backend.index('enum { DS_FREE ='):backend.index('// \u5199\u56de\u524d\u77bb\u7684\u5224\u5b9a')]
        start = backend.index('static int pass_ds_need(void) {')
        demand = backend[start:backend.index('// \u7eaf\u6e05\u9664\u901a\u9053\u524d\u63d0', start)]
        source = '''#include "nvn_obj.h"
#include <assert.h>
#include <string.h>
#define PD(...) ((void)0)
static int g_hoist_n;
static int hoist_skipped(const uint8_t *p) { (void)p; return 0; }
static struct { int clear_d, clear_s; p_set_rt rt; } R;
static struct { ds_st ds; } S;
static const uint8_t *g_exec_next, *g_ret[16];
static uint64_t g_rest[16];
static int g_ret_n, g_nrest;
''' + scan + demand + '''
static size_t emit(uint8_t *p, int op, const void *data, size_t size) {
    size_t length=(size+15)&~7u; op_hdr h={.op=op,.size=length};
    memcpy(p,&h,8); if (size) memcpy(p+8,data,size); return length;
}
static size_t state(uint8_t *p, int depth, int stencil) {
    p_bind_state b={.kind=5}; ds_st ds={.dt=depth,.dw=1,.st=stencil};
    memcpy(b.bytes,&ds,sizeof ds); return emit(p,OP_BIND_STATE,&b,sizeof b);
}
int main(void) {
    uint8_t commands[256]={0}, child[256]={0}, tail[256]={0}; size_t n=0,m=0;
    tex_t texture={0}; texture.d.format=NVN_FORMAT_DEPTH32F_STENCIL8;
    R.rt.depth=&texture; g_exec_next=commands;
    n+=emit(commands+n,OP_DRAW,NULL,0); emit(commands+n,OP_END,NULL,0);
    assert(pass_ds_need()==0);
    S.ds.dw=1; assert(pass_ds_need()==0); S.ds.dt=1; assert(pass_ds_need()==1); S.ds.dt=S.ds.dw=0;
    R.clear_s=1; assert(pass_ds_need()==2); R.clear_d=1; assert(pass_ds_need()==3);
    R.clear_d=R.clear_s=0;
    n=state(commands,1,0); n+=emit(commands+n,OP_DRAW,NULL,0); emit(commands+n,OP_END,NULL,0);
    assert(pass_ds_need()==1 && !S.ds.dt);
    m=state(child,0,1); emit(child+m,OP_END,NULL,0); uint64_t address=(uintptr_t)child;
    n=emit(commands,OP_CALL,&address,8); n+=emit(commands+n,OP_DRAW,NULL,0); emit(commands+n,OP_END,NULL,0);
    assert(pass_ds_need()==2);
    S.ds.dt=1; assert(pass_ds_need()==3); S.ds.dt=0;
    // These commands enable stencil later, but depth-only formats have no stencil plane to attach.
    S.ds.dt=1;
    const int formats[]={NVN_FORMAT_DEPTH16,NVN_FORMAT_DEPTH24,NVN_FORMAT_DEPTH32F};
    for (unsigned i=0;i<sizeof formats/sizeof *formats;i++) {
        texture.d.format=formats[i]; g_ds_scanned=-1;
        assert(pass_ds_need()==3 && g_ds_scanned==-1);
    }
    R.rt.has_dview=1; R.rt.dview.set_format=1; R.rt.dview.format=NVN_FORMAT_DEPTH32F_STENCIL8;
    assert(pass_ds_need()==3); // The effective view, not the base format, decides the planes.
    texture.d.format=NVN_FORMAT_DEPTH32F_STENCIL8; R.rt.dview.format=NVN_FORMAT_DEPTH32F;
    g_ds_scanned=-1; assert(pass_ds_need()==3 && g_ds_scanned==-1);
    R.rt.has_dview=0; S.ds.dt=0;
    emit(commands,OP_END,NULL,0); n=state(tail,1,0); n+=emit(tail+n,OP_DRAW,NULL,0); emit(tail+n,OP_END,NULL,0);
    g_ret[0]=tail; g_ret_n=1; assert(pass_ds_need()==1); g_ret_n=0;
    g_rest[0]=(uintptr_t)tail; g_nrest=1; assert(pass_ds_need()==1); g_nrest=0;
    emit(commands,OP_SET_RT,NULL,0); g_ret_n=1; assert(pass_ds_need()==0); g_ret_n=0;
    emit(commands,OP_CLEAR_DS,NULL,0); assert(pass_ds_need()==3);
    emit(child,OP_CALL,&address,8); emit(commands,OP_CALL,&address,8); assert(pass_ds_need()==3);
    g_exec_next=NULL; assert(pass_ds_need()==3);
}
'''
        with tempfile.TemporaryDirectory(dir=ROOT / 'work/tmp') as temp:
            code, executable = Path(temp) / 'depth-demand.c', Path(temp) / 'depth-demand'
            code.write_text(source)
            subprocess.run(['clang', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                            '-ivfsoverlay', str(ROOT / 'work/build/graphics-overlay.json'),
                            '-I', str(CORE / 'gfx/nvn'), '-I', str(CORE / 'gfx/nvn/include'),
                            str(code), '-o', str(executable)], check=True)
            subprocess.run([str(executable)], check=True)

    def test_acquire_waits_for_its_image_and_preserves_queue_fences(self):
        interface = adapt_interface((CORE / 'gfx/nvn/hle_nvn.c').read_text())
        acquire = interface[interface.index('NVN(nvnWindowAcquireTexture,'):interface.index('// \u5e27\u8fb9\u754c：')]
        sync = interface[interface.index('NVN(nvnQueueFenceSync,'):interface.index('NVN(nvnEventBuilderSetDefaults,')]
        present = interface[interface.index('NVN(nvnQueuePresentTexture,'):]
        present = present[present.index('    gfx_enter();'):present.index('    gfx_leave();')]
        source = '''#include "nvn_obj.h"
#include "timeline.h"
#include <assert.h>
#include <stdlib.h>
#include <string.h>
#define NVN(name, ret, ...) static ret h_##name(__VA_ARGS__)
#define CTX(t) ((tex_t *)(t))
static uint64_t latest, completed, ticket, waited_ticket, waited_fence;
static uint64_t g_acq_wait_ns, g_pq_wait_ns, g_frame;
static uint64_t now_ns(void) { return 1000; }
static uint64_t guest_ns(void) { return 1000; }
static int dm_detclock_enabled(void) { return 0; }
static uint64_t present_period(const win_t *w) { (void)w; return 0; }
typedef struct { const char *name; } rt_decl;
static rt_decl g_rt_main;
static void set_time_constraint(rt_decl *d, uint64_t n) { (void)d; (void)n; }
static void rt_comp_tick(rt_decl *d, uint64_t n) { (void)d; (void)n; }
int mtl_link_active(void) { return 0; }
int mtl_link_wait_tick(uint64_t n) { (void)n; return 0; }
static void sleep_until_ns(uint64_t n) { (void)n; }
static void trow(uint64_t f, const char *s, double n) { (void)f; (void)s; (void)n; }
static void gfx_enter(void) {}
static uint64_t mtl_last_fence(void) { return latest; }
static int mtl_wait(uint64_t fence, uint64_t timeout) { (void)timeout; waited_fence=fence; return completed>=fence; }
void mtl_present_wait(uint64_t t) { waited_ticket=t; }
uint64_t mtl_present(const tex_t *t, double p, const int *crop) { (void)t; (void)p; (void)crop; latest++; return ++ticket; }
// Translation queue in place (Zelda forces DM_NVN_SYNC): a present job runs inside x_push.
enum { XJ_SUBMIT, XJ_PRESENT, XJ_FRAME };
typedef struct { int kind, n; uint64_t *h; const tex_t *tex; double period; int crop[4]; uint64_t frame, sub, host; } xjob;
static uint64_t jobs, job_ticket[16];
static uint64_t x_push(xjob *j) { job_ticket[++jobs] = mtl_present(j->tex, j->period, j->crop); return jobs; }
static uint64_t x_present_ticket(uint64_t job) { return job_ticket[job]; }
static const tex_t *snap_tex(const NVNtexture *t) { return (const tex_t *)t; }
''' + acquire + sync + '''
static void present_image(win_t *o, int idx) {
''' + present + '''
    (void)t0;
}
int main(void) {
    struct { NVNwindow w; unsigned char canary[32]; } storage;
    memset(&storage, 0, sizeof storage); memset(storage.canary, 0xa5, sizeof storage.canary);
    win_t *w=(win_t *)&storage.w; w->ntex=3;
    NVNsync raw={0}; sync_t *s=(sync_t *)&raw; int idx=-1;
    latest=900;
    assert(h_nvnWindowAcquireTexture(&storage.w,&raw,&idx)==NVN_WINDOW_ACQUIRE_TEXTURE_RESULT_SUCCESS);
    assert(idx==0 && s->fenced && s->fence==0 && !waited_ticket);
    assert(h_nvnSyncWait(&raw,0)==NVN_SYNC_WAIT_RESULT_ALREADY_SIGNALED);
    latest=7; present_image(w,0); assert(w->render_fence[0]==7 && w->ticket[0]==1);
    latest=12; present_image(w,1); latest=18; present_image(w,2);
    latest=900;
    assert(h_nvnWindowAcquireTexture(&storage.w,&raw,&idx)==NVN_WINDOW_ACQUIRE_TEXTURE_RESULT_SUCCESS);
    assert(idx==1 && s->fence==12 && waited_ticket==2);
    completed=11; assert(h_nvnSyncWait(&raw,0)==NVN_SYNC_WAIT_RESULT_TIMEOUT_EXPIRED && waited_fence==12);
    completed=12; assert(h_nvnSyncWait(&raw,0)==NVN_SYNC_WAIT_RESULT_ALREADY_SIGNALED);
    h_nvnWindowAcquireTexture(&storage.w,&raw,&idx); assert(idx==2 && s->fence==18 && waited_ticket==3);
    h_nvnWindowAcquireTexture(&storage.w,&raw,&idx); assert(idx==0 && s->fence==7 && waited_ticket==1);
    h_nvnQueueFenceSync(NULL,&raw,NVN_SYNC_CONDITION_ALL_GPU_COMMANDS_COMPLETE,0);
    assert(s->fence==900 && h_nvnSyncWait(&raw,0)==NVN_SYNC_WAIT_RESULT_TIMEOUT_EXPIRED);
    completed=900; assert(h_nvnSyncWait(&raw,0)==NVN_SYNC_WAIT_RESULT_ALREADY_SIGNALED);
    h_nvnWindowAcquireTexture(&storage.w,NULL,&idx); assert(idx==1 && waited_ticket==2);
    for (unsigned i=0;i<sizeof storage.canary;i++) assert(storage.canary[i]==0xa5);
}
'''
        with tempfile.TemporaryDirectory(dir=ROOT / 'work/tmp') as temp:
            code, executable = Path(temp) / 'acquire.c', Path(temp) / 'acquire'
            code.write_text(source)
            # Upstream's present job initialiser leaves the trailing fields zero.
            subprocess.run(['clang', '-Wall', '-Wextra', '-Werror', '-Wno-missing-field-initializers', '-fsanitize=address,undefined',
                            '-ivfsoverlay', str(ROOT / 'work/build/graphics-overlay.json'),
                            '-I', str(CORE / 'gfx/nvn'), '-I', str(CORE / 'gfx/nvn/include'), '-I', str(ROOT / 'host'),
                            str(code), str(ROOT / 'host/timeline.c'), '-o', str(executable)], check=True)
            subprocess.run([str(executable)], check=True)

    def test_translation_stays_in_place_with_metal_fences(self):
        interface = adapt_interface((CORE / 'gfx/nvn/hle_nvn.c').read_text())
        start = interface[interface.index('static void x_start(void) {'):interface.index('static uint64_t x_push(')]
        # Partial submits commit several Metal fences per game submit; game fences must stay Metal fences.
        self.assertIn('g_x_sync = 1;', start)
        sync = interface[interface.index('NVN(nvnQueueFenceSync,'):interface.index('NVN(nvnSyncWait,')]
        self.assertIn('->fence = mtl_last_fence();', sync)
        self.assertNotIn('x_fence()', interface[interface.index('NVN(nvnWindowAcquireTexture,'):interface.index('// \u5e27\u8fb9\u754c：')])
        self.assertIn('static const tex_t *snap_tex(const NVNtexture *t) { return TX(t); }', interface)
        objects = adapt_objects((CORE / 'gfx/nvn/nvn_obj.h').read_text())
        texture = objects[objects.index('uint32_t magic; uint8_t has_layout'):objects.index('} tex_t;')]
        self.assertNotIn('snap', texture)

    def test_pacing_uses_verified_timing_and_retains_unpatched_fallback(self):
        interface = adapt_interface((CORE / 'gfx/nvn/hle_nvn.c').read_text())
        policy = interface[interface.index('static int g_hz ='):interface.index('int (*g_nvn_loadscreen_up)(void);')]
        source = '''#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define LOG(...) do { if (0) printf(__VA_ARGS__); } while (0)
typedef struct { int interval; } win_t;
static unsigned target;
static unsigned zelda_game_frame_rate(void) { return target; }
''' + policy + '''
int main(void) {
    unsetenv("DM_PRESENT_HZ"); unsetenv("DM_PRESENT_INTERVAL");
    win_t window = {.interval=2};
    assert(present_period(&window) == 33333333 && !g_display_mode);
    window.interval = 3; assert(present_period(&window) == 50000000);
    window.interval = 1; assert(present_period(&window) == 16666666);
    window.interval = 0; assert(present_period(&window) == 16666666);
    window.interval = 2;
    setenv("DM_PRESENT_INTERVAL", "display", 1); g_hz = -1;
    assert(present_period(&window) == 16666666 && g_display_mode);
    setenv("DM_PRESENT_INTERVAL", "game", 1); g_hz = -1;
    assert(present_period(&window) == 33333333 && !g_display_mode);
    setenv("DM_PRESENT_HZ", "0", 1); g_hz = -1;
    assert(!present_period(&window));
    unsetenv("DM_PRESENT_HZ"); g_hz = -1; target = 60;
    assert(present_period(&window) == 16666666 && !g_display_mode);
    window.interval = 3; assert(present_period(&window) == 16666666);
    target = 30; assert(present_period(&window) == 33333333);
    target = 90; assert(present_period(&window) == 11111111);
    target = 120; assert(present_period(&window) == 8333333);
    setenv("DM_PRESENT_INTERVAL", "display", 1); g_hz = -1;
    assert(present_period(&window) == 16666666 && g_display_mode);
}
'''
        with tempfile.TemporaryDirectory(dir=ROOT / 'work/tmp') as temp:
            code, executable = Path(temp) / 'pacing.c', Path(temp) / 'pacing'
            code.write_text(source)
            subprocess.run(['clang', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                            str(code), '-o', str(executable)], check=True)
            subprocess.run([str(executable)], check=True)

    def test_texture_swizzle_roundtrip_and_default(self):
        extra = (ROOT / 'host/nvn_extra.inc').read_text()
        implementation = extra[extra.index('static uint16_t texture_swizzle('):extra.index('// NVN\'s optional compression palette')]
        source = '''#include "nvn_obj.h"
#include <assert.h>
#include <stdlib.h>
#define NVN(name, ret, ...) static ret h_##name(__VA_ARGS__)
#define LOG(...) ((void)0)
''' + implementation + '''
int main(void) {
    tex_t texture = {0}; tex_builder_t builder = {0}; view_t view = {0};
    NVNtextureSwizzle result[] = {99, 99, 99, 99, 99, 99};
    h_nvnTextureGetSwizzle((const NVNtexture *)&texture, result + 1, result + 2, result + 3, result + 4);
    assert(result[0] == 99 && result[5] == 99);
    for (unsigned i = 1; i <= 4; i++) assert(result[i] == i + 1);
    h_nvnTextureBuilderSetSwizzle((NVNtextureBuilder *)&builder, 4, 3, 2, 1);
    h_nvnTextureViewSetSwizzle((NVNtextureView *)&view, 4, 3, 2, 1);
    assert(builder.swizzle == view.swizzle); texture.swizzle = builder.swizzle;
    h_nvnTextureGetSwizzle((const NVNtexture *)&texture, result + 1, result + 2, result + 3, result + 4);
    for (unsigned i = 1; i <= 4; i++) assert(result[i] == 5 - i);
    assert(result[0] == 99 && result[5] == 99);
}
'''
        with tempfile.TemporaryDirectory(dir=ROOT / 'work/tmp') as temp:
            directory = Path(temp)
            (directory / 'nvn_obj.h').write_text(adapt_objects((CORE / 'gfx/nvn/nvn_obj.h').read_text()))
            code, executable = directory / 'swizzle.c', directory / 'swizzle'
            code.write_text(source)
            subprocess.run(['clang', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                            '-I', str(CORE / 'gfx/nvn'), '-I', str(CORE / 'gfx/nvn/include'),
                            str(code), '-o', str(executable)], check=True)
            subprocess.run([str(executable)], check=True)

    def test_runtime_shader_compiler_checks_child_failure_and_external_temp(self):
        source = '''#import <Foundation/Foundation.h>
#include <stdlib.h>
#define MLOG(...) ((void)0)
#define MDIE(...) exit(78)
#include "nvn_compile_shaders.inc"
int main(void) { zelda_compile_shaders(); }
'''
        with tempfile.TemporaryDirectory(prefix='shader compiler ', dir=ROOT / 'work/tmp') as temp:
            directory = Path(temp)
            code, executable = directory / 'compiler.m', directory / 'compiler'
            code.write_text(source)
            subprocess.run(['clang', '-Wall', '-Wextra', '-Werror', '-fobjc-arc',
                            '-framework', 'Foundation', '-I', str(ROOT / 'host'),
                            str(code), '-o', str(executable)], check=True)
            for path in ('work/venv/bin', 'work/logs', 'work/tmp'):
                (directory / path).mkdir(parents=True)
            compiler = directory / 'work/venv/bin/python'
            compiler.write_text('''#!/bin/sh
test "$1" = script/compile_runtime_shaders.py || exit 5
test "$PYTHONDONTWRITEBYTECODE" = 1 || exit 6
test "$TMPDIR" -ef "$PWD/work/tmp" || exit 7
printf 'compiler output\\n'
''')
            compiler.chmod(0o700)
            subprocess.run([str(executable)], cwd=temp, check=True)
            self.assertEqual((directory / 'work/logs/runtime-shaders.log').read_text(), 'compiler output\n')
            compiler.write_text('#!/bin/sh\nexit 1\n')
            self.assertEqual(subprocess.run([str(executable)], cwd=temp).returncode, 78)
            compiler.unlink()
            self.assertEqual(subprocess.run([str(executable)], cwd=temp).returncode, 78)

    def test_completed_resources_drain_before_the_outer_pool_ends(self):
        backend = adapt_backend((CORE / 'gfx/nvn/nvn_mtl.m').read_text())
        drain = backend[backend.index('static void defer_drain(void) {'):].split(
            'static pthread_mutex_t g_bf_want_mu', 1)[0]
        source = '''#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <pthread.h>
#include <assert.h>
static unsigned destroyed;
@interface Resource : NSObject @end
@implementation Resource
- (BOOL)conformsToProtocol:(Protocol *)p { return p == @protocol(MTLResource) || [super conformsToProtocol:p]; }
- (void)dealloc { destroyed++; }
@end
@interface NvDeferredRelease : NSObject { @public id object; uint64_t fence; int keep_resident; } @end
@implementation NvDeferredRelease @end
static NSMutableArray *g_deferred; static NSHashTable *g_res_objects;
static pthread_mutex_t g_deferred_mu = PTHREAD_MUTEX_INITIALIZER, g_res_mu = PTHREAD_MUTEX_INITIALIZER;
static uint64_t g_done;
static void res_remove(id object) { [g_res_objects removeObject:object]; }
''' + drain + '''
int main(void) { @autoreleasepool {
    g_deferred = [NSMutableArray array]; g_res_objects = [NSHashTable weakObjectsHashTable];
    @autoreleasepool {
        NvDeferredRelease *d = [NvDeferredRelease new]; d->object = [Resource new]; d->fence = 1;
        [g_res_objects addObject:d->object]; [g_deferred addObject:d];
    }
    defer_drain(); assert(!destroyed && g_deferred.count == 1);
    g_done = 1; defer_drain(); assert(destroyed == 1 && !g_deferred.count);
} }
'''
        with tempfile.TemporaryDirectory(dir=ROOT / 'work/tmp') as temp:
            code, executable = Path(temp) / 'retirement.m', Path(temp) / 'retirement'
            code.write_text(source)
            subprocess.run(['clang', '-fobjc-arc', '-Wall', '-Wextra', '-Werror',
                            '-framework', 'Foundation', str(code), '-o', str(executable)], check=True)
            subprocess.run([str(executable)], check=True)

    def test_retired_texture_views_release_after_gpu_completion(self):
        backend = adapt_backend((CORE / 'gfx/nvn/nvn_mtl.m').read_text())
        self.assertIn('if (zelda_texture_view_current(v, cur)) { rp->view_ser = sg;', backend)
        self.assertIn('zelda_sampled_view_forget(&g_treg[id_]);', backend)
        self.assertIn('zelda_sampled_view_forget(rp);', backend)
        source = '''#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <pthread.h>
#include <assert.h>
@interface TestTexture : NSObject
@property(strong) id parentTexture;
@end
static unsigned destroyed;
@implementation TestTexture
- (void)dealloc { destroyed++; }
@end
@interface NvPkg : NSObject { @public id<MTLTexture> tex; } @end
@implementation NvPkg @end
typedef struct { struct { uint64_t serial; } t; void *view, *array_view; uint64_t view_rid, array_rid; } treg_t;
static treg_t g_treg[2]; static int g_ntreg = 2;
static pthread_mutex_t g_rmu = PTHREAD_MUTEX_INITIALIZER;
static id owners[2]; static NSMutableArray *pending;
static id serial_get(uint64_t serial) { return owners[serial]; }
static void defer_obj_unref(id object) { [pending addObject:object]; }
#include "nvn_texture_cache.inc"
int main(void) {
    @autoreleasepool {
    pending = [NSMutableArray array];
    @autoreleasepool {
        TestTexture *base = [TestTexture new], *child = [TestTexture new], *swizzled = [TestTexture new];
        child.parentTexture = base; swizzled.parentTexture = child;
        assert(zelda_texture_view_current((id)base, (id)base));
        assert(zelda_texture_view_current((id)child, (id)base));
        assert(zelda_texture_view_current((id)swizzled, (id)base));
        assert(!zelda_texture_view_current((id)base, (id)swizzled));
        assert(!zelda_texture_view_current((id)swizzled, nil));
        assert(!zelda_texture_view_current(nil, (id)base));
        NvPkg *package = [NvPkg new]; package->tex = (id)base; owners[0] = package;
        g_treg[0].view = (void *)CFBridgingRetain(swizzled); g_treg[0].view_rid = 42;
        TestTexture *array = [TestTexture new]; array.parentTexture = swizzled;
        g_treg[0].array_view = (void *)CFBridgingRetain(array); g_treg[0].array_rid = 43;
        zelda_trim_texture_views();
        assert(g_treg[0].view && g_treg[0].view_rid == 42 && g_treg[0].array_rid == 43 && !pending.count);
        package->tex = (id)[TestTexture new];
        assert(!zelda_texture_view_current((id)swizzled, package->tex));
    }
    @autoreleasepool {
        zelda_trim_texture_views();
        assert(!g_treg[0].view && !g_treg[0].view_rid && !g_treg[0].array_view && !g_treg[0].array_rid && pending.count == 2);
        assert(!destroyed); // In-flight work still owns the old view and both ancestors.
    }
    [pending removeAllObjects];
    }
    assert(destroyed == 4);
    @autoreleasepool {
        TestTexture *texture = [TestTexture new]; owners[1] = texture;
        g_treg[1] = (treg_t){.t.serial=1, .view=(void *)CFBridgingRetain(texture), .view_rid=7};
        zelda_trim_texture_views(); assert(g_treg[1].view && !pending.count);
        owners[1] = nil;
    }
    @autoreleasepool { zelda_trim_texture_views(); assert(!g_treg[1].view && destroyed == 4); }
    [pending removeAllObjects]; assert(destroyed == 5);
}
'''
        with tempfile.TemporaryDirectory(dir=ROOT / 'work/tmp') as temp:
            code, executable = Path(temp) / 'cache.m', Path(temp) / 'cache'
            code.write_text(source)
            subprocess.run(['clang', '-fobjc-arc', '-Wall', '-Wextra', '-Werror',
                            '-framework', 'Foundation', '-I', str(ROOT / 'host'),
                            str(code), '-o', str(executable)], check=True)
            subprocess.run([str(executable)], check=True)

    def test_timestamp_reports_do_not_enable_conditional_clears(self):
        backend = adapt_backend((CORE / 'gfx/nvn/nvn_mtl.m').read_text())
        report = backend.split('else { uint64_t t = gpu_ticks(), rec[2]', 1)[1].split(' }', 1)[0]
        objects = adapt_objects((CORE / 'gfx/nvn/nvn_obj.h').read_text())
        source = '''#include "nvn_obj.h"
#include <assert.h>
#include <string.h>
static uint64_t gpu_ticks(void) { static uint64_t time = 100; return ++time; }
static void report(uint32_t type, uint64_t *destination) {
    struct { uint32_t type; uint64_t addr; } ops[] = {{type, (uintptr_t)destination}};
    int i = 0;
    uint64_t t = gpu_ticks(), rec[2]''' + report + '''
}
int main(void) {
    uint64_t data[] = {11, 1, 2, 3, 4, 22};
    report(NVN_COUNTER_TYPE_TIMESTAMP_TOP, data + 1);
    report(NVN_COUNTER_TYPE_TIMESTAMP, data + 3);
    assert(data[0] == 11 && data[5] == 22);
    assert(data[1] == 0 && data[3] == 0 && data[2] > 0 && data[4] > data[2]);
    p_render_enable predicate = {.address = (uintptr_t)(data + 1), .mode = NVN_CONDITIONAL_RENDER_MODE_RENDER_IF_NOT_EQUAL};
    assert(!nvn_render_test(&predicate, data + 1));
    predicate.mode = NVN_CONDITIONAL_RENDER_MODE_RENDER_IF_EQUAL;
    assert(nvn_render_test(&predicate, data + 1));
}
'''
        with tempfile.TemporaryDirectory(dir=ROOT / 'work/tmp') as temp:
            directory = Path(temp)
            (directory / 'nvn_obj.h').write_text(objects)
            code, executable = directory / 'counter.c', directory / 'counter'
            code.write_text(source)
            subprocess.run(['clang', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                            '-I', str(CORE / 'gfx/nvn'), '-I', str(CORE / 'gfx/nvn/include'),
                            str(code), '-o', str(executable)], check=True)
            subprocess.run([str(executable)], check=True)

    def test_cpu_texture_upload_does_not_require_packaged_layout(self):
        backend = adapt_backend((CORE / 'gfx/nvn/nvn_mtl.m').read_text())
        decision = backend.split('int packaged = ', 1)[1].split(';', 1)[0]
        source = '''#include "nvn_obj.h"
#include <assert.h>
static int uploads(const tex_t *t) { return ''' + decision + '''; }
int main(void) {
    uint8_t pixels[16] = {1};
    pool_t pool = {.flags=NVN_MEMORY_POOL_FLAGS_CPU_CACHED | NVN_MEMORY_POOL_FLAGS_GPU_CACHED};
    tex_t texture = {.mem=pixels, .pool=&pool, .d={.target=NVN_TEXTURE_TARGET_2D}};
    assert(uploads(&texture));
    pool.flags = NVN_MEMORY_POOL_FLAGS_CPU_UNCACHED; assert(uploads(&texture));
    pool.flags = NVN_MEMORY_POOL_FLAGS_CPU_NO_ACCESS | NVN_MEMORY_POOL_FLAGS_VIRTUAL;
    assert(!uploads(&texture));
    texture.has_layout = 1; assert(uploads(&texture));
    texture.pool = NULL; assert(uploads(&texture));
    texture.has_layout = 0; assert(!uploads(&texture));
    texture.pool = &pool; pool.flags = NVN_MEMORY_POOL_FLAGS_CPU_CACHED;
    texture.d.target = NVN_TEXTURE_TARGET_BUFFER; assert(!uploads(&texture));
    texture.d.target = NVN_TEXTURE_TARGET_2D; texture.mem = NULL; assert(!uploads(&texture));
}
'''
        with tempfile.TemporaryDirectory(dir=ROOT / 'work/tmp') as temp:
            code, executable = Path(temp) / 'upload.c', Path(temp) / 'upload'
            code.write_text(source)
            subprocess.run(['clang', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                            '-I', str(CORE / 'gfx/nvn'), '-I', str(CORE / 'gfx/nvn/include'),
                            str(code), '-o', str(executable)], check=True)
            subprocess.run([str(executable)], check=True)

    def test_shader_prediction_returns_through_nested_command_lists(self):
        backend = adapt_backend((CORE / 'gfx/nvn/nvn_mtl.m').read_text())
        cursor = backend[backend.index('typedef struct { const uint8_t *p, *ret[8];'):].split('\n// \u53ef\u8bfb tile', 1)[0]
        prediction = backend.split('static void pso_predict_rest(void) {', 1)[1]
        start = prediction[prediction.index('fz_cursor cur ='):].split('    // \u5148\u6536\u9f50\u518d\u4e22：', 1)[0]
        source = '''#include "nvn_obj.h"
#include <assert.h>
#include <string.h>
static const uint8_t *g_exec_next, *g_ret[16];
static const uint64_t *g_rest;
static int g_ret_n, g_nrest, g_hoist_n;
static int hoist_skipped(const uint8_t *p) { (void)p; return 0; }
''' + cursor + '''
static fz_cursor scan;
static void start(void) {
''' + start + '''scan = cur; }
int main(void) {
    op_hdr end = {OP_END, 8};
    op_hdr parent[] = {{OP_DRAW_IDX, 8}, {OP_END, 8}};
    op_hdr root[] = {{OP_DRAW, 8}, {OP_END, 8}};
    op_hdr next[] = {{OP_DISPATCH, 8}, {OP_END, 8}};
    uint64_t rest[] = {(uint64_t)next};
    g_exec_next = (const uint8_t *)&end;
    g_ret[0] = (const uint8_t *)root; g_ret[1] = (const uint8_t *)parent; g_ret_n = 2;
    g_rest = rest; g_nrest = 1;
    start(); op_hdr result;
    assert(fz_next(&scan, NULL, &result, NULL) && result.op == OP_DRAW_IDX);
    assert(fz_next(&scan, NULL, &result, NULL) && result.op == OP_DRAW);
    assert(fz_next(&scan, NULL, &result, NULL) && result.op == OP_DISPATCH);
    assert(!fz_next(&scan, NULL, &result, NULL) && !scan.bad);
    assert(g_ret_n == 2 && g_ret[0] == (const uint8_t *)root);
    memset(&scan, 0, sizeof scan); g_ret_n = 9; start(); assert(!scan.p);
}
'''
        with tempfile.TemporaryDirectory(dir=ROOT / 'work/tmp') as temp:
            code, executable = Path(temp) / 'scan.c', Path(temp) / 'scan'
            code.write_text(source)
            subprocess.run(['clang', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                            '-I', str(CORE / 'gfx/nvn'), '-I', str(CORE / 'gfx/nvn/include'),
                            str(code), '-o', str(executable)], check=True)
            subprocess.run([str(executable)], check=True)

    def test_vertex_variant_uses_only_active_attributes_and_keeps_normalized_formats_float(self):
        backend = adapt_backend((CORE / 'gfx/nvn/nvn_mtl.m').read_text())
        formats = backend[backend.index('static MTLVertexFormat vfmt('):backend.index('static MTLBlendFactor bfac(')]
        variants = (ROOT / 'host/nvn_shader_variant.inc').read_text()
        source = '''#import <Metal/Metal.h>
#include <CommonCrypto/CommonDigest.h>
#include "nvn_obj.h"
#include "nvn_refl.h"
#include <assert.h>
#define MLOG(...) ((void)0)
#define MDIE(...) do { fprintf(stderr, __VA_ARGS__); abort(); } while (0)
typedef struct { nvn_refl rf; char key[32]; id<MTLFunction> fn; NSUInteger arg_len; int z_layered, z_cube_uniform, z_wide_line; } NvShader;
static NSString *g_msl_dir = @"missing-libraries";
static NvShader *shader_get(NSString *name, int stage) { (void)name; (void)stage; assert(!"unexpected library load"); return NULL; }
static void zelda_compile_shaders(void) {}
''' + formats + variants + '''
static void pso_predict_rest(void) {
    assert(z_variant_missing && !z_variant_collect);
    NvShader next = {.key = "fedcba9876543210_b"};
    z_variant_collect = 1;
    assert(!zelda_shader_variant(&next, 2, 0, NULL));
    z_variant_collect = 0;
}
int main(int argc, char **argv) {
    (void)argv;
    (void)zelda_uniform_linked_source;
    for (unsigned f = 1; f <= 0x30; f++) {
        unsigned type = (f - 1) % 12;
        unsigned expected = type == 2 || type == 7 || type == 10 ? 1 : type == 3 || type == 8 || type == 11 ? 2 : 0;
        assert(zelda_vertex_class(vfmt(f)) == expected);
    }
    assert(!zelda_vertex_class(vfmt(0x3d)) && !zelda_vertex_class(vfmt(0x69)) && !zelda_vertex_class(vfmt(0x9a)));
    for (unsigned f = 0x51; f <= 0x68; f++) {   // 8/16-bit integer-to-float formats are mapped; 32-bit ones stay unmapped.
        int wide = (f - 0x51) % 6 >= 4;
        assert(zelda_vertex_class(vfmt(f)) == (wide ? 0u : 2 - (f & 1)) && (vfmt(f) == MTLVertexFormatInvalid) == wide);
        assert(!zelda_vertex_input_class(f));
    }
    NvShader shader = {.key = "0123456789abcdef_b", .fn = (id)[NSObject new],
        .rf = {.nv = 2, .v = {{.nvn = 2, .loc = 0}, {.nvn = 3, .loc = 15}}}};
    va_st attributes[16] = {[0] = {.fmt = 0x30}, [2] = {.fmt = 0x2f}, [3] = {.fmt = 0x30}};
    if (argc > 1) { zelda_vertex_function(&shader, attributes, NULL); return 1; }
    z_variant_collect = 1;
    assert(!zelda_vertex_function(&shader, attributes, NULL) && z_variant_missing);
    NSData *data = [NSData dataWithContentsOfFile:@"work/shaders/variants/0123456789abcdef-80000001.json"];
    NSDictionary *request = [NSJSONSerialization JSONObjectWithData:data options:0 error:nil];
    assert([request[@"base"] isEqual:@"0123456789abcdef"] && [request[@"mask"] unsignedIntValue] == 0x80000001u);
    uint32_t targets[8] = {[0] = MTLPixelFormatRGBA8Uint, [7] = MTLPixelFormatRGBA32Sint};
    assert(!zelda_fragment_function(NULL, targets));
    assert(!zelda_fragment_function(&shader, targets));
    data = [NSData dataWithContentsOfFile:@"work/shaders/variants/0123456789abcdef-8001.json"];
    request = [NSJSONSerialization JSONObjectWithData:data options:0 error:nil];
    assert([request[@"mask"] unsignedIntValue] == 0x8001u);
    attributes[2].fmt = 0x2a; attributes[3].fmt = 0;
    assert(zelda_vertex_function(&shader, attributes, NULL) == shader.fn);
    attributes[2].fmt = 0x64; attributes[3].fmt = 0x53;
    assert(zelda_vertex_function(&shader, attributes, NULL) == shader.fn);
    attributes[2].fmt = 0x2a; attributes[3].fmt = 0;
    memset(targets, 0, sizeof targets); assert(zelda_fragment_function(&shader, targets) == shader.fn);
    NvShader fragment = {.key = "fedcba9876543210_b"};
    assert(!zelda_vertex_function(&shader, attributes, &fragment));
    // The startup warm-up skips a missing variant without capturing or compiling it.
    z_warming = 1; attributes[2].fmt = 0x2f;
    assert(!zelda_vertex_function(&shader, attributes, NULL));
    assert(![NSFileManager.defaultManager fileExistsAtPath:@"work/shaders/variants/0123456789abcdef-00000001.json"]);
    z_warming = 0; attributes[2].fmt = 0x2a;
    shader.z_wide_line = 1;
    assert(zelda_vertex_function(&shader, attributes, &fragment) == shader.fn);

}
'''
        with tempfile.TemporaryDirectory(dir=ROOT / 'work/tmp') as temp:
            code, executable = Path(temp) / 'vertex.m', Path(temp) / 'vertex'
            code.write_text(source)
            subprocess.run(['clang', '-Wall', '-Wextra', '-Werror', '-fobjc-arc', '-fsanitize=undefined',
                            '-I', str(CORE / 'gfx/nvn'), '-I', str(CORE / 'gfx/nvn/include'),
                            str(code), '-framework', 'Foundation', '-framework', 'Metal', '-o', str(executable)], check=True)
            subprocess.run([str(executable)], cwd=temp, check=True)
            from build_shaders import shader_entries
            import json
            directory = Path(temp) / 'work/shaders'
            (directory / 'index.json').write_text(json.dumps([
                {'key': '0123456789abcdef', 'stage': 'vs'},
                {'key': 'fedcba9876543210', 'stage': 'ps'}]))
            # Only the linked request is needed for this cross-language identity check.
            requests = directory / 'variants'
            (requests / '0123456789abcdef-8001.json').unlink()
            linked = [e for e in shader_entries(directory) if 'next' in e]
            self.assertEqual(len(linked), 1)
            self.assertEqual(linked[0]['next']['key'], 'fedcba9876543210')
            self.assertEqual(linked[0]['attr'], {})
            remaining = Path(temp) / 'work/shaders/variants/fedcba9876543210-00000002.json'
            self.assertFalse(remaining.exists())
            self.assertEqual(subprocess.run([str(executable), 'draw'], cwd=temp).returncode, 78)
            self.assertTrue(remaining.is_file())

    def test_integer_to_float_vertex_formats_reach_float_inputs_on_gpu(self):
        backend = adapt_backend((CORE / 'gfx/nvn/nvn_mtl.m').read_text())
        formats = backend[backend.index('static MTLVertexFormat vfmt('):backend.index('static MTLBlendFactor bfac(')]
        source = '#import <Metal/Metal.h>\n#include <assert.h>\n#include <string.h>\n' + formats + r'''
// Each mapped 8/16-bit *_UI2F / *_I2F format, read by a float4 shader input, must give the integer values with missing
// components (0, 0, 1). Metal rejects 32-bit integers read as float, so those formats must stay unmapped.
int main(void) { @autoreleasepool {
    id<MTLDevice> device = MTLCreateSystemDefaultDevice(); assert(device);
    NSError *error = nil;
    id<MTLLibrary> library = [device newLibraryWithSource:@"#include <metal_stdlib>\nusing namespace metal;\n"
        "struct V { float4 a [[attribute(0)]]; };\n"
        "vertex void fetch(V v [[stage_in]], device float4 *out [[buffer(1)]], uint id [[vertex_id]]) { out[id] = v.a; }\n" options:nil error:&error];
    assert(library);
    const uint8_t bytes[16] = {0xfd, 0x05, 0x7f, 0x80, 0x01, 0xff, 0x00, 0x90, 0x12, 0x34, 0x56, 0xf8, 0x9a, 0xbc, 0xde, 0x7f};
    id<MTLBuffer> input = [device newBufferWithBytes:bytes length:sizeof bytes options:MTLResourceStorageModeShared];
    id<MTLBuffer> output = [device newBufferWithLength:16 options:MTLResourceStorageModeShared];
    id<MTLCommandQueue> queue = [device newCommandQueue];
    for (unsigned f = 0x51; f <= 0x68; f++) {
        unsigned type = (f - 0x51) % 6, components = (f - 0x51) / 6 + 1, size = 1u << (type / 2), is_signed = type & 1;
        if (size == 4) { assert(vfmt((int)f) == MTLVertexFormatInvalid); continue; }
        MTLRenderPipelineDescriptor *pd = [MTLRenderPipelineDescriptor new];
        pd.vertexFunction = [library newFunctionWithName:@"fetch"]; pd.rasterizationEnabled = NO;
        MTLVertexDescriptor *vd = [MTLVertexDescriptor vertexDescriptor];
        vd.attributes[0].format = vfmt((int)f); vd.attributes[0].bufferIndex = 0; vd.layouts[0].stride = 16;
        pd.vertexDescriptor = vd;
        id<MTLRenderPipelineState> pipeline = [device newRenderPipelineStateWithDescriptor:pd error:&error];
        if (!pipeline) { fprintf(stderr, "format %#x: %s\n", f, error.localizedDescription.UTF8String); abort(); }
        memset(output.contents, 0xff, 16);
        id<MTLCommandBuffer> command = [queue commandBuffer];
        MTLRenderPassDescriptor *pass = [MTLRenderPassDescriptor new];
        pass.renderTargetWidth = pass.renderTargetHeight = 1; pass.defaultRasterSampleCount = 1;
        id<MTLRenderCommandEncoder> encoder = [command renderCommandEncoderWithDescriptor:pass];
        [encoder setRenderPipelineState:pipeline];
        [encoder setVertexBuffer:input offset:0 atIndex:0]; [encoder setVertexBuffer:output offset:0 atIndex:1];
        [encoder drawPrimitives:MTLPrimitiveTypePoint vertexStart:0 vertexCount:1];
        [encoder endEncoding]; [command commit]; [command waitUntilCompleted];
        assert(command.status == MTLCommandBufferStatusCompleted);
        float expected[4] = {0, 0, 0, 1};
        for (unsigned c = 0; c < components; c++) {
            uint32_t raw = 0; memcpy(&raw, bytes + c * size, size);
            int32_t value = (int32_t)raw;
            if (is_signed && raw >> (8 * size - 1)) value -= 1 << (8 * size);
            expected[c] = (float)value;
        }
        assert(!memcmp(output.contents, expected, sizeof expected));
    }
} }
'''
        with tempfile.TemporaryDirectory(dir=ROOT / 'work/tmp') as temp:
            code, executable = Path(temp) / 'scaled.m', Path(temp) / 'scaled'
            code.write_text(source)
            subprocess.run(['clang', '-Wall', '-Wextra', '-Werror', '-fobjc-arc', str(code),
                            '-framework', 'Foundation', '-framework', 'Metal', '-o', str(executable)], check=True)
            subprocess.run([str(executable)], check=True, env={**os.environ, 'MTL_DEBUG_LAYER': '1'})

    def test_constant_updates_between_passes_wait_for_the_next_pass(self):
        backend = adapt_backend((CORE / 'gfx/nvn/nvn_mtl.m').read_text())
        writes = (ROOT / 'host/nvn_pass_writes.inc').read_text()
        loop = backend[backend.index('void mtl_exec('):]
        # Pending updates are copied before any command not proven unable to observe them, ahead of other hooks.
        flush = loop.index('if (z_write_n && !zelda_write_passthrough(h.op, a)) zelda_write_flush();')
        self.assertLess(flush, loop.index('if (z_color_clear.count && !zelda_color_clear_passthrough(h.op, a)) rp_flush();'))
        self.assertIn('if (!zelda_write_defer((uint64_t)((const uint8_t *)a + 16), d, n)) mtl_sub_copy(', loop)
        # The next pass writes them first, before its own clears and draws; commits copy what is left.
        begin = backend[backend.index('g_enc = enc_r_begin(pd);'):]
        self.assertLess(begin.index('zelda_write_encode();'), begin.index('zelda_depth_clear_encode();'))
        self.assertIn('zelda_uniform_flush(); zelda_write_flush(); zelda_query_flush();', backend)
        submit = (ROOT / 'host/nvn_submit.inc').read_text()
        self.assertIn('z_write_keep = 1; mtl_sub_commit(); z_write_keep = 0; zelda_write_carry();', submit)
        # Only commands that touch no buffer memory before the pass, or whose ranges were checked, pass through.
        passthrough = writes[writes.index('static int zelda_write_passthrough'):writes.index('static void zelda_write_encode')]
        for op in ('OP_COPY_B2B', 'OP_COPY_T2B', 'OP_DISPATCH', 'OP_SIGNAL_EVENT', 'OP_CLEAR_TEX', 'OP_COPY_B2T', 'OP_COPY_T2T'):
            self.assertNotIn(op, passthrough)
        self.assertIn('return !z_render.address;', passthrough)
        self.assertIn('zelda_uniform_overlaps(destination, bytes)', writes)
        self.assertIn('barrierAfterEncoderStages:MTLStageVertex beforeEncoderStages:(MTLStageVertex | MTLStageFragment)', writes)

    def test_metalfx_upscale_runs_before_the_present_draw_only_on_metal_4(self):
        backend = adapt_backend((CORE / 'gfx/nvn/nvn_mtl.m').read_text())
        present = backend[backend.index('static int present_encode_m4(CBUF cb, id<MTLTexture> src0'):]
        hook = present.index('zelda_present_upscale(cb, src, cx, cy, cw, ch,')
        self.assertLess(present.index('#if !DM_NVN_M3', present.index('int full = ')), hook)
        self.assertLess(hook, present.index('RPD *rp = [RPD new];'))
        scale = (ROOT / 'host/nvn_present_scale.inc').read_text()
        # The scaler waits on a fence that an encoder behind a queue barrier updates; outputs retire after presents.
        self.assertIn('barrierAfterQueueStages:MTLStageAll', scale)
        self.assertIn('z_scaler.fence = z_scale_fence', scale)
        self.assertIn('z_scale_frames >= z_scale_retired[i].frame + 4', scale)
        # The 4K tier fits 3840x2160 to the image, only when the drawable is larger than the game image.
        self.assertIn('if (mode == 2 && out_w > cw && out_h > ch)', scale)
        self.assertIn('fmin(3840.0 / cw, 2160.0 / ch)', scale)

    def test_shader_warm_runs_at_the_first_submission_and_records_only_canonical_pipelines(self):
        backend = adapt_backend((CORE / 'gfx/nvn/nvn_mtl.m').read_text())
        begin = backend[backend.index('void mtl_sub_begin(void) {'):]
        self.assertLess(begin.index('zelda_shader_warm();'), begin.index('if (g_cb) MDIE('))
        self.assertLess(backend.index('#include "nvn_shader_warm.inc"'), backend.index('static void warm_claim(void) {'))
        # Variants reuse their base key, so only pipelines of the cached shader objects are recorded.
        self.assertIn('if (zelda_warm_canonical(vs, fs)) pso_user_record("R\\t%s\\n", shex);', backend)
        self.assertIn('pso_user_record("F\\t%s\\t%s\\n", shex, fs->z_fetch_identity.UTF8String);', backend)
        variant = (ROOT / 'host/nvn_shader_variant.inc').read_text()
        missing = variant.index('if (![NSFileManager.defaultManager fileExistsAtPath:library]) {')
        self.assertLess(missing, variant.index('if (z_warming) return nil;'))
        self.assertLess(variant.index('if (z_warming) return nil;'), variant.index('Captured Zelda'))
        warm = (ROOT / 'host/nvn_shader_warm.inc').read_text()
        # Workers only load shaders and compile; descriptors and cache inserts stay on the submit thread.
        self.assertIn('loaded[i] = shader_load(keys[i], stages[i].intValue);', warm)
        self.assertIn('MTLRenderPipelineDescriptor *d = pso_desc(&k, vs, fs, nil, 0);', warm)
        self.assertIn('if (sk.merged || sk.fz) continue;', warm)
        self.assertLess(warm.index('fs = zelda_fetch_shader('), warm.index('base.fs = (__bridge void *)fs;'))
        self.assertIn('!zelda_fetch_record_mapping(fetch)', warm)
        self.assertIn('!zelda_fetch_record_key(c[1])', warm)
        self.assertIn('variants.allValues', warm)
        self.assertLess(warm.index('if (done++) return;'), warm.index('@autoreleasepool {'))
        self.assertNotIn('g_shcache[', warm[warm.index('dispatch_apply(count'):warm.index('unsigned shaders = 0;')])
        launcher = (ROOT / 'script/build_and_run.sh').read_text()
        self.assertEqual(launcher.count('DM_PSO_USER=${DM_PSO_USER:-"$PWD/work/shaders/pso_user.tsv"}'), 2)

    def test_partial_submit_preserves_dependencies_and_query_slots(self):
        source = r'''#include <stdint.h>
#include <stdlib.h>
#include <stdatomic.h>
#include <assert.h>
#define MAX(a,b) ((a)>(b)?(a):(b))
static _Atomic uint64_t g_cur_frame;
static uint64_t g_stat_pass;
#include <pthread.h>
static int g_npend, g_pend_over, g_vis_ring_i = 2, deferred, commits, begins, dirty;
static pthread_mutex_t g_mu = PTHREAD_MUTEX_INITIALIZER;
static uint64_t g_done, g_fence;
static uint32_t g_vis_n = 41;
static int vis_buffer, *g_vis = &vis_buffer;
#define nil NULL
static struct { int keep, ring; uint32_t used; } z_slice_vis;
static int *z_slice_vis_buffer;
static int tbdr_on(void) { return deferred; }
static void mtl_sub_commit(void) { commits++; g_fence++; g_vis = NULL; g_vis_n = 0; g_vis_ring_i = -1; }
static void mtl_sub_begin(void) {
    // A query reset before the slice is reported after it, so its slot buffer and cursor must carry over.
    assert(z_slice_vis.keep && z_slice_vis_buffer == &vis_buffer && z_slice_vis.ring == 2 && z_slice_vis.used == 41);
    begins++; g_npend = g_pend_over = 0; g_vis = z_slice_vis_buffer; g_vis_n = z_slice_vis.used; g_vis_ring_i = z_slice_vis.ring;
}
static void args_dirty_all(void) { dirty++; }
static void *g_enc;
static struct { int clear_d, clear_s, clear_c[8]; } R;
static struct { int pending; } z_depth_clear;
static struct { int count; } z_color_clear;
static int z_write_keep;
static void zelda_write_carry(void) {}
#include "timeline.h"
#include "nvn_submit.inc"
int main(void) {
    setenv("ZELDA_SUBMIT_PASSES", "64", 1); setenv("ZELDA_SUBMIT_FIRST", "64", 1); setenv("ZELDA_SUBMIT_SLICES", "3", 1);
    setenv("ZELDA_SUBMIT_SECOND", "16", 1);
    zelda_submit_slice(); g_stat_pass = 63; zelda_submit_slice(); assert(!commits);
    g_stat_pass = 64; deferred = 1; zelda_submit_slice(); assert(!commits);
    deferred = 0; g_npend = 7; g_pend_over = 1;
    zelda_submit_slice(); assert(commits == 1 && begins == 1 && dirty == 1);
    assert(g_npend == 7 && g_pend_over == 1 && !z_slice_vis.keep && !z_slice_vis_buffer && g_vis == &vis_buffer);
    g_stat_pass = 65; zelda_submit_slice(); assert(commits == 1);
    g_stat_pass = 79; zelda_submit_slice(); assert(commits == 1);
    g_stat_pass = 80; zelda_submit_slice(); assert(commits == 2); // Only the second batch is smaller.
    g_stat_pass = 143; zelda_submit_slice(); assert(commits == 2);
    g_stat_pass = 144; zelda_submit_slice(); assert(commits == 3);
    g_stat_pass = 256; zelda_submit_slice(); assert(commits == 3);
    g_cur_frame++; zelda_submit_slice(); assert(commits == 3);
    g_stat_pass += 64; zelda_submit_slice(); assert(commits == 4);
    // Once the GPU has drained every committed slice, the next barrier with new passes submits early.
    g_stat_pass += 1; zelda_submit_slice(); assert(commits == 4);
    g_done = g_fence; zelda_submit_slice(); assert(commits == 5);
    zelda_submit_slice(); assert(commits == 5); // No new pass since that slice.
    g_stat_pass += 1; g_done = g_fence; zelda_submit_slice(); assert(commits == 6);
    g_stat_pass += 1; g_done = g_fence; zelda_submit_slice(); assert(commits == 6); // Frame limit.
    g_cur_frame++; g_stat_pass += 1; zelda_submit_slice(); assert(commits == 6); // Idle does not shortcut the first slice.
    // Between passes, a recorded clear or open encoder must stay with the current slice.
    g_stat_pass += 64; R.clear_c[7] = 1; zelda_slice_between_passes(); assert(commits == 6);
    R.clear_c[7] = 0; z_depth_clear.pending = 1; zelda_slice_between_passes(); assert(commits == 6);
    z_depth_clear.pending = 0; z_color_clear.count = 1; zelda_slice_between_passes(); assert(commits == 6);
    z_color_clear.count = 0; R.clear_s = 1; zelda_slice_between_passes(); assert(commits == 6);
    R.clear_s = 0; g_enc = &vis_buffer; zelda_slice_between_passes(); assert(commits == 6);
    g_enc = NULL; zelda_slice_between_passes(); assert(commits == 7);
}
'''
        with tempfile.TemporaryDirectory(dir=ROOT / 'work/tmp') as temp:
            code, executable = Path(temp) / 'submit.c', Path(temp) / 'submit'
            code.write_text(source)
            subprocess.run(['clang', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                            '-I', str(ROOT / 'host'), str(code), str(ROOT / 'host/timeline.c'), '-o', str(executable)], check=True)
            subprocess.run([str(executable)], check=True)

    def test_submit_slices_reuse_the_open_visibility_buffer(self):
        backend = adapt_backend((CORE / 'gfx/nvn/nvn_mtl.m').read_text())
        begin = backend[backend.index('void mtl_sub_begin(void) {'):backend.index('uint64_t mtl_sub_commit(void) {')]
        keep, pick = begin.index('if (z_slice_vis.keep) {'), begin.index('int pick = -1;')
        # The ring pick and its possible wait for an older submission happen only outside a slice continuation.
        self.assertLess(keep, pick)
        self.assertIn('g_vis = z_slice_vis_buffer; g_vis_n = z_slice_vis.used;', begin[keep:pick])
        self.assertIn('g_vis = g_vis_ring[pick].buf;\n    }', begin)
        self.assertIn('g_vis_ring[g_vis_ring_i].fence = UINT64_MAX', begin[keep:pick])
        execute = backend[backend.index('void mtl_exec('):]
        set_rt = execute[execute.index('case OP_SET_RT: {'):execute.index('case OP_CLEAR_COLOR: {')]
        # Slicing only follows the pass flush, before the next target becomes current.
        self.assertIn('rp_flush(); zelda_submit_slice(); R.rt = *r;', set_rt)

    def test_attachment_reads_and_dispatches_stay_in_open_encoders(self):
        backend = adapt_backend((CORE / 'gfx/nvn/nvn_mtl.m').read_text())
        draw = backend[backend.index('frz_plain:;'):backend.index('uint64_t z_index_gpu =', backend.index('frz_plain:;'))]
        # The framebuffer-fetch variant is chosen before the feedback split, which then ignores what it reads in place.
        fetch = draw.index('pr = zelda_fetch_prepare(pr, z_pass_written, &z_fetched);')
        split = draw.index('draw_samples_rt_mask(pr, z_pass_written & ~z_fetched,')
        self.assertLess(fetch, split)
        self.assertIn('NSMutableDictionary *z_fetch;', backend[backend.index('@interface NvProg'):])
        dispatch = backend[backend.index('static void dispatch(const p_dispatch *dp) {'):backend.index('static void copy_t2b(')]
        self.assertEqual(dispatch.count('zelda_dispatch_encoder()'), 2)
        self.assertEqual(dispatch.count('zelda_dispatch_end();'), 2)
        self.assertNotIn('pg_compute()', dispatch)
        self.assertNotIn('enc_c_end(ce)', dispatch)
        self.assertIn('#include "nvn_dispatch.inc"\nstatic void dispatch(', backend)

    def test_index_reuse_checks_shader_writes_before_dispatch(self):
        backend = adapt_backend((CORE / 'gfx/nvn/nvn_mtl.m').read_text())
        dispatch = backend[backend.index('static void dispatch(const p_dispatch *dp) {'):backend.index('static void copy_t2b(')]
        self.assertLess(dispatch.index('zelda_buffer_shader_written(&cs->rf, 5);'),
                        dispatch.index('zelda_dispatch_encoder()'))
        self.assertIn('if (cs->builtin) zelda_buffer_forget();', dispatch)
        self.assertIn('zelda_buffer_shader_written(&pr->sh[stage]->rf, pr->sh[stage]->stage);', backend)
        self.assertIn('#include "nvn_buffer_writes.inc"', backend)

    def test_conditional_clears_and_counter_writes_wait_for_readers(self):
        backend = adapt_backend((CORE / 'gfx/nvn/nvn_mtl.m').read_text())
        execute = backend[backend.index('void mtl_exec('):]
        loop = execute[execute.index('const void *a = p + 8;'):execute.index('switch (h.op) {', execute.index('const void *a = p + 8;'))]
        # Pending clears are drawn before anything that could observe them, ahead of the other deferred writes.
        self.assertLess(loop.index('zelda_color_clear_passthrough(h.op, a)) rp_flush();'), loop.index('zelda_inline_flush();'))
        self.assertIn('if (z_query_first >= 0 && !zelda_query_passthrough(h.op, a)) zelda_query_flush();', loop)
        self.assertNotIn('h.op == OP_BARRIER || h.op == OP_DISPATCH', loop)
        draw = backend[backend.index('frz_plain:;'):backend.index('uint64_t z_index_gpu =', backend.index('frz_plain:;'))]
        self.assertLess(draw.index('zelda_query_prepare_draw(pr, di);'), draw.index('zelda_uniform_prepare_draw(pr, di);'))
        self.assertLess(draw.index('draw_samples_rt_mask(pr, zelda_color_clear_targets(), 0)) rp_flush();'),
                        draw.index('zelda_fetch_prepare('))
        begin = backend[backend.index('static RENC rp_begin_ex(int clear_only) {'):backend.index('// \u5f00\u901a\u9053\u524d\u7684\u7a7a\u526a\u88c1\u9884\u68c0\u67e5')]
        self.assertLess(begin.index('zelda_color_clear_unfit(pd);'), begin.index('g_enc = enc_r_begin(pd);'))
        self.assertLess(begin.index('zelda_depth_clear_encode();'), begin.index('zelda_color_clear_encode();'))
        self.assertIn('R.clear_c[i] || (zelda_color_clear_targets() >> i & 1)', begin)
        flush = backend[backend.index('static void rp_flush(void) {'):]
        self.assertIn('z_depth_clear.pending || z_color_clear.count;', flush[:200])
        self.assertIn('#include "nvn_query_gpu.inc"\n#include "nvn_color_clear.inc"\n', backend)
        self.assertIn('#include "nvn_framebuffer_fetch.inc"\n#include "nvn_sample_range.inc"', backend)
        self.assertIn('!zelda_sample_mip_disjoint(sh, b, tid, h, targets[j], views[j], has_view[j])', backend)

    def test_clear_and_query_reset_preserve_pass_boundaries(self):
        backend = adapt_backend((CORE / 'gfx/nvn/nvn_mtl.m').read_text())
        execute = backend[backend.index('void mtl_exec('):]
        case = execute[execute.index('case OP_CLEAR_COLOR:'):execute.index('case OP_CLEAR_DS:')]
        reset = execute[execute.index('case OP_RESET_COUNTER:'):execute.index('case OP_COPY_B2B:')]
        barrier = execute[execute.index('case OP_BARRIER:'):execute.index('case OP_SET_TEX_POOL:')]
        source = '''#include "nvn_obj.h"
#include <assert.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/wait.h>
#define PD(...) ((void)0)
#define RED_ONCE(...) abort()
#define MDIE(...) exit(78)
#define nil 0
typedef struct { double red, green, blue, alpha; } Clear;
#define MTLClearColorMake(r,g,b,a) ((Clear){r,g,b,a})
static struct { p_set_rt rt; int clear_c[8]; Clear cc[8]; } R;
static int g_enc, g_rt_merged, full = 1, enabled = 1, endings, partials;
static void rp_end(void) { g_enc = 0; endings++; }
static int between;
// A slice point comes after the pass ends and before this clear becomes pending for the next pass.
static void zelda_slice_between_passes(void) { assert(!g_enc && !R.clear_c[0]); between++; }
static int zelda_render_enabled(void) { return enabled; }
static int zelda_gpu_conditional_clear(const void *clear) { (void)clear; return 0; }
static int scissor_full(const void *t, const void *v, int has) { (void)t; (void)v; (void)has; return full; }
static void zelda_partial_clear(const p_clear_color *c) { (void)c; partials++; }
static void clear(const p_clear_color *a) { switch (OP_CLEAR_COLOR) {
''' + case + '''} }
static int g_enc_has_vis, g_vis_on, g_vis_counting, cleared;
static uint64_t g_cnt_reset[32];
static void vis_clear(void) { cleared++; }
static void reset_query(uint64_t type) { const void *a = &type; switch (OP_RESET_COUNTER) {
''' + reset + '''} }
static int g_blit, blit_barriers;
static void zelda_submit_slice(void) {}
static void rp_flush(void) { if (g_enc) rp_end(); }
static void zelda_blit_barrier(void) { assert(g_blit); blit_barriers++; }
static void memory_barrier(void) { switch (OP_BARRIER) {
''' + barrier + '''} }
int main(void) {
    tex_t texture = {0}; R.rt.n = 2; R.rt.color[0] = R.rt.color[1] = &texture;
    p_clear_color c = {.index = 0, .rgba = {1, 2, 3, 4}, .mask = 15};
    g_enc = 1; enabled = 0; clear(&c);
    assert(g_enc && !endings && !R.clear_c[0]);
    enabled = 1; clear(&c);
    assert(!g_enc && endings == 1 && between == 1 && R.clear_c[0] && R.cc[0].green == 2);
    c.index = 1; clear(&c); assert(endings == 1 && between == 1 && R.clear_c[0] && R.clear_c[1]);
    full = 0; clear(&c); assert(partials == 1);
    c.mask = 0; clear(&c); assert(partials == 1);
    g_enc = 1; reset_query(NVN_COUNTER_TYPE_TIMESTAMP);
    assert(g_enc && endings == 1 && !cleared && !g_vis_counting);
    reset_query(NVN_COUNTER_TYPE_SAMPLES_PASSED);
    assert(!g_enc && endings == 2 && cleared == 1 && g_vis_on && g_vis_counting);
    g_enc = g_enc_has_vis = 1; reset_query(NVN_COUNTER_TYPE_SAMPLES_PASSED);
    assert(g_enc && endings == 2 && cleared == 2);
    pid_t child = fork(); assert(child >= 0);
    if (!child) { c.mask = 7; clear(&c); exit(1); }
    int status; assert(waitpid(child, &status, 0) == child && WIFEXITED(status) && WEXITSTATUS(status) == 78);
    g_enc = g_blit = 1; memory_barrier();
    assert(!g_enc && g_blit && endings == 3 && blit_barriers == 1);
}
'''
        with tempfile.TemporaryDirectory(dir=ROOT / 'work/tmp') as temp:
            directory = Path(temp)
            (directory / 'nvn_obj.h').write_text(adapt_objects((CORE / 'gfx/nvn/nvn_obj.h').read_text()))
            code, executable = directory / 'clear.c', directory / 'clear'
            code.write_text(source)
            subprocess.run(['clang', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                            '-I', str(CORE / 'gfx/nvn'), '-I', str(CORE / 'gfx/nvn/include'),
                            str(code), '-o', str(executable)], check=True)
            subprocess.run([str(executable)], check=True)

    def test_command_overflow_preserves_callbacks_and_persistent_records(self):
        source = adapt_interface((CORE / 'gfx/nvn/hle_nvn.c').read_text())
        recorder = source[source.index('#define CB(c)'):source.index('NVN(nvnCommandBufferCopyCommands,')]
        extra = (ROOT / 'host/nvn_extra.inc').read_text()
        recorder += source[source.index('static void *rec('):source.index('static void copy_view(')]
        recorder += extra[extra.index('NVN(nvnCommandBufferGetCommandMemoryUsed,'):extra.index('NVN(nvnCommandBufferDrawElements,')]
        recorder += source[source.index('NVN(nvnCommandBufferDrawArrays,'):source.index('NVN(nvnCommandBufferDrawElementsInstanced,')]
        recorder += (ROOT / 'host/nvn_deferred.inc').read_text()
        recorder += (ROOT / 'host/nvn_conditional.inc').read_text()
        backend = adapt_backend((CORE / 'gfx/nvn/nvn_mtl.m').read_text())
        submission = backend.split('} else if (dw) {', 1)[1].split('} else {', 1)[0]
        submission = submission.replace('if (z_conditional_gpu) [(id<MTL4RenderCommandEncoder>)en drawPrimitives:mp indirectBuffer:z_conditional_gpu];',
                                        'if (z_conditional_gpu) abort();')
        dispatch = backend[backend.index('static void dispatch(const p_dispatch *dp) {'):].split('    zskip_clear();', 1)[0]
        prefix = '''#include "nvn_obj.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <pthread.h>
#include <stddef.h>
#define NVN(name, ret, ...) static ret h_##name(__VA_ARGS__)
#define LOG(...) ((void)0)
#define die(...) abort()
#define MDIE(...) abort()
static uint64_t frame;
uint64_t dm_nvn_frame_count(void) { return frame; }
'''
        prefix += '''static unsigned draw_calls;
static uint64_t z_conditional_gpu;
static p_draw submitted;
static void enc_r_draw_instanced(int en, int prim, int first, int count, int instances, int base) {
    (void)en; draw_calls++; submitted = (p_draw){prim, first, count, base, instances};
}
static void submit(const p_draw *dw) { int en = 0, mp = dw->prim;
''' + submission + '}\n'
        prefix += 'static unsigned dispatches;\n' + dispatch + 'dispatches++; }\n'
        check = '''
static unsigned callbacks;
static void refill(NVNcommandBuffer *c, NVNcommandBufferMemoryEvent event, size_t size, void *data) {
    assert(event == NVN_COMMAND_BUFFER_MEMORY_EVENT_OUT_OF_COMMAND_MEMORY && size <= 512);
    callbacks++; pool_t pool = {.mem = data, .size = 512};
    h_nvnCommandBufferAddCommandMemory(c, (NVNmemoryPool *)&pool, 0, 512);
}
static unsigned small_grants;
static void refill_small(NVNcommandBuffer *c, NVNcommandBufferMemoryEvent event, size_t size, void *data) {
    (void)event; (void)size; small_grants++; pool_t pool = {.mem = data, .size = 80};
    h_nvnCommandBufferAddCommandMemory(c, (NVNmemoryPool *)&pool, 0, 80);
}
static size_t adjacent_offset;
static void refill_adjacent(NVNcommandBuffer *c, NVNcommandBufferMemoryEvent event, size_t size, void *data) {
    assert(event == NVN_COMMAND_BUFFER_MEMORY_EVENT_OUT_OF_COMMAND_MEMORY);
    pool_t *pool = data;
    adjacent_offset = CB(c)->guest_memory - pool->mem + h_nvnCommandBufferGetCommandMemoryUsed(c) + 4;
    assert(adjacent_offset + size < pool->size);
    h_nvnCommandBufferAddCommandMemory(c, (NVNmemoryPool *)pool, adjacent_offset, pool->size - adjacent_offset);
    assert(h_nvnCommandBufferGetCommandMemoryUsed(c) == (size_t)(align8(pool->mem + adjacent_offset) - (pool->mem + adjacent_offset)));
}
int main(void) {
    const p_dispatch groups[] = {{0, 1, 1, 0}, {1, 0, 1, 0}, {1, 1, 0, 0}, {1, 1, 1, 0}};
    for (unsigned i = 0; i < 4; i++) { dispatch(&groups[i]); assert(dispatches == (i == 3)); }
    _Alignas(8) uint8_t guest[3][128], supplied[512]; memset(guest, 0xa5, sizeof guest);
    assert(nvn_command_valid(supplied, (op_hdr){OP_BEGIN, 8}));
    assert(nvn_command_valid(supplied, (op_hdr){OP_BIND_UBO, 32}));
    assert(!nvn_command_valid(supplied + 4, (op_hdr){OP_COPY_STRIDE, 0}));
    assert(!nvn_command_valid(supplied, (op_hdr){OP_BEGIN, 0}));
    assert(!nvn_command_valid(supplied, (op_hdr){OP_BEGIN, 12}));
    assert(!nvn_command_valid(supplied, (op_hdr){OP_COUNT_, 8}));
    NVNcommandBuffer commands[3]; uint8_t *records[3];
    for (unsigned i = 0; i < 3; i++) {
        frame = i * 1000;
        NVNcommandBuffer *c = &commands[i]; assert(h_nvnCommandBufferInitialize(c, NULL));
        h_nvnCommandBufferSetMemoryCallback(c, refill); h_nvnCommandBufferSetMemoryCallbackData(c, supplied);
        CB(c)->command_callback_disabled = i != 0;
        pool_t pool = {.mem = guest[i], .size = 128};
        h_nvnCommandBufferAddCommandMemory(c, (NVNmemoryPool *)&pool, 4, 64);
        uint8_t *limit = CB(c)->end;
        ((uint8_t *)c)[0x10] = 1; // Zelda's inline commands mark the buffer dirty at main+0xbb5d0.
        assert(CB(c)->end == limit);
        assert(h_nvnCommandBufferGetCommandMemoryUsed(c) == 4);
        h_nvnCommandBufferBeginRecording(c);
        assert(h_nvnCommandBufferGetCommandMemoryUsed(c) == 12);
        records[i] = cmd_alloc(CB(c), 96); memset(records[i] + 8, 17 + i, 88);
        assert(h_nvnCommandBufferEndRecording(c) == (NVNcommandHandle)(guest[i] + 8));
        assert(h_nvnCommandBufferGetCommandMemoryUsed(c) == (i ? 28 : 104));
        assert(h_nvnCommandBufferGetCommandMemorySize(c) == (i ? 64 : 512));
        assert(h_nvnCommandBufferGetCommandMemoryFree(c) == (i ? 36 : 408));
        for (unsigned j = 68; j < 128; j++) assert(guest[i][j] == 0xa5);
        if (i) {
            uint8_t first[32]; memcpy(first, guest[i], sizeof first);
            size_t used = h_nvnCommandBufferGetCommandMemoryUsed(c);
            h_nvnCommandBufferAddCommandMemory(c, (NVNmemoryPool *)&pool, 4 + used, 128 - 4 - used);
            h_nvnCommandBufferBeginRecording(c); h_nvnCommandBufferEndRecording(c);
            assert(!memcmp(first, guest[i], sizeof first));
        }
    }
    assert(callbacks == 1 && records[1] != records[2]);
    for (unsigned i = 0; i < 3; i++) for (unsigned j = 8; j < 96; j++) assert(records[i][j] == 17 + i);
    _Alignas(8) uint8_t tail[160]; memset(tail, 0xa5, sizeof tail);
    pool_t short_pool = {.mem = tail, .size = 128}; NVNcommandBuffer short_commands;
    h_nvnCommandBufferInitialize(&short_commands, NULL); CB(&short_commands)->command_callback_disabled = 1;
    h_nvnCommandBufferAddCommandMemory(&short_commands, (NVNmemoryPool *)&short_pool, 80, 512);
    h_nvnCommandBufferBeginRecording(&short_commands); cmd_alloc(CB(&short_commands), 96);
    cmd_alloc(CB(&short_commands), 5000); h_nvnCommandBufferEndRecording(&short_commands);
    assert(h_nvnCommandBufferGetCommandMemorySize(&short_commands) == 48);
    assert(h_nvnCommandBufferGetCommandMemoryUsed(&short_commands) == 24);
    assert(h_nvnCommandBufferGetCommandMemoryFree(&short_commands) == 24);
    for (unsigned i = 128; i < sizeof tail; i++) assert(tail[i] == 0xa5);
    // The game's callback may only grant a tail too small for one host record (v1.4.3 movie: 200 bytes for an
    // 8-byte record plus the jump reserve); after its second grant recording continues in host overflow memory.
    _Alignas(8) uint8_t small_arena[80];
    NVNcommandBuffer small; assert(h_nvnCommandBufferInitialize(&small, NULL));
    h_nvnCommandBufferSetMemoryCallback(&small, refill_small); h_nvnCommandBufferSetMemoryCallbackData(&small, small_arena);
    h_nvnCommandBufferBeginRecording(&small);
    unsigned grants_before = small_grants;
    uint8_t *spilled = cmd_alloc(CB(&small), 96); memset(spilled + 8, 0x3c, 88);
    assert(small_grants - grants_before == 2 && (spilled < small_arena || spilled >= small_arena + sizeof small_arena));
    h_nvnCommandBufferEndRecording(&small);
    // Beginning a new recording must not link through the previous recording's retired arena tail.
    _Alignas(8) uint8_t shared_arena[128] = {0};
    pool_t shared_pool = {.mem = shared_arena, .size = sizeof shared_arena};
    NVNcommandBuffer old_recording, neighbor;
    h_nvnCommandBufferInitialize(&old_recording, NULL); CB(&old_recording)->command_callback_disabled = 1;
    h_nvnCommandBufferAddCommandMemory(&old_recording, (NVNmemoryPool *)&shared_pool, 0, 64);
    h_nvnCommandBufferBeginRecording(&old_recording); cmd_alloc(CB(&old_recording), 24);
    h_nvnCommandBufferEndRecording(&old_recording);
    assert(h_nvnCommandBufferGetCommandMemoryUsed(&old_recording) == 40);
    h_nvnCommandBufferInitialize(&neighbor, NULL);
    h_nvnCommandBufferAddCommandMemory(&neighbor, (NVNmemoryPool *)&shared_pool, 44, 84);
    h_nvnCommandBufferBeginRecording(&neighbor);
    NVNcommandHandle preserved = h_nvnCommandBufferEndRecording(&neighbor);
    assert(preserved == (NVNcommandHandle)(shared_arena + 48));
    uint8_t before[128]; memcpy(before, shared_arena, sizeof before);
    h_nvnCommandBufferBeginRecording(&old_recording);
    NVNcommandHandle fresh = h_nvnCommandBufferEndRecording(&old_recording);
    assert(fresh != preserved && !memcmp(before, shared_arena, sizeof before));
    assert(((op_hdr *)(uintptr_t)preserved)->op == OP_BEGIN);
    assert(((op_hdr *)(uintptr_t)fresh)->op == OP_BEGIN);
    assert(h_nvnCommandBufferGetCommandMemoryUsed(&old_recording) == 40);
    // A refill callback may reuse every byte not reported as consumed, including the reserved link area.
    _Alignas(8) uint8_t adjacent_arena[512] = {0};
    pool_t adjacent_pool = {.mem = adjacent_arena, .size = sizeof adjacent_arena};
    NVNcommandBuffer adjacent;
    h_nvnCommandBufferInitialize(&adjacent, NULL);
    h_nvnCommandBufferSetMemoryCallback(&adjacent, refill_adjacent);
    h_nvnCommandBufferSetMemoryCallbackData(&adjacent, &adjacent_pool);
    h_nvnCommandBufferAddCommandMemory(&adjacent, (NVNmemoryPool *)&adjacent_pool, 0, 64);
    h_nvnCommandBufferBeginRecording(&adjacent); cmd_alloc(CB(&adjacent), 32);
    assert(h_nvnCommandBufferEndRecording(&adjacent) == (NVNcommandHandle)adjacent_arena);
    uintptr_t continuation; memcpy(&continuation, adjacent_arena + 48, 8);
    assert(continuation == (uintptr_t)align8(adjacent_arena + adjacent_offset));
    assert(adjacent_offset == 60 && ((op_hdr *)continuation)->op == OP_END);
    NVNtextureHandle handles[33] = {0}; NVNcommandBuffer c;
    assert(h_nvnCommandBufferInitialize(&c, NULL)); cmd_new_segment(CB(&c), supplied, sizeof supplied);
    h_nvnCommandBufferBeginRecording(&c);
    h_nvnCommandBufferBindTexturesDeferred(&c, NVN_SHADER_STAGE_FRAGMENT, 95, 33, handles);
    h_nvnCommandBufferEndRecording(&c);
    const op_hdr *head = (const op_hdr *)(supplied + 8); assert(head->op == OP_BIND_TEX);
    const p_bind_handles *first = (const p_bind_handles *)(head + 1);
    const op_hdr *next = (const op_hdr *)((const uint8_t *)head + head->size);
    const p_bind_handles *second = (const p_bind_handles *)(next + 1);
    assert(first->first == 95 && first->n == 32 && second->first == 127 && second->n == 1);
    handles[0] = 123; handles[32] = 456;
    assert(nvn_bound_handles(first)[0] == 123 && nvn_bound_handles(second)[0] == 456);
    handles[0] = 789; assert(nvn_bound_handles(first)[0] == 789);
    p_bind_handles immediate = {.h = {999}}; assert(nvn_bound_handles(&immediate)[0] == 999);
    cmd_new_segment(CB(&c), supplied, sizeof supplied); h_nvnCommandBufferBeginRecording(&c);
    _Alignas(16) uint64_t counters[4] = {1ull << 40, 11, 1ull << 40, 99};
    h_nvnCommandBufferSetRenderEnableConditional(&c, NVN_CONDITIONAL_RENDER_MODE_RENDER_IF_EQUAL, (uintptr_t)counters);
    h_nvnCommandBufferSetRenderEnable(&c, 0); h_nvnCommandBufferSetRenderEnable(&c, 1);
    h_nvnCommandBufferEndRecording(&c);
    head = (const op_hdr *)(supplied + 8); assert(head->op == OP_RENDER_ENABLE);
    const p_render_enable *condition = (const p_render_enable *)(head + 1);
    assert(nvn_render_test(condition, counters));
    counters[0] = 0; assert(!nvn_render_test(condition, counters));
    p_render_enable inverted = *condition; inverted.mode = NVN_CONDITIONAL_RENDER_MODE_RENDER_IF_NOT_EQUAL;
    assert(nvn_render_test(&inverted, counters)); counters[0] = counters[2]; assert(!nvn_render_test(&inverted, counters));
    head = (const op_hdr *)((const uint8_t *)head + head->size);
    assert(!nvn_render_test((const p_render_enable *)(head + 1), NULL));
    head = (const op_hdr *)((const uint8_t *)head + head->size);
    assert(nvn_render_test((const p_render_enable *)(head + 1), NULL));
    cmd_new_segment(CB(&c), supplied, sizeof supplied); h_nvnCommandBufferBeginRecording(&c);
    h_nvnCommandBufferDrawArraysInstanced(&c, 5, 3, 8, 7, 4);
    h_nvnCommandBufferDrawArraysInstanced(&c, 5, 0, 8, 0, 0);
    h_nvnCommandBufferDrawArraysInstanced(&c, 5, 0, 0, 0, 4);
    h_nvnCommandBufferDrawArrays(&c, 4, 2, 3);
    h_nvnCommandBufferEndRecording(&c);
    head = (const op_hdr *)(supplied + 8);
    for (unsigned i = 0; i < 4; i++) {
        assert(head->op == OP_DRAW); submit((const p_draw *)(head + 1));
        if (!i) assert(draw_calls == 1 && submitted.first == 3 && submitted.count == 8 && submitted.base_instance == 7 && submitted.instances == 4);
        if (i == 1 || i == 2) assert(draw_calls == 1);
        head = (const op_hdr *)((const uint8_t *)head + head->size);
    }
    assert(draw_calls == 2 && submitted.first == 2 && submitted.count == 3 && submitted.base_instance == 0 && submitted.instances == 1);
    for (unsigned i = 0; i < 300; i++) assert(hostseg_take(4096));
    size_t reserved = 0; for (unsigned i = 0; i < HOSTSEG_MAX; i++) reserved += g_hostseg[i].mem != NULL;
    assert(reserved == 2);
    assert(records[1][8] == 18 && records[2][8] == 19);
    for (unsigned i = 0; i < HOSTSEG_MAX; i++) free(g_hostseg[i].mem);
}
'''
        with tempfile.TemporaryDirectory(dir=ROOT / 'work/tmp') as temp:
            directory = Path(temp)
            (directory / 'nvn_obj.h').write_text(adapt_objects((CORE / 'gfx/nvn/nvn_obj.h').read_text()))
            code, executable = directory / 'commands.c', directory / 'commands'
            code.write_text(prefix + recorder + check)
            subprocess.run(['clang', '-fsanitize=address,undefined', '-I', str(ROOT / 'host'), '-I', str(CORE / 'gfx/nvn'),
                            '-I', str(CORE / 'gfx/nvn/include'), str(code), '-o', str(executable)], check=True)
            subprocess.run([str(executable)], check=True)

    def test_unsupported_gpu_work_stops_and_source_drift_is_rejected(self):
        source = (CORE / 'gfx/nvn/hle_nvn.c').read_text()
        adapted = adapt_interface(source)
        self.assertIn('    exit(78);\n}\n\ntypedef struct', adapted)
        self.assertIn('if (strstr(g_nops[k], "Debug"))', adapted)
        self.assertIn('[0x00] = 55, [0x01] = 15,', adapted)
        self.assertIn('return (void *)h_nvnCommandBufferBindTexturesDeferred;', adapted)
        self.assertIn('BLOCKED NVN nvnCommandBufferBindImagesDeferred', adapted)
        with self.assertRaises(ValueError): adapt_interface(source.replace('    return 0;\n}\n\ntypedef struct', 'changed'))
        glue = adapt_glue((CORE / 'gfx/hle_nv.c').read_text())
        self.assertNotIn('FinalizeGraphics', glue)
        self.assertIn('InitializeGraphics', glue)
        with self.assertRaises(ValueError): adapt_glue('changed')
        with self.assertRaises(ValueError): adapt_objects('changed')
        with self.assertRaises(ValueError): adapt_backend('changed')
        backend = adapt_backend((CORE / 'gfx/nvn/nvn_mtl.m').read_text())
        self.assertIn('#include "nvn_texture_swizzle.inc"', backend)
        self.assertIn('r.has_v && r.v.set_format ? r.v.format : r.t.d.format', backend)
        self.assertIn('v = zelda_texture_swizzle(v, format, s);', backend)
