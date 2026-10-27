"""Version-bound elapsed-time stepping; addresses verified against TotK 1.4.3.

Timing fields and cutscene sites were identified by masagrator's FPSLocker config:
https://github.com/masagrator/FPSLocker-Warehouse/blob/v4/SaltySD/plugins/FPSLocker/patches/0100F2C0115B6000/277178B7DBA1B6D4.yaml
The native stubs use the existing 19.2 MHz clock and preserve the Apple ABI.
"""
import hashlib
import struct

BUILD_ID = '277178b7dba1b6d4949a84778d4abc58'
STATE_OFFSET = 0x3ae4000
MAGIC = b'ZFPS143\0'
DAY_MAGIC = b'ZDAY143\0'
ORIGINAL = {
    0x2db18c: '09604139',
    0x2daa54: '605242bd', 0x2daa6c: '00d8215e',
    0x3e562c: 'c05242bd', 0x3e5638: '00d8215e',
    0xbe14a8: '61a24039',
    0x2b36100: 'b6f37097', 0x2b33918: 'de170214',
    0x1fbc80: '0820201e0a04081f',
    0x68c15c: '693e40bd',  # World clock increment, independently verified against this build.
    # Player Fuse timer update and the strict-positive equipment restriction check.
    0x2b0368: '609e50bd8166010f0020211ee8260154',
    0x31cc84: '0820201e4c0d0054',
}


def apply_timing(env):
    if env['BUILD_ID'] != BUILD_ID:
        raise ValueError('FPS timing requires the verified v1.4.3 main Build ID')
    image, emit, here, branch = env['IMG'], env['emit'], env['here'], env['B']
    if env['KEY_PAGE_OFF'] + 0x4000 != STATE_OFFSET or STATE_OFFSET + 52 > env['PATCH_OFF']:
        raise ValueError('FPS state must be outside the read-only key and code pages')
    for offset, expected in ORIGINAL.items():
        if image[offset:offset + len(expected) // 2] != bytes.fromhex(expected):
            raise ValueError(f'FPS instruction mismatch at {offset:#x}')
    if any(image[STATE_OFFSET:]):
        raise ValueError('FPS state overlaps existing image data')
    image.extend(bytes(env['PATCH_OFF'] - len(image)))
    image[STATE_OFFSET:STATE_OFFSET + 8] = MAGIC
    struct.pack_into('<f', image, STATE_OFFSET + 12, 60.0)
    image[STATE_OFFSET + 36:STATE_OFFSET + 44] = DAY_MAGIC
    struct.pack_into('<fI', image, STATE_OFFSET + 44, 1.0, 0)
    writes, routines = [], {}

    def replace(offset, words, why):
        new = struct.pack(f'<{len(words)}I', *words)
        old = bytes(image[offset:offset + len(new)])
        writes.append(dict(offset=offset, original=old.hex(), replacement=new.hex(), reason=why))
        image[offset:offset + len(new)] = new

    def finish(name, start):
        body = bytes(env['patch'][start - env['PATCH_OFF']:])
        routines[name] = dict(offset=start, size=len(body), sha256=hashlib.sha256(body).hexdigest())

    start = here()
    # step = clamp(elapsed - lead, 1 / (cutscene ? 30 : configured target), 1 / 15); fps = 1 / step.
    # lead = clamp(lead + step - elapsed, 0, 0.25): game time ahead of host time after short frames is repaid by
    # later frames, so a ceiling no longer speeds the game up; time lost to long frames is not caught up (as before).
    emit(env['ADRP'](3, here(), STATE_OFFSET))
    for word in (
        0xf9406c22, 0x6dbe13e3, 0xfd000be5,    # ldr x2,[x1,#0xd8]; save d3-d5
        0x9e630041, 0x529f0002, 0x72a02482,    # d1 = ticks; w2 = 19.2 MHz
        0x1e630042, 0x1e621821,                # d1 = elapsed seconds
        0xbd400c62, 0x1e27d000, 0x39402062,    # s2 = state.target; s0 = 30; cutscene flag
        0x7100045f, 0x1e220c02, 0x1e22c042,    # ceiling; d2 = ceiling
        0x1e6e1000, 0x1e621802,                # d0 = 1; d2 = shortest step
        0x1e65d005, 0x1e651805,                # d5 = longest step 1/15
        0xfd400c63, 0x1e633824, 0x1e624884,    # d3 = state.lead; d4 = elapsed - lead; fmax
        0x1e655884, 0x1e642863, 0x1e613863,    # fmin; lead += step - elapsed
        0x9e6703e5, 0x1e654863, 0x1e6a1005,    # lead >= 0; d5 = 0.25
        0x1e655863, 0xfd000c63,                # lead <= 0.25; store
        0x1e641801, 0x1e624021, 0x1e390022,    # fps = 1 / step; integer fps
        0xb9005002, 0xbd002401,                # integer and float timing fields
        0xfd400860, 0x1e642800, 0xfd000860,    # cumulative seconds += step
        0xfd400be5, 0x6cc213e3,                # restore d3-d5
    ):
        emit(word)
    emit(branch(here(), 0x2db284))  # Original epilogue restores all saved registers.
    finish('frame_step', start)
    replace(0x2db18c, [branch(0x2db18c, start)], 'elapsed-time frame step with shared host ceiling')
    for offset, word in ((0x2daa54, 0xbd422660), (0x3e562c, 0xbd4226c0),
                         (0x2daa6c, 0xd503201f), (0x3e5638, 0xd503201f)):
        replace(offset, [word], 'consume fractional FPS instead of integer conversion')
    replace(0xbe14a8, [0x52800021], 'allow three swapchain buffers without changing dimensions')

    # ExecutePlayerOneTouchBond initializes the weapon/shield timer to (5, 5, -1).
    # At 90 FPS, float32 subtraction can leave 2^-24: below the old update
    # threshold (2^-23), but still positive to the restriction at 0x31cc84.
    # Continue this timer until <= 0; keep the original update and state checks.
    replace(0x2b036c, [0x0f000401],  # movi v1.2s,#0
            'let the Fuse equipment timer expire at fractional frame steps')

    for name, site, target, store in (
        ('movie_enter', 0x2b36100, 0x772fd8, 0x39002230),
        ('movie_exit', 0x2b33918, 0x2bb9890, 0x3900223f),
    ):
        start = here()
        emit(env['ADRP'](17, here(), STATE_OFFSET))
        if name == 'movie_enter':
            emit(0x52800030)  # w16 = 1; argument registers and LR remain intact.
        emit(store)
        emit(branch(here(), target))
        finish(name, start)
        replace(site, [branch(site, start) | (0x80000000 if name == 'movie_enter' else 0)],
                'pace prerendered movies at their original 30 FPS')

    start = here()
    end = start + 9 * 4
    for word in (0x1e202028,                              # fcmp s1,0
                 0x54000001 | (((end - (start + 4)) // 4) << 5),
                 0x785fa308, 0x7100151f,                 # type fields must both be 5
                 0x54000001 | (((end - (start + 16)) // 4) << 5),
                 0x785f8308, 0x7100151f,
                 0x54000001 | (((end - (start + 28)) // 4) << 5),
                 0x1e2e1008,                            # initialize this menu transition
                 0x1f08040a, 0x1e202008):               # original fmadd and compare flags
        emit(word)
    emit(branch(here(), 0x1fbc88))
    finish('menu_transition', start)
    replace(0x1fbc80, [branch(0x1fbc80, start), 0xd503201f], 'D-pad menu transition at fractional frame steps')

    # The original s9 increment advances day time and its associated world clocks, not the frame step.
    # Default bypass keeps the original load bit-exact, including NaN payloads and FP status.
    start = here()
    emit(0xbd403e69)                         # ldr s9,[x19,#0x3c]
    emit(0xa9bf47f0)                         # stp x16,x17,[sp,#-16]!
    emit(env['ADRP'](16, here(), STATE_OFFSET))
    emit(0xb9403211)                         # ldr w17,[x16,#48] (enabled)
    emit(0x340000b1)                         # cbz w17,+20 (restore x16/x17)
    emit(0x3c9f0fea)                         # str q10,[sp,#-16]!
    emit(0xbd402e0a)                         # ldr s10,[x16,#44] (day rate)
    emit(0x1e2a0929)                         # fmul s9,s9,s10
    emit(0x3cc107ea)                         # ldr q10,[sp],#16
    emit(0xa8c147f0)                         # ldp x16,x17,[sp],#16
    emit(branch(here(), 0x68c160))
    finish('day_clock', start)
    replace(0x68c15c, [branch(0x68c15c, start)], 'optional world clock rate; original increment by default')
    return dict(build_id=BUILD_ID, target_fps=60, supported_targets=[30, 60, 90, 120], movie_fps=30,
                state_offset=STATE_OFFSET, target_offset=STATE_OFFSET + 12, seconds_offset=STATE_OFFSET + 16, lead_offset=STATE_OFFSET + 24,
                day_rate_offset=STATE_OFFSET + 44, day_rate_enabled_offset=STATE_OFFSET + 48,
                graphics='original docked quality and dynamic-resolution parameters unchanged',
                writes=writes, routines=routines)
