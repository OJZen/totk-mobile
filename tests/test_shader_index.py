import sys
from pathlib import Path
import struct
import unittest
import ctypes
import subprocess
import tempfile
import os
import json
import hashlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'script'))
from index_shaders import stage, bnsh, archive
from build_shaders import shader_entries, reflection_helpers, point_size_limit_misuse, pointer_storage


class ShaderIndexTests(unittest.TestCase):
    def test_cache_alias_requires_the_exact_source_and_tool_fingerprint(self):
        from unittest.mock import patch
        import build_shaders as build
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(dir=root / 'work/tmp') as directory:
            path = Path(directory)
            script = path / 'script'; script.mkdir()
            inputs = [script / name for name in ('build_shaders.py', 'index_shaders.py', 'translate_shaders.py', 'spirv_subgroup.py')]
            inputs += [path / 'gfx/tools/dmsh_build.py', path / 'spirv-cross']
            inputs += [path / (name + '.dll') for name in ('dmsh_xlate', 'Ryujinx.Graphics.Shader', 'Ryujinx.Common', 'Spv.Generator')]
            for source in inputs:
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_bytes(b'fixture')
            aliases = script / 'shader-chain-aliases.json'; aliases.write_text('{}')
            with patch.multiple(build, ROOT=path, CORE=path, TOOLS=path, XLATE=path, __file__=str(inputs[0])):
                version = build.chain()
                self.assertRegex(version, r'^[0-9a-f]{12}$')
                aliases.write_text(json.dumps({version: '0123456789ab'}))
                self.assertEqual(build.chain(), '0123456789ab')
                for source in inputs:
                    source.write_bytes(b'changed')
                    self.assertNotIn(build.chain(), (version, '0123456789ab'))
                    source.write_bytes(b'fixture')
                inputs[-1].unlink()
                self.assertEqual(build.chain(), 'unprepared')

    def test_english_and_legacy_shader_stage_maps(self):
        from unittest.mock import patch
        import index_shaders
        import compile_runtime_shaders as runtime
        from prepare_graphics import adapt_backend
        from build_shaders import CORE
        root = Path(__file__).resolve().parents[1]
        names = dict(vs='vertex', ps='pixel', cs='compute', hs='hull', ds='domain', gs='geometry')
        self.assertEqual(runtime.LABELS, names)
        with tempfile.TemporaryDirectory(dir=root / 'work/tmp') as directory:
            path = Path(directory); source = path / 'source'; source.mkdir()
            (source / 'fixture.nvn').write_bytes(b'fixture')
            records = [({'key': f'{i:016x}', 'stage': stage, 'c1': ''}, b'code') for i, stage in enumerate(names)]
            with patch.object(index_shaders, 'archive', return_value=records):
                index_shaders.index(source, path / 'code')
            rows = (path / 'shader_map.tsv').read_text().splitlines()[1:]
            self.assertEqual([row.split('\t')[2] for row in rows], list(names.values()))

            backend = adapt_backend((CORE / 'gfx/nvn/nvn_mtl.m').read_text())
            start = backend.index('static int stage_of(NSString *zh) {')
            function = backend[start:backend.index('\n}', start) + 2]
            test = path / 'stages.m'
            test.write_text('#import <Foundation/Foundation.h>\n#include <assert.h>\n' + function + r'''
int main(void) { @autoreleasepool {
    NSArray *english = @[@"vertex", @"pixel", @"compute", @"hull", @"domain"];
    NSArray *legacy = @[@"\u9876\u70b9", @"\u50cf\u7d20", @"\u8ba1\u7b97", @"\u5916\u58f3", @"\u57df"];
    int stages[] = {0, 1, 5, 3, 4};
    for (unsigned i = 0; i < 5; i++) { assert(stage_of(english[i]) == stages[i]); assert(stage_of(legacy[i]) == stages[i]); }
    assert(stage_of(@"unknown") == -1 && stage_of(@"geometry") == -1 && stage_of(nil) == -1);
} }
''')
            subprocess.run(['clang', '-fobjc-arc', '-Wall', '-Wextra', '-Werror', str(test),
                            '-framework', 'Foundation', '-o', str(path / 'stages')], check=True, capture_output=True)
            subprocess.run([str(path / 'stages')], check=True)

    def test_linked_vertex_variant_checks_fragment_and_identity(self):
        root = Path(__file__).resolve().parents[1]
        vertex = {'key': '0123456789abcdef', 'stage': 'vs', 'c1': '01'}
        fragment = {'key': 'fedcba9876543210', 'stage': 'ps', 'c1': '02'}
        with tempfile.TemporaryDirectory(dir=root / 'work/tmp') as directory:
            path = Path(directory); (path / 'variants').mkdir()
            (path / 'index.json').write_text(json.dumps([vertex, fragment]))
            name = hashlib.sha256(f'{vertex["key"]}-00000000-{fragment["key"]}'.encode()).hexdigest()[:16]
            request = path / 'variants' / (name + '.json')
            data = {'base': vertex['key'], 'mask': 0, 'next': fragment['key']}
            request.write_text(json.dumps(data))
            linked = shader_entries(path)[2]
            self.assertEqual(linked['base'], vertex['key'])
            self.assertEqual(linked['next'], fragment)
            self.assertEqual(linked['c1'], vertex['c1'])
            self.assertEqual(linked['attr'], {})
            for value in (vertex['key'], '0000000000000000', [], '', None):
                request.write_text(json.dumps(dict(data, next=value)))
                with self.assertRaises(ValueError): shader_entries(path)

    def test_runtime_compile_retries_requests_that_were_never_translated(self):
        import compile_runtime_shaders as runtime
        root = Path(__file__).resolve().parents[1]
        vertex = {'key': '0123456789abcdef', 'stage': 'vs', 'c1': '01'}
        fragments = [{'key': 'fedcba987654321%d' % i, 'stage': 'ps', 'c1': '02'} for i in range(2)]
        with tempfile.TemporaryDirectory(dir=root / 'work/tmp') as directory:
            path = Path(directory); (path / 'variants').mkdir()
            (path / 'index.json').write_text(json.dumps([vertex, *fragments]))
            names = []
            for fragment in fragments:
                name = hashlib.sha256(f'{vertex["key"]}-00000000-{fragment["key"]}'.encode()).hexdigest()[:16]
                (path / 'variants' / (name + '.json')).write_text(json.dumps({'base': vertex['key'], 'mask': 0, 'next': fragment['key']}))
                names.append(name)
            (path / 'build-failures.json').write_text(json.dumps([{'key': names[0], 'error': 'SPIR-V translation missing'},
                                                                  {'key': names[1], 'error': 'unresolved resource bindings'}]))
            shaders, version = runtime.SHADERS, runtime.chain
            runtime.SHADERS, runtime.chain = path, lambda: 'test'
            try:
                self.assertEqual([entry['key'] for entry in runtime.requested([])], [names[0]])
            finally:
                runtime.SHADERS, runtime.chain = shaders, version

    def test_runtime_compile_drops_failures_it_rebuilt(self):
        import compile_runtime_shaders as runtime
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(dir=root / 'work/tmp') as directory:
            path = Path(directory); failures = path / 'build-failures.json'
            failures.write_text(json.dumps([{'key': 'a', 'error': 'SPIR-V translation missing'},
                                            {'key': 'b', 'error': 'unresolved resource bindings'}]))
            names = ('SHADERS', 'translator_current', 'index_captures', 'requested', 'translate')
            saved = [getattr(runtime, name) for name in names] + [runtime.build_shaders.build]

            def build():
                failures.write_text('[]')
                return False
            runtime.SHADERS, runtime.translator_current, runtime.index_captures = path, lambda: True, lambda: []
            runtime.requested, runtime.translate, runtime.build_shaders.build = lambda added: [{'key': 'a'}], lambda: None, build
            try:
                runtime.compile_pending()
            finally:
                for name, value in zip(names, saved): setattr(runtime, name, value)
                runtime.build_shaders.build = saved[-1]
            self.assertEqual([failure['key'] for failure in json.loads(failures.read_text())], ['b'])

    def test_compute_uniform_table_and_storage_write_reflection(self):
        helpers = reflection_helpers()
        reads = helpers['drv_reads']('(*spvDescriptorSet0.cp_c0)._m0[41].x')
        self.assertEqual(helpers['unfillable'](reads, 'cs'), [])
        self.assertEqual(helpers['unfillable'](reads, 'vs'), ['c0+0x290'])
        reads = helpers['drv_reads']('(*spvDescriptorSet0.cp_c0)._m0[index].x')
        self.assertTrue(helpers['unfillable'](reads, 'cs'))
        for offset in (0x100, 0x410):
            self.assertTrue(helpers['unfillable']({'0': {'dynamic': False, 'vec4_offsets': [offset]}}, 'cs'))
        clamp = 'float _111 = fast::min(_103, (*spvDescriptorSet0.vp_c0)._m0[97].x);'
        reads = helpers['drv_reads'](clamp)
        self.assertEqual(helpers['unfillable'](reads, 'vs'), [])
        self.assertEqual(helpers['unfillable'](reads, 'ps'), ['c0+0x610'])
        self.assertFalse(point_size_limit_misuse(clamp))
        self.assertTrue(point_size_limit_misuse(clamp.replace('[97].x', '[97].y')))
        self.assertTrue(point_size_limit_misuse('float4 v = (*spvDescriptorSet0.vp_c0)._m0[97u];'))
        source = 'struct spvDescriptorSetBuffer0 {\n device Buffer* cp_s0 [[id(0)]];\n const device Buffer* cp_s1 [[id(1)]];\n};'
        resources = {'sbufs': [{'binding': 0, 'cb_slot': 0, 'cb_offset': 0x310 // 4, 'flags': 1},
                               {'binding': 1, 'cb_slot': 0, 'cb_offset': 0x290 // 4, 'flags': 0}]}
        write, read = helpers['reflect']('test', 'cs', source, resources)
        self.assertEqual((write['kind'], write['reg'], write['rw']), ('sbuf', 0, True))
        self.assertEqual((read['kind'], read['reg'], read['rw']), ('ubo_ptr', 8, False))

    def test_uniform_held_storage_address_reflects_its_uniform_slot(self):
        helpers = reflection_helpers()
        # v1.4.3 cave_quadmesh: global memory at the address in c6[0] (NVN uniform slot 3).
        source = 'struct spvDescriptorSetBuffer0 {\n constant Block* vp_c6 [[id(0)]];\n const device Buffer* vp_s0 [[id(1)]];\n};'
        resources = {'sbufs': [{'binding': 2, 'slot': 0, 'cb_slot': 6, 'cb_offset': 0, 'flags': 0}]}
        uniform, storage = pointer_storage(helpers['reflect']('test', 'vs', source, resources), resources)
        self.assertEqual((uniform['kind'], uniform['reg']), ('cbuf', 3))
        self.assertEqual((storage['kind'], storage['reg'], storage['offset'], storage['rw']), ('sbuf_cb', 3, 0, False))
        for change in ({'flags': 1}, {'cb_offset': 1}, {'cb_slot': 2}, {'cb_slot': 17}):
            other = {'sbufs': [dict(resources['sbufs'][0], **change)]}
            storage = pointer_storage(helpers['reflect']('test', 'vs', source, other), other)[1]
            self.assertEqual((storage['kind'], storage.get('reg')), ('sbuf', None))   # still unresolved and blocked
        driver = {'sbufs': [{'binding': 0, 'slot': 0, 'cb_slot': 0, 'cb_offset': 0x110 // 4, 'flags': 0}]}
        storage = pointer_storage(helpers['reflect']('test', 'vs', source, driver), driver)[1]
        self.assertEqual((storage['kind'], storage['reg']), ('sbuf', 0))   # driver c0 storage slots are unchanged

    def test_interface_variants_preserve_code_and_validate_types(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(dir=root / 'work/tmp') as directory:
            path = Path(directory); (path / 'variants').mkdir()
            for stage, field, slots in (('ps', 'fout', 8), ('vs', 'attr', 16)):
                base = {'key': '0123456789abcdef', 'stage': stage, 'c1': '0102'}
                (path / 'index.json').write_text(json.dumps([base]))
                mask = 1 | 2 << (2 * (slots - 1))
                request = path / 'variants' / f'{base["key"]}-{mask:0{slots // 2}x}.json'
                request.write_text(json.dumps({'base': base['key'], 'mask': mask}))
                original, variant = shader_entries(path)
                self.assertEqual(original, base)
                self.assertEqual(variant['base'], base['key'])
                self.assertEqual(variant[field], {'0': 'uint', str(slots - 1): 'sint'})
                self.assertEqual(variant['c1'], base['c1'])
                for mask in (0, -1, 1 << (2 * slots), 3, True, 1):
                    request.write_text(json.dumps({'base': base['key'], 'mask': mask}))
                    with self.assertRaises(ValueError): shader_entries(path)
                request.unlink()

    def test_runtime_key_matches_archive_key(self):
        root = Path(__file__).resolve().parents[1]
        library = root / 'work/tmp/shader-key-test.dylib'
        source = '''#include "nvn.h"
#define die(...) abort()
#include "host/nvn_shader_capture.inc"
int key(const void *c, const void *d, void *h) { return zelda_shader_hash(c,d,h); }
void capture(const void *c, const void *d) { NVNshaderData shader = {.control=c, .data=(uintptr_t)d}; zelda_capture_shader(&shader); }
void pair(const void *vc, const void *vd, const void *fc, const void *fd) {
    NVNshaderData shaders[] = {{.control=fc, .data=(uintptr_t)fd}, {.control=vc, .data=(uintptr_t)vd}};
    zelda_capture_program(shaders, 2);
}
'''
        try:
            subprocess.run(['clang', '-x', 'c', '-', '-dynamiclib', '-Wno-deprecated-declarations',
                            '-I', str(root), '-I', str(root / 'core/gfx/nvn/include'),
                            '-o', str(library)], input=source, text=True, check=True, capture_output=True)
            runtime = ctypes.CDLL(str(library))
            runtime.key.argtypes = [ctypes.c_void_p] * 3
            control, code = self.fixture()
            struct.pack_into('<I', code, 0, 0x12345678)
            digest = ctypes.create_string_buffer(32)
            for _ in range(2):
                expected, _ = stage(control, code)
                self.assertEqual(runtime.key(bytes(control), bytes(code), digest), 1)
                self.assertEqual(digest.raw.hex()[:16], expected['key'])
                code[0x80] = 1
            runtime.capture.argtypes = [ctypes.c_void_p] * 2
            with tempfile.TemporaryDirectory(dir=root / 'work/tmp') as directory:
                current = Path.cwd()
                try:
                    os.chdir(directory)
                    for folder in ('work/tmp', 'work/shaders/source', 'work/shaders/code'): Path(folder).mkdir(parents=True)
                    path = Path(f'work/shaders/source/runtime-{expected["key"]}.nvn')
                    runtime.capture(bytes(control), bytes(code))
                    self.assertEqual(path.read_bytes(), control + code)
                    timestamp = path.stat().st_mtime_ns
                    runtime.capture(bytes(control), bytes(code))
                    self.assertEqual(path.stat().st_mtime_ns, timestamp)
                    self.assertEqual(list(Path('work/tmp').iterdir()), [])
                    path.unlink()
                    Path(f'work/shaders/code/{expected["key"]}.bin').touch()
                    runtime.capture(bytes(control), bytes(code))
                    self.assertFalse(path.exists())
                    invalid = bytearray(control); struct.pack_into('<I', invalid, 0, 0)
                    runtime.capture(bytes(invalid), bytes(code))
                    self.assertEqual(list(Path('work/shaders/source').iterdir()), [])
                    runtime.pair.argtypes = [ctypes.c_void_p] * 4
                    fragment = bytearray(control); struct.pack_into('<I', fragment, 0x714, 1)
                    fragment_key = stage(fragment, code)[0]['key']
                    pair_key = hashlib.sha256(f'{expected["key"]}-00000000-{fragment_key}'.encode()).hexdigest()[:16]
                    runtime.pair(bytes(control), bytes(code), bytes(fragment), bytes(code))
                    request = Path(f'work/shaders/variants/{pair_key}.json')
                    self.assertEqual(json.loads(request.read_text()), {'base': expected['key'], 'mask': 0, 'next': fragment_key})
                    timestamp = request.stat().st_mtime_ns
                    runtime.pair(bytes(control), bytes(code), bytes(fragment), bytes(code))
                    self.assertEqual(request.stat().st_mtime_ns, timestamp)
                    runtime.pair(bytes(invalid), bytes(code), bytes(fragment), bytes(code))
                    self.assertEqual(len(list(Path('work/shaders/variants').iterdir())), 1)
                    self.assertEqual(list(Path('work/tmp').iterdir()), [])
                finally:
                    os.chdir(current)
            struct.pack_into('<I', control, 0x700, 0x300)
            self.assertEqual(runtime.key(bytes(control), bytes(code), digest), 0)
            control, code = self.fixture(compute=True)
            expected, _ = stage(control, code)
            self.assertEqual(runtime.key(bytes(control), bytes(code), digest), 1)
            self.assertEqual(digest.raw.hex()[:16], expected['key'])
            self.assertEqual(list(archive(control + code)), [stage(control, code)])
            self.assertEqual(expected['local'], [4, 1, 1])
            struct.pack_into('<I', code, 0, 0x12345678)
            self.assertEqual(runtime.key(bytes(control), bytes(code), digest), 0)
            with self.assertRaises(ValueError): stage(control, code)
            with self.assertRaises(ValueError): list(archive(control + code))
        finally:
            library.unlink(missing_ok=True)

    def fixture(self, compute=False):
        control = bytearray(0x880 if compute else 0x7d1)
        struct.pack_into('<3I', control, 0, 0x98761234, 1, 16 if compute else 9)
        struct.pack_into('<I', control, 0x20, len(control))
        struct.pack_into('<8I', control, 0x6f8, 0x100, 0, 0x200, 0x200, 0x100 if compute else 0x30, 6, 0, 5 if compute else 0)
        if compute:
            struct.pack_into('<4I', control, 0x748, 4, 1, 1, 0)
        code = bytearray(0x200)
        struct.pack_into('<I', code, 0, 0x12345679 if compute else 0x12345678)
        return control, code

    def test_shared_control_distinct_code_and_control_bounds(self):
        control, code = self.fixture()
        a, _ = stage(control, code)
        code[0x80] = 1
        b, _ = stage(control, code)
        self.assertNotEqual(a['key'], b['key'])
        self.assertEqual(a['control_sha256'], b['control_sha256'])
        with self.assertRaises(ValueError): stage(control[:-1], code)
        struct.pack_into('<I', control, 0x700, 0x300)
        with self.assertRaises(ValueError): stage(control, code)

    def test_bnsh_headers_and_program_range(self):
        control, code = self.fixture()
        data = bytearray(0x2000)
        data[:8] = b'BNSH\0\0\0\0'
        struct.pack_into('<H', data, 12, 0xfeff)
        struct.pack_into('<I', data, 28, len(data))
        data[0x60:0x64] = b'grsc'
        struct.pack_into('<IQ', data, 0x7c, 1, 0xc0)
        struct.pack_into('<Q', data, 0xd0, 0x100)
        struct.pack_into('<Q', data, 0x108, 0x180)
        struct.pack_into('<QQII', data, 0x188, 0x200, 0x1800, len(code), len(control))
        data[0x200:0x200 + len(control)] = control
        data[0x1800:0x1a00] = code
        self.assertEqual(len(list(bnsh(data))), 1)
        with self.assertRaises(ValueError): list(bnsh(data[:-1]))
        struct.pack_into('<Q', data, 0xd0, 0x1fff)
        with self.assertRaises(ValueError): list(bnsh(data))
        with self.assertRaises(ValueError): list(archive(b'unknown'))

    def test_captured_shader_bounds(self):
        control, code = self.fixture()
        struct.pack_into('<I', code, 0, 0x12345678)
        record = control + code
        self.assertEqual(list(archive(record)), [stage(control, code)])
        with self.assertRaises(ValueError): list(archive(record[:-1]))
        with self.assertRaises(ValueError): list(archive(record + b'\0'))
        struct.pack_into('<I', record, 0x20, len(record) + 1)
        with self.assertRaises(ValueError): list(archive(record))
