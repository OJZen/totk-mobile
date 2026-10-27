"""Exercise the emitted Fuse timer threshold on native ARM64, without game data."""
from pathlib import Path
import platform
import struct
import subprocess
import tempfile
import unittest

from test_exclusive import encoders
from game_timing import apply_timing, BUILD_ID, ORIGINAL, STATE_OFFSET

SITE = 0x2b036c


def environment():
    env = encoders()
    env.update(BUILD_ID=BUILD_ID, KEY_PAGE_OFF=STATE_OFFSET - 0x4000,
               PATCH_OFF=0x3af0000, IMG=bytearray(STATE_OFFSET))
    for offset, expected in ORIGINAL.items():
        env['IMG'][offset:offset + len(expected) // 2] = bytes.fromhex(expected)
    return env


class FuseTimingTests(unittest.TestCase):
    def test_manifest_changes_only_the_threshold(self):
        env = environment()
        before = bytes(env['IMG'][SITE - 4:SITE + 12])
        record = apply_timing(env)
        change, = [w for w in record['writes'] if w['offset'] == SITE]
        self.assertEqual(change['original'], before[4:8].hex())
        self.assertEqual(len(bytes.fromhex(change['replacement'])), 4)
        self.assertEqual(env['IMG'][SITE - 4:SITE], before[:4])
        self.assertEqual(env['IMG'][SITE + 4:SITE + 12], before[8:])
        instruction, = env['md'].disasm(bytes.fromhex(change['replacement']), SITE)
        self.assertEqual((instruction.mnemonic, instruction.op_str), ('movi', 'v1.2s, #0'))

    def test_rejects_changed_timer_or_restriction_instructions(self):
        for site in (SITE - 4, SITE, SITE + 4, SITE + 8, 0x31cc84, 0x31cc88):
            with self.subTest(site=hex(site)):
                env = environment()
                env['IMG'][site] ^= 1
                with self.assertRaisesRegex(ValueError, 'instruction mismatch'):
                    apply_timing(env)
        env = environment()
        env['BUILD_ID'] = 'unverified'
        with self.assertRaisesRegex(ValueError, 'Build ID'):
            apply_timing(env)

    @unittest.skipUnless(platform.machine() in ('arm64', 'aarch64'), 'native ARM64 instructions')
    def test_countdown_expires_with_emitted_threshold(self):
        env = environment()
        original = struct.unpack_from('<I', env['IMG'], SITE)[0]
        apply_timing(env)
        replacement = struct.unpack_from('<I', env['IMG'], SITE)[0]
        # Compile the actual emitted instruction. Separate fmul/fadd reproduce
        # the game's rounding; this is not a double-precision timer simulation.
        code = r'''
#include <assert.h>
#include <float.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define GUARD(name, instruction) \
static int name(float value) { \
    int result; \
    __asm__ volatile("fmov s0, %s[value]\n\t" instruction "\n\t" \
                     "fcmp s0, s1\n\tcset %w[result], hi" \
                     : [result] "=r" (result) : [value] "w" (value) \
                     : "v0", "v1", "cc"); \
    return result; \
}
GUARD(old_guard, ".inst ORIGINAL_WORD")
GUARD(new_guard, ".inst REPLACEMENT_WORD")

typedef struct { float current, previous, rate; } Timer;
static void tick(Timer *timer, float step, int fixed) {
    if (!(fixed ? new_guard(timer->current) : old_guard(timer->current))) return;
    float delta, next;
    __asm__ volatile("fmul %s0, %s1, %s2" : "=w"(delta) : "w"(step), "w"(timer->rate));
    __asm__ volatile("fadd %s0, %s1, %s2" : "=w"(next) : "w"(timer->current), "w"(delta));
    timer->previous = timer->current;
    timer->current = next;
}
int main(void) {
    const int fps[] = {30, 60, 90, 120};
    for (unsigned i = 0; i < sizeof(fps) / sizeof(*fps); ++i) {
        Timer old = {5, 5, -1}, fixed = old;
        float step = 30.0f / fps[i];
        for (int frame = 0; frame < fps[i]; ++frame) {
            tick(&old, step, 0); tick(&fixed, step, 1);
        }
        assert(fixed.current <= 0);
        if (fps[i] == 90) {
            uint32_t bits; memcpy(&bits, &old.current, sizeof(bits));
            assert(bits == 0x33800000);
            for (int frame = 0; frame < 300; ++frame) tick(&old, 1, 0);
            assert(old.current > 0);
            tick(&old, step, 1);
            assert(old.current <= 0);
        } else assert(old.current == 0);
    }
    const float boundaries[] = {-1, -0.0f, 0, FLT_EPSILON / 2, FLT_EPSILON, FLT_EPSILON * 2, 5};
    for (unsigned i = 0; i < sizeof(boundaries) / sizeof(*boundaries); ++i) {
        float value = boundaries[i];
        assert(old_guard(value) == (value > FLT_EPSILON));
        assert(new_guard(value) == (value > 0));
    }
    Timer paused = {5, 5, -1};
    tick(&paused, 0, 1); assert(paused.current == 5);
    Timer jitter = {5, 5, -1};
    for (int frame = 0; frame < 120; ++frame)
        tick(&jitter, frame % 2 ? .5f : 1.0f / 3, 1);
    assert(jitter.current <= 0);
    float ended = jitter.current;
    tick(&jitter, 1, 1); assert(jitter.current == ended);
    puts("Fuse timer: original 90 FPS latch reproduced; emitted patch expires all tested steps.");
}
'''.replace('ORIGINAL_WORD', hex(original)).replace('REPLACEMENT_WORD', hex(replacement))
        with tempfile.TemporaryDirectory() as tmp:
            source, executable = Path(tmp) / 'fuse.c', Path(tmp) / 'fuse'
            source.write_text(code)
            subprocess.run(['clang', '-O2', '-Wall', '-Wextra', '-Werror', '-ffp-contract=off',
                            str(source), '-o', str(executable)], check=True)
            subprocess.run([str(executable)], check=True)
