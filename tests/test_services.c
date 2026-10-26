#include "../host/services.h"
#include "../host/amiibo_tag.h"
#include "../host/kernel.h"
#include "../host/audio.h"
#include "../host/audio_voice.h"
#include "../host/filesystem.h"
#include "dm.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <time.h>
#include <fcntl.h>
#include <pthread.h>
#include <sys/stat.h>

module_t g_mod[MAX_MOD];
int g_nmod;

static uint32_t message[65], result;
static bool send(uint32_t handle) {
    message[64] = 0xdeadbeef;
    bool handled = service_request(handle, message, 256, &result);
    assert(message[64] == 0xdeadbeef);
    return handled;
}
static void cmif(unsigned type, unsigned command) {
    memset(message, 0, sizeof message);
    message[0] = type; message[1] = 8; message[4] = 0x49434653; message[6] = command;
}
static void domain(unsigned object, unsigned command) {
    memset(message, 0, sizeof message);
    message[0] = 6; message[1] = 10;
    message[4] = 0x100001; message[5] = object;
    message[8] = 0x49434653; message[10] = command;
}
static void nfp_command(unsigned command) {
    cmif(4, command); message[1] = 12; message[8] = 1;
}
static void nfp_output(unsigned command, void *out, size_t size) {
    nfp_command(command); message[1] |= 3 << 10;
    uint64_t descriptor = (uintptr_t)out | ((uint64_t)size << 48);
    memcpy(message + 14, &descriptor, 8);
}
static void check_amiibo(uint32_t service) {
    // Entirely synthetic NTAG215: no user dump or keys are test fixtures.
    uint8_t dump[572] = {4, 1, 2, 0x8f, 3, 4, 5, 6, 4};
    memcpy(dump + 12, "\xf1\x10\xff\xee", 4);
    const uint8_t model[] = {1, 2, 3, 0, 4, 5, 9, 2}; memcpy(dump + 0x54, model, 8);
    amiibo_tag tag; assert(amiibo_tag_parse(dump, 540, &tag));
    assert(tag.model[0] == 2 && tag.model[1] == 1 && tag.model[3] == 9 && tag.model[4] == 5 && tag.model[5] == 4);
    assert(amiibo_tag_parse(dump, 532, &tag) && amiibo_tag_parse(dump, 572, &tag));
    assert(!amiibo_tag_parse(dump, 539, &tag) && !amiibo_tag_parse(dump, 541, &tag));
    dump[3] ^= 1; assert(!amiibo_tag_parse(dump, 540, &tag)); dump[3] ^= 1;
    dump[0x5b] = 0; assert(!amiibo_tag_parse(dump, 540, &tag)); dump[0x5b] = 2;
    char path[] = "work/tmp/amiibo-test-XXXXXX", error[256]; int fd = mkstemp(path); assert(fd >= 0);
    assert(write(fd, dump, 540) == 540); close(fd);
    nfp_command(17); assert(send(service) && !result && message[2] == 2); uint32_t activate = message[3];
    nfp_command(18); assert(send(service) && !result); uint32_t deactivate = message[3];
    nfp_command(3); assert(send(service) && !result && !message[6]);
    nfp_command(20); assert(send(service) && !result && message[8] == 1);
    assert(service_amiibo_load(path, error, sizeof error) && service_amiibo_selected());
    uint64_t regs[31] = {0}; regs[1] = (uintptr_t)&activate; regs[2] = 1;
    assert(kernel_svc(0x18, regs) && !regs[0]);
    nfp_command(20); assert(send(service) && !result && message[8] == 2);
    uint8_t output[0x100]; memset(output, 0xa5, sizeof output);
    nfp_output(13, output, 0x58); assert(send(service) && !result && !message[6]);
    assert(!memcmp(output, tag.uid, 7) && output[10] == 7 && output[0x20] == 1 && output[0x24] == 2 && output[0x58] == 0xa5);
    nfp_output(16, output, 0x40); assert(send(service) && !result && message[6]); // Not mounted.
    nfp_command(5); message[10] = 0; message[11] = 1; assert(send(service) && !result && !message[6]);
    nfp_output(16, output, 0x40); assert(send(service) && !result && !message[6] && !memcmp(output, tag.model, 7));
    memset(output, 0xa5, sizeof output);
    nfp_output(16, output, 6); assert(send(service) && !result && message[6] && output[0] == 0xa5);
    nfp_output(16, (void *)1, 0x40); assert(send(service) && result == 0xe601);
    nfp_command(21); message[8] = 99; assert(send(service) && !result && message[6]);
    assert(!service_amiibo_load("work/tmp/no-such-amiibo", error, sizeof error));
    nfp_command(20); assert(send(service) && !result && message[8] == 4); // Failed load is atomic.
    service_amiibo_remove(); assert(!service_amiibo_selected());
    regs[0] = 0; regs[1] = (uintptr_t)&deactivate; regs[2] = 1; regs[3] = 0;
    assert(kernel_svc(0x18, regs) && !regs[0]);
    nfp_command(20); assert(send(service) && !result && message[8] == 3);
    nfp_command(4); assert(send(service) && !result && !message[6]);
    assert(service_amiibo_load(path, error, sizeof error));
    nfp_command(3); assert(send(service) && !result && !message[6]); // Preselected tag is delivered.
    nfp_command(20); assert(send(service) && !result && message[8] == 2);
    nfp_command(4); assert(send(service) && !result);
    nfp_command(3); assert(send(service) && !result);
    nfp_command(20); assert(send(service) && !result && message[8] == 1); // No automatic repeat scan.
    assert(service_amiibo_load(path, error, sizeof error));
    nfp_command(20); assert(send(service) && !result && message[8] == 2);
    nfp_command(5); message[11] = 2; assert(send(service) && !result && message[6]); // RAM unsupported.
    service_amiibo_remove();
    regs[0] = activate; assert(kernel_svc(0x16, regs) && !regs[0]);
    regs[0] = deactivate; assert(kernel_svc(0x16, regs) && !regs[0]);
    unlink(path);
}
static uint32_t connect_sm(void) {
    uint32_t handle; assert(service_connect("sm:", &handle)); return handle;
}
static void register_sm(uint32_t handle) {
    memset(message, 0, sizeof message);
    message[0] = 16; message[1] = 0x80000000; message[2] = 1;
    assert(send(handle) && !result && message[1] == 1 && message[2] == 0);
}
static void *small_stack_request(void *handle) {
    cmif(4, 999); assert(!send((uint32_t)(uintptr_t)handle)); return NULL;
}
static void check_small_stack(uint32_t sm) {
    pthread_attr_t attr; assert(!pthread_attr_init(&attr));
    assert(!pthread_attr_setstacksize(&attr, 0x10000));
    pthread_t worker; assert(!pthread_create(&worker, &attr, small_stack_request, (void *)(uintptr_t)sm));
    pthread_attr_destroy(&attr); assert(!pthread_join(worker, NULL));
}
static void check_account(uint32_t sm) {
    memset(message, 0, sizeof message); message[0] = 17; message[1] = 2; memcpy(message + 2, "acc:u0", 6);
    assert(send(sm) && !result); uint32_t account = message[3];
    cmif(4, 140); assert(send(account) && result == 0x1c01);
    memset(message, 0, sizeof message); message[0] = 4; message[1] = 0x80000009; message[2] = 1;
    message[8] = 0x49434653; message[10] = 160;
    assert(send(account) && !result);
    cmif(4, 150); assert(send(account) && !result && !message[8]);
    cmif(4, 999); assert(!send(account));
    assert(service_close(account));
}
static void check_addon_content(uint32_t sm) {
    memset(message, 0, sizeof message); message[0] = 17; message[1] = 2; memcpy(message + 2, "aoc:u", 5);
    assert(send(sm) && !result); uint32_t aoc = message[3];
    uint32_t entries[3] = {0xa5a5a5a5, 0xa5a5a5a5, 0xa5a5a5a5}; uint64_t address = (uint64_t)entries;
    for (unsigned requested = 0; requested <= 4; requested++) {
        memset(message, 0, sizeof message); message[0] = 4 | (1u << 24); message[1] = 0x80000008; message[2] = 1;
        message[5] = sizeof entries; message[6] = address;
        message[7] = (((address >> 32) & 15) << 28) | ((address >> 36) << 2);
        message[8] = 0x49434653; message[10] = 3; message[13] = requested;
        assert(send(aoc) && result == (requested <= 3 ? 0 : 0x1c01));
        if (!result) assert(!message[8] && !message[6]);
        for (unsigned i = 0; i < 3; i++) assert(entries[i] == 0xa5a5a5a5);
    }
    assert(service_close(aoc));
}
static void check_fonts(uint32_t sm) {
    char path[] = "work/tmp/font-test.XXXXXX";
    int fd = mkstemp(path); assert(fd >= 0);
    uint8_t font[52] = {0, 1, 0, 0, 0, 2};
    memcpy(font + 12, "glyf", 4); font[23] = 44; font[27] = 4;
    memcpy(font + 28, "loca", 4); font[39] = 48; font[43] = 4;
    assert(!setenv("ZELDA_SHARED_FONT", path, 1));
    memset(message, 0, sizeof message); message[0] = 17; message[1] = 2; memcpy(message + 2, "pl:u", 4);
    assert(send(sm) && !result); uint32_t pl = message[3];
    for (unsigned invalid = 0; invalid < 4; invalid++) {
        uint8_t broken[sizeof font]; memcpy(broken, font, sizeof font);
        if (invalid == 0) memcpy(broken, "OTTO", 4);
        if (invalid == 1) broken[39] = 255; // Table outside the file.
        if (invalid == 2) memcpy(broken + 28, "CFF ", 4); // No loca table.
        if (invalid == 3) { memcpy(broken, "ttcf", 4); broken[11] = 1; memset(broken + 12, 255, 4); }
        assert(pwrite(fd, broken, sizeof broken, 0) == sizeof broken);
        cmif(4, 0); assert(!send(pl));
    }
    assert(pwrite(fd, font, sizeof font, 0) == sizeof font); close(fd);
    cmif(4, 0); assert(send(pl) && !result);
    cmif(4, 1); assert(send(pl) && !result && message[8] == 1);
    cmif(4, 2); assert(send(pl) && !result && message[8] == sizeof font);
    cmif(4, 3); assert(send(pl) && !result && message[8] == 8);
    cmif(4, 1); message[8] = 6; assert(send(pl) && result == 0x1c01);
    cmif(4, 4); assert(send(pl) && !result && message[2] == 2);
    uint64_t regs[31] = {message[3]}; assert(kernel_svc(0x16, regs) && !regs[0]);
    assert(service_close(pl)); unlink(path); unsetenv("ZELDA_SHARED_FONT");
}
static void check_audio_voice(void) {
    uint8_t p[0x188] = {0}; p[8] = p[9] = 1; p[11] = 2;
    uint32_t rate = 48000, channels = 1, count = 1; float pitch = 1;
    memcpy(p + 12, &rate, 4); memcpy(p + 24, &channels, 4); memcpy(p + 28, &pitch, 4);
    memcpy(p + 0x54, &count, 4);
    audio_wave waves[4] = {{.address = 1, .size = 960, .end = 480}, {.sent = 1}, {.sent = 1}, {.sent = 1}};
    memcpy(p + 0x78, waves, sizeof waves);
    audio_voice v = {0}; assert(audio_voice_update(&v, p, 10, rate));
    assert(!v.played && !v.consumed);
    p[8] = 0; waves[0].sent = 1; memcpy(p + 0x78, waves, sizeof waves);
    assert(audio_voice_update(&v, p, 11, rate) && v.played == 240 && !v.consumed);
    p[10] = 2; assert(audio_voice_update(&v, p, 11, rate));
    assert(audio_voice_update(&v, p, 30, rate) && v.played == 240);
    p[10] = 0; assert(audio_voice_update(&v, p, 30, rate));
    assert(audio_voice_update(&v, p, 31, rate) && v.played == 480 && v.consumed == 1);
    assert(audio_voice_update(&v, p, 300, rate) && v.played == 480 && v.consumed == 1);
    // Recycled slots, finite loops and EOS must release exactly once.
    p[8] = 1; waves[0].sent = 0; waves[0].loop = 1; waves[0].loops = 2; waves[0].end_of_stream = 1;
    memcpy(p + 0x78, waves, sizeof waves); assert(audio_voice_update(&v, p, 0, rate));
    p[8] = 0; waves[0].sent = 1; memcpy(p + 0x78, waves, sizeof waves);
    assert(audio_voice_update(&v, p, 5, rate) && v.played == 1200 && !v.consumed);
    assert(audio_voice_update(&v, p, 6, rate) && !v.played && v.consumed == 1);
    // The real startup voice is mono ADPCM with an indefinitely looping second buffer.
    p[8] = 1; p[11] = 6; waves[0] = (audio_wave){.address = 1, .size = 80, .end = 140, .loop = 1, .loops = -1};
    memcpy(p + 0x78, waves, sizeof waves); assert(audio_voice_update(&v, p, 0, rate));
    p[8] = 0; waves[0].sent = 1; memcpy(p + 0x78, waves, sizeof waves);
    p[0x174] = 1; assert(audio_voice_update(&v, p, 0, rate));
    assert(audio_voice_update(&v, p, 10000, rate) && !v.consumed && v.played == 2400000 % 140);
    p[10] = 1; assert(audio_voice_update(&v, p, 10000, rate) && v.consumed == 1 && !v.played);
    assert(audio_voice_update(&v, p, 10001, rate) && v.consumed == 1);
    p[8] = 1; p[10] = 0; p[0x174] = 0;
    waves[0] = (audio_wave){.address = 1, .size = 80, .end = 140};
    waves[1] = (audio_wave){.address = 1, .size = 80, .end = 140, .loop = 1, .loops = -1};
    memcpy(p + 0x78, waves, sizeof waves); count = 2; memcpy(p + 0x54, &count, 4);
    assert(audio_voice_update(&v, p, 0, rate)); p[8] = 0;
    waves[0].sent = waves[1].sent = 1; memcpy(p + 0x78, waves, sizeof waves);
    assert(audio_voice_update(&v, p, 2, rate) && v.consumed == 1 && v.head == 1 && v.played == 480);
    p[0x171] = 1; assert(audio_voice_update(&v, p, 2, rate) && v.consumed == 2 && !v.ready[1]); p[0x171] = 0;
    p[8] = 1; waves[0].sent = 0; waves[0].end = 141; memcpy(p + 0x78, waves, sizeof waves);
    assert(!audio_voice_update(&v, p, 10002, rate));
    waves[0].end = 140; memcpy(p + 0x78, waves, sizeof waves);
    pitch = NAN; memcpy(p + 28, &pitch, 4); assert(!audio_voice_update(&v, p, 10002, rate));
    pitch = 1; memcpy(p + 28, &pitch, 4);
    const unsigned sizes[] = {7, 8, 9, 10, 5431}, samples[] = {12, 14, 14, 16, 9504};
    for (unsigned i = 0; i < 5; i++) {
        waves[0] = (audio_wave){.address = 1, .size = sizes[i], .end = samples[i]};
        memcpy(p + 0x78, waves, sizeof waves); assert(audio_voice_update(&v, p, 0, rate));
        p[8] = 0; waves[0].sent = 1; memcpy(p + 0x78, waves, sizeof waves);
        assert(audio_voice_update(&v, p, 100, rate) && v.consumed == 1 && v.played == samples[i]);
        p[8] = 1; waves[0].sent = 0; waves[0].end++;
        memcpy(p + 0x78, waves, sizeof waves); assert(!audio_voice_update(&v, p, 100, rate));
    }
}
static void check_audio(void) {
    check_audio_voice();
    uint32_t config[13] = {48000, 240, 4, 1, 2, 1, 1}; config[12] = 0x3f564552;
    assert(audio_open(config, sizeof config));
    assert(audio_value(0) == 48000 && audio_value(3) == 1);
    uint32_t event = audio_event(); assert(event);
    uint64_t regs[31] = {0, (uint64_t)&event, 1, 0};
    assert(kernel_svc(0x18, regs) && regs[0] == 0xea01);
    assert(audio_set(5, 0)); regs[1] = (uint64_t)&event; regs[3] = 1000000000;
    assert(kernel_svc(0x18, regs) && !regs[0]);
    assert(audio_set(6, 0) && audio_value(3) == 1);
    uint32_t header[16] = {0x3f564552, 16, 9 * 32, 2 * 0x188, 2 * 0x70, 0xc0, 0, 0x140, 16};
    header[15] = 64; for (unsigned i = 1; i < 15; i++) header[15] += header[i];
    uint8_t *input = calloc(1, header[15]); uint8_t output[4096]; memset(output, 0xa5, sizeof output);
    assert(input); memcpy(input, header, sizeof header); input[64 + 16 + 16] = 4;
    size_t length = audio_update(input, header[15], output, sizeof output);
    assert(length && length < sizeof output && output[length] == 0xa5 && output[64] == 5);
    uint32_t total; memcpy(&total, output + 60, 4); assert(total == length);
    input[64 + header[1] + header[2] + header[4] + 9] = 1;
    assert(!audio_update(input, header[15], output, sizeof output));
    assert(!audio_update(input, 63, output, sizeof output));
    input[64 + header[1] + header[2] + header[4] + 9] = 0;
    regs[0] = event; assert(kernel_svc(0x16, regs) && !regs[0]); audio_close();
    assert(!audio_update(input, header[15], output, sizeof output)); free(input);
}
static void check_audio_device(uint32_t sm) {
    memset(message, 0, sizeof message); message[0] = 17; message[1] = 2; memcpy(message + 2, "audren:u", 8);
    assert(send(sm) && !result); uint32_t manager = message[3];
    cmif(4, 2); assert(send(manager) && !result); uint32_t device = message[3];
    char names[3][256]; uint32_t count;
    for (unsigned command = 6; command <= 14; command += 8) for (unsigned capacity = 256; capacity <= sizeof names; capacity += 512) {
        memset(names, 0xa5, sizeof names); uint64_t output = (uint64_t)names;
        memset(message, 0, sizeof message); message[0] = 4 | (1u << 24); message[1] = 7;
        message[2] = capacity; message[3] = output;
        message[4] = (((output >> 32) & 15) << 28) | ((output >> 36) << 2);
        message[8] = 0x49434653; message[10] = command;
        assert(send(device) && !result); count = message[8];
        assert(count == capacity / 256 && !strcmp(names[0], "AudioTvOutput"));
        if (count == 3) assert(!strcmp(names[1], "AudioBuiltInSpeakerOutput") && !strcmp(names[2], "AudioStereoJackOutput"));
        else assert((unsigned char)names[1][0] == 0xa5);
    }
    char name[256] = "AudioExternalOutput"; uint64_t address = (uint64_t)name;
    for (unsigned i = 0; i < 2; i++) {
        memset(message, 0, sizeof message); message[0] = 4 | (1u << 16) | (1u << 20); message[1] = 6;
        message[4] = sizeof name; message[5] = address;
        message[6] = (((address >> 32) & 15) << 28) | ((address >> 36) << 2);
        message[8] = 0x49434653; message[10] = i ? 8 : 7;
        float volume = 2.0f; memcpy(message + 12, &volume, 4);
        assert(send(device) && !result);
        if (i) { memcpy(&volume, message + 8, 4); assert(volume == 2.0f); }
    }
    cmif(4, 12); assert(send(device) && !result && message[2] == 2);
    uint32_t event = message[3]; assert(event);
    assert(service_close(device) && service_close(manager));
    uint64_t regs[31] = {0, (uint64_t)&event, 1, 0};
    assert(kernel_svc(0x18, regs) && regs[0] == 0xea01);
    regs[0] = event; assert(kernel_svc(0x16, regs) && !regs[0]);
}
static void logger_frame(uint64_t address, unsigned length, bool x, bool a, bool use_x) {
    memset(message, 0, sizeof message);
    message[0] = 6 | ((unsigned)x << 16) | ((unsigned)a << 20);
    size_t p = 2;
    if (x) {
        if (use_x) { message[p] = (length << 16) | (((address >> 32) & 15) << 12) | (((address >> 36) & 63) << 6); message[p + 1] = address; }
        p += 2;
    }
    if (a) {
        if (!use_x) { message[p] = length; message[p + 1] = address; message[p + 2] = (((address >> 32) & 15) << 28) | ((address >> 36) << 2); }
        p += 3;
    }
    size_t aligned = (p + 3) & ~(size_t)3;
    message[1] = aligned + 8 - p;
    message[aligned] = 0x100001; message[aligned + 1] = 2;
    message[aligned + 4] = 0x49434653;
}
static void check_logger(uint32_t lm) {
    uint64_t r[31] = {0, 2, 0xffff8001, 0};
    assert(kernel_svc(0x29, r) && !r[0]); uint64_t address = r[1];
    r[0] = address; r[1] = 0x1000; assert(kernel_svc(0x2c, r) && !r[0]);
    uint32_t *packet = (void *)address; packet[5] = 4;
    logger_frame(address, 28, true, false, true); assert(send(lm) && !result);
    logger_frame(address, 28, false, true, false); assert(send(lm) && !result);
    logger_frame(address, 28, true, true, false); assert(send(lm) && !result);
    logger_frame(address, 28, true, true, true); assert(send(lm) && !result);
    logger_frame(address, 28, true, true, true); message[4] = 28; assert(send(lm) && result == 0x1c01);
    logger_frame(address, 28, false, true, false); packet[5] = 5; assert(send(lm) && result == 0x1c01);
    logger_frame(address, 23, true, false, true); assert(send(lm) && result == 0x1c01);
    logger_frame(1, 28, false, true, false); assert(send(lm) && result == 0xe601);
    logger_frame(address, 28, true, true, false); message[1] = 1; assert(send(lm) && result == 0x1c01);
    r[0] = address; r[1] = 0x1000; assert(kernel_svc(0x2d, r) && !r[0]);
}
static void storage_read_frame(uint64_t address, unsigned capacity, uint64_t offset, uint64_t length) {
    memset(message, 0, sizeof message);
    message[0] = 6 | (1u << 24); message[1] = 15;
    message[2] = capacity; message[3] = address;
    message[4] = 1 | (((address >> 32) & 15) << 28) | ((address >> 36) << 2);
    message[8] = 0x200001; message[9] = 2; message[12] = 0x49434653;
    memcpy(message + 16, &offset, 8); memcpy(message + 18, &length, 8);
}
static void check_storage(uint32_t sm) {
    memset(message, 0, sizeof message); message[0] = 17; message[1] = 2; memcpy(message + 2, "fsp-srv", 7);
    assert(send(sm) && !result); uint32_t fs = message[3];
    cmif(5, 0); assert(send(fs) && !result);
    memset(message, 0, sizeof message); message[0] = 6; message[1] = 0x8000000d; message[2] = 1;
    message[8] = 0x180001; message[9] = 1; message[12] = 0x49434653; message[14] = 1;
    assert(send(fs) && !result);
    domain(1, 1005); assert(send(fs) && !result && !message[12]);
    cmif(5, 2); assert(send(fs) && !result); uint32_t clone = message[3];
    char path[] = "work/tmp/romfs.XXXXXX";
    int file = mkstemp(path); assert(file >= 0 && !setenv("ZELDA_ROMFS", path, 1));
    domain(1, 200); assert(!send(clone) && !strcmp(service_blocker(), "invalid merged RomFS header or table bounds"));
    uint64_t header[10] = {0x50, 0x50, 4, 0x54, 24, 0x6c, 4, 0x70, 32, 0x200};
    assert(!ftruncate(file, 0x300));
    header[8] = UINT64_MAX; assert(pwrite(file, header, sizeof header, 0) == sizeof header);
    domain(1, 200); assert(!send(fs));
    header[8] = 32; assert(pwrite(file, header, sizeof header, 0) == sizeof header);
    domain(1, 200); assert(send(clone) && !result && message[12] == 2);
    domain(2, 4); assert(send(fs) && !result && message[12] == 0x300 && !message[13]);
    uint8_t data[84]; memset(data, 0xa5, sizeof data);
    storage_read_frame((uint64_t)data, 80, 0, 80); assert(send(clone) && !result);
    assert(!memcmp(data, header, 80) && data[80] == 0xa5);
    storage_read_frame((uint64_t)data, 80, 0x2ff, 2); assert(send(fs) && result == 0x1c01);
    storage_read_frame((uint64_t)data, 79, 0, 80); assert(send(fs) && result == 0x1c01);
    storage_read_frame(1, 80, 0, 80); assert(send(fs) && result == 0xe601);
    storage_read_frame(0, 0, 0x300, 0); assert(send(fs) && !result);
    domain(2, 1); assert(!send(fs)); // Read-only resource storage never writes to the image.
    domain(2, 0); message[4] = 2; assert(send(clone) && !result);
    domain(2, 4); assert(send(fs) && result == 0x1c01); // Domain closure is shared by clones.
    assert(service_close(fs) && service_close(clone));
    assert(!close(file) && !unlink(path) && !unsetenv("ZELDA_ROMFS"));
}
static void local_path_frame(unsigned command, const char *path, uint64_t argument) {
    uint64_t address = (uint64_t)path;
    cmif(4, command); message[0] |= 1u << 16;
    message[2] = ((strlen(path) + 1) << 16) | (((address >> 32) & 15) << 12) | (((address >> 36) & 63) << 6);
    message[3] = address;
    memcpy(message + (command == 0 ? 10 : 8), &argument, 8);
}
static void mapped_frame(unsigned command, bool input, void *data, size_t length) {
    uint64_t address = (uint64_t)data;
    memset(message, 0, sizeof message); message[0] = 4 | (1u << (input ? 20 : 24)); message[1] = 13;
    message[2] = length; message[3] = address;
    message[4] = (((address >> 32) & 15) << 28) | ((address >> 36) << 2);
    message[8] = 0x49434653; message[10] = command;
}
static void check_local_storage(uint32_t sm, uint32_t am) {
    char temp[] = "work/tmp/filesystem-ipc.XXXXXX";
    assert(mkdtemp(temp)); int original = open(".", O_RDONLY); assert(original >= 0);
    assert(!chdir(temp) && !mkdir("work", 0700));
    domain(6, 27); message[4] = 0x280001; message[1] = 16; message[14] = 4096; message[16] = 4096;
    assert(send(am) && !result && !message[10] && message[12] == 1 && !message[14] && !message[15]);
    memset(message, 0, sizeof message); message[0] = 17; message[1] = 2; memcpy(message + 2, "fsp-srv", 7);
    assert(send(sm) && !result); uint32_t fs = message[3];
    memset(message, 0, sizeof message); message[0] = 4; message[1] = 0x80000009; message[2] = 1;
    message[8] = 0x49434653; message[10] = 1;
    assert(send(fs) && !result);
    cmif(4, 51); message[1] = 24; message[8] = 1; message[10] = 0x0011e000; message[11] = 0x01007ef0;
    message[12] = 1; message[18] = 1;
    assert(send(fs) && !result && message[6] == 0x7d402);
    cmif(4, 62); assert(send(fs) && !result); uint32_t reader = message[3];
    uint8_t infos[0x61]; memset(infos, 0xa5, sizeof infos);
    mapped_frame(0, false, infos, 0x60); assert(send(reader) && !result && message[8] == 1 && !message[9]);
    uint64_t size; memcpy(&size, infos + 0x30, 8);
    assert(infos[8] == 1 && infos[9] == 5 && size == 4096 && !infos[0x38] && infos[0x60] == 0xa5);
    mapped_frame(0, false, infos, 0x60); assert(send(reader) && !result && !message[8] && !message[9]);
    assert(service_close(reader));
    cmif(4, 51); message[1] = 24; message[8] = 1; message[18] = 5;
    assert(send(fs) && !result); uint32_t cache = message[3];
    cmif(5, 3); assert(send(cache) && !result && message[8] == 0x1000);
    local_path_frame(0, "/data", 4); assert(send(cache) && !result && !message[6]);
    local_path_frame(8, "/data", 3); assert(send(cache) && !result); uint32_t file = message[3];
    char contents[] = "live";
    mapped_frame(1, true, contents, 4); message[16] = 4;
    assert(send(file) && !result && !message[6]);
    cmif(5, 0); assert(send(file) && !result && message[8] == 1);
    cmif(5, 2); assert(send(file) && !result); uint32_t clone = message[3];
    assert(service_close(file));
    domain(1, 4); assert(send(clone) && !result && !message[10] && message[12] == 4);
    cmif(4, 10); assert(send(cache) && !result && message[6]);
    domain(1, 0); message[4] = 2; assert(send(clone) && !result);
    assert(service_close(clone));
    cmif(4, 10); assert(send(cache) && !result && !message[6]);
    local_path_frame(8, "/data", 1); assert(send(cache) && !result); file = message[3];
    char copied[5] = {0};
    mapped_frame(0, false, copied, 4); message[16] = 4;
    assert(send(file) && !result && !message[6] && message[8] == 4 && !strcmp(copied, "live"));
    assert(service_close(file) && service_close(cache));
    cmif(4, 33); assert(send(fs) && !result && !message[6]); assert(service_close(fs));
    filesystem_shutdown();
    assert(!unlink("work/cache-0.lock") && !rmdir("work"));
    assert(!fchdir(original)); close(original); assert(!rmdir(temp));
}
int main(void) {
    kernel_init(0);
    uint32_t sm = connect_sm();
    const uint32_t init[] = {16, 0x80000000, 1, 0, 0};
    for (size_t n = 0; n < sizeof init; n++) {
        void *short_message = malloc(n ? n : 1); assert(short_message);
        memcpy(short_message, init, n);
        service_request(sm, short_message, n, &result);
        assert(result != 0);
        free(short_message);
    }
    register_sm(sm);
    check_audio();
    check_audio_device(sm);
    check_small_stack(sm);
    check_fonts(sm);
    check_account(sm);
    check_addon_content(sm);
    memset(message, 0, sizeof message); message[0] = 17; message[1] = 2;
    memcpy(message + 2, "pctl:s", 6);
    assert(send(sm) && !result && message[2] == 0x1015); // Not allowed by this NPDM, rather than a fake session.
    memset(message, 0, sizeof message);
    message[0] = 17; message[1] = 1023;
    assert(send(sm) && result == 0x1c01); // Oversized raw-data length.
    message[1] = 2; memcpy(message + 2, "nvdrv", 5);
    assert(!send(sm) && !strcmp(service_blocker(), "nvdrv"));
    message[2] = 0x6d6c; message[3] = 0;
    assert(send(sm) && !result && message[1] == 0x80000001 && message[2] == 0x20);
    uint32_t lm = message[3]; assert(lm != sm);
    cmif(7, 3); assert(send(lm) && !result && message[4] == 0x4f434653 && !message[8]);
    cmif(7, 0); assert(send(lm) && !result && message[8] == 1);
    // Domain OpenLogger with a PID descriptor, object 1, and an eight-byte scalar input.
    memset(message, 0, sizeof message);
    message[0] = 6; message[1] = 0x8000000d; message[2] = 1;
    message[8] = 0x180001; message[9] = 1;
    message[12] = 0x49434653; message[13] = 1;
    assert(send(lm) && !result && message[4] == 1 && message[8] == 0x4f434653 && message[12] == 2);
    memset(message, 0, sizeof message);
    message[0] = 6; message[1] = 10; message[4] = 0x100001; message[5] = 2;
    message[8] = 0x49434653; message[10] = 123;
    assert(!send(lm) && !strcmp(service_blocker(), "logger request command=123"));
    message[5] = 17; assert(send(lm) && result == 0x1c01);
    message[5] = 1; message[4] = 0xffff0001; assert(send(lm) && result == 0x1c01);
    check_logger(lm);
    cmif(5, 2); assert(send(lm) && !result && message[2] == 0x20);
    uint32_t lm_clone = message[3]; assert(lm_clone != lm);
    assert(service_close(lm));
    check_logger(lm_clone); // Domain objects survive closing the original session.
    assert(service_close(lm_clone));
    memset(message, 0, sizeof message);
    message[0] = 17; message[1] = 2; memcpy(message + 2, "apm", 3);
    assert(send(sm) && !result);
    uint32_t apm = message[3];
    cmif(4, 1); assert(send(apm) && !result && message[8] == 1);
    cmif(4, 0); assert(send(apm) && !result && message[2] == 0x20);
    uint32_t perf = message[3]; assert(perf != apm);
    cmif(4, 1); message[8] = 1;
    assert(!send(perf) && !strcmp(service_blocker(), "unconfigured performance mode"));
    cmif(4, 0); message[8] = 1; message[9] = 0x92220009;
    assert(send(perf) && !result);
    cmif(4, 1); message[8] = 1;
    assert(send(perf) && !result && message[8] == 0x92220009);
    cmif(4, 0); message[8] = 2;
    assert(!send(perf) && !strcmp(service_blocker(), "invalid performance mode"));
    cmif(4, 9); assert(!send(apm) && !strcmp(service_blocker(), "apm request command=9"));
    assert(service_close(perf) && service_close(apm));
    memset(message, 0, sizeof message);
    message[0] = 17; message[1] = 2; memcpy(message + 2, "appletOE", 8);
    assert(send(sm) && !result); uint32_t am = message[3];
    cmif(5, 0); assert(send(am) && !result);
    // OpenApplicationProxy carries both PID and current-process copy descriptors.
    memset(message, 0, sizeof message);
    message[0] = 6; message[1] = 0x8000000c; message[2] = 3; message[5] = 0xffff8001;
    message[8] = 0x180001; message[9] = 1; message[12] = 0x49434653;
    assert(send(am) && !result && message[12] == 2);
    domain(2, 0); assert(send(am) && !result && message[12] == 3); // CommonStateGetter.
    domain(2, 1); assert(send(am) && !result && message[12] == 4); // SelfController.
    domain(2, 2); assert(send(am) && !result && message[12] == 5); // WindowController.
    domain(5, 1); assert(send(am) && !result && message[12] == (uint32_t)getpid() && !message[13]);
    domain(5, 10); assert(send(am) && !result);
    domain(3, 0); assert(send(am) && !result && message[2] == 2);
    uint32_t event1 = message[3];
    domain(3, 0); assert(send(am) && !result && message[2] == 2);
    uint32_t event2 = message[3]; assert(event2 != event1);
    uint64_t regs[31] = {0, (uint64_t)&event1, 1, 0};
    assert(kernel_svc(0x18, regs) && !regs[0]);
    domain(3, 1); assert(send(am) && !result && message[12] == 15 && !message[10]);
    regs[1] = (uint64_t)&event2; regs[2] = 1; regs[3] = 0;
    assert(kernel_svc(0x18, regs) && regs[0] == 0xea01);
    domain(3, 1); assert(send(am) && !result && message[10] == 0x680);
    domain(3, 9); assert(send(am) && !result && message[12] == 1);
    domain(3, 5); assert(send(am) && !result && message[12] == 1);
    domain(3, 6); assert(send(am) && !result && message[12] == 1);
    domain(4, 91); assert(send(am) && !result && message[2] == 2);
    uint32_t suspend = message[3];
    domain(4, 90); assert(send(am) && !result && !message[12] && !message[13]);
    domain(4, 13); assert(send(am) && result == 0x1c01); // Missing three policy bytes.
    domain(4, 13); message[4] = 0x140001; message[1] = 11; message[12] = 0x010001;
    assert(send(am) && !result);
    domain(4, 14); assert(send(am) && result == 0x1c01);
    domain(4, 14); message[4] = 0x140001; message[1] = 11; message[12] = 1;
    assert(send(am) && !result);
    domain(4, 1234); assert(!send(am) && !strcmp(service_blocker(), "self-controller request command=1234"));
    domain(4, 62); assert(send(am) && result == 0x1c01);
    domain(4, 62); message[4] = 0x140001; message[1] = 11; message[12] = 2;
    assert(send(am) && !result);
    domain(4, 63); assert(send(am) && !result && message[12] == 2);
    domain(4, 62); message[4] = 0x140001; message[1] = 11; message[12] = 3;
    assert(send(am) && result == 0x1c01);
    domain(4, 63); assert(send(am) && !result && message[12] == 2);
    regs[0] = event1; assert(kernel_svc(0x16, regs) && !regs[0]);
    regs[0] = event2; assert(kernel_svc(0x16, regs) && !regs[0]);
    regs[0] = suspend; assert(kernel_svc(0x16, regs) && !regs[0]);
    domain(2, 20); assert(send(am) && !result && message[12] == 6);
    domain(6, 21); assert(send(am) && !result && !memcmp(message + 12, "zh-Hans", 8));
    domain(6, 123); assert(send(am) && !result && message[12] == UINT32_MAX);
    domain(6, 40); assert(send(am) && !result && message[12] == 1);
    domain(6, 1); message[4] = 0x140001; message[1] = 11; message[12] = 2;
    assert(send(am) && !result && message[12] == 7);
    domain(7, 0); assert(send(am) && !result && message[12] == 8);
    domain(8, 0); assert(send(am) && !result && message[12] == 0x88 && !message[13]);
    uint32_t launch[0x88 / 4 + 1]; uint64_t launch_address = (uint64_t)launch;
    for (unsigned use_static = 0; use_static < 2; use_static++) {
        memset(launch, 0xa5, sizeof launch); memset(message, 0, sizeof message);
        message[0] = 6 | (1u << 24); message[1] = 13 | (3u << 10);
        if (use_static) {
            message[18] = launch_address; message[19] = (launch_address >> 32) | (0x88u << 16);
        } else {
            message[2] = 0x88; message[3] = launch_address;
            message[4] = (((launch_address >> 32) & 15) << 28) | ((launch_address >> 36) << 2);
        }
        message[8] = 0x180001; message[9] = 8; message[12] = 0x49434653; message[14] = 11;
        assert(send(am) && !result && launch[0] == 0xc79497ca && launch[1] == 1 && launch[2] == 1);
        assert(!launch[3] && !launch[4] && !launch[5] && launch[0x88 / 4] == 0xa5a5a5a5);
    }
    domain(8, 0); message[4] = 2; assert(send(am) && !result);
    domain(7, 0); message[4] = 2; assert(send(am) && !result);
    domain(2, 3); assert(send(am) && !result && message[12] == 7);
    domain(7, 1); assert(send(am) && !result && message[12] == 0x3f800000);
    domain(7, 0); message[4] = 0x180001; message[1] = 12; message[12] = 0x3f000000; message[13] = 0x3f800000;
    assert(send(am) && !result);
    domain(7, 1); assert(send(am) && !result && message[12] == 0x3f000000);
    domain(7, 2); assert(send(am) && !result && message[12] == 0x3f800000);
    domain(7, 0); message[4] = 2; assert(send(am) && !result);
    domain(6, 1); message[4] = 0x140001; message[1] = 11; message[12] = 2;
    assert(send(am) && !result && message[10] == 0x480);
    domain(6, 210); assert(send(am) && !result && message[2] == 2);
    uint32_t operation_event = message[3];
    regs[1] = (uint64_t)&operation_event; regs[2] = 1; regs[3] = 0;
    assert(kernel_svc(0x18, regs) && regs[0] == 0xea01);
    regs[0] = operation_event; assert(kernel_svc(0x16, regs) && !regs[0]);
    domain(6, 9999); assert(!send(am));
    check_local_storage(sm, am);
    assert(service_close(am));
    memset(message, 0, sizeof message); message[0] = 17; message[1] = 2; memcpy(message + 2, "time:u", 6);
    assert(send(sm) && !result); uint32_t time_service = message[3];
    cmif(5, 3); assert(send(time_service) && !result && message[8] == 0x1000);
    cmif(5, 0); assert(send(time_service) && !result);
    domain(1, 20); assert(send(time_service) && !result && message[2] == 2);
    uint32_t clock_handle = message[3];
    regs[1] = 12; regs[2] = 0xffff8001; regs[3] = 0;
    assert(kernel_svc(0x29, regs) && !regs[0]); uint64_t clock_address = regs[1] + 0x10000;
    regs[0] = clock_handle; regs[1] = clock_address; regs[2] = 0x1000; regs[3] = 1;
    assert(kernel_svc(0x13, regs) && !regs[0]);
    uint8_t *clock = (void *)clock_address;
    assert(!memcmp(clock + 16, clock + 0x50, 16) && !memcmp(clock + 0x50, clock + 0x98, 16));
    assert(!memcmp(clock + 0xd8, clock + 0x110, 0x38));
    assert(!memcmp(clock + 16, clock + 0x100, 16));
    for (unsigned i = 0xd8; i < 0x100; i++) assert(!clock[i]); // Identity continuous adjustment, in nanoseconds.
    struct timespec now; clock_gettime(CLOCK_MONOTONIC, &now);
    int64_t offset; memcpy(&offset, clock + 0x40, 8);
    uint8_t clock_source[16]; memcpy(clock_source, clock + 16, 16);
    assert(llabs(offset + now.tv_sec - time(NULL)) <= 1);
    regs[0] = clock_handle; regs[1] = clock_address; regs[2] = 0x1000; assert(kernel_svc(0x14, regs) && !regs[0]);
    regs[0] = clock_handle; assert(kernel_svc(0x16, regs) && !regs[0]);
    domain(1, 3); assert(send(time_service) && !result); uint32_t timezone = message[12];
    domain(timezone, 101); assert(send(time_service) && result == 0x1c01);
    char *old_zone = getenv("TZ") ? strdup(getenv("TZ")) : NULL;
    assert(!setenv("TZ", "CST-8", 1)); tzset();
    domain(timezone, 101); message[4] = 0x180001; message[1] = 12;
    uint64_t epoch = 1582934400; memcpy(message + 12, &epoch, 8);
    assert(send(time_service) && !result);
    const uint8_t leap_day[8] = {0xe4, 7, 2, 29, 8, 0, 0, 0};
    assert(!memcmp(message + 12, leap_day, 8) && message[14] == 6 && message[15] == 59);
    assert(!strcmp((char *)(message + 16), "CST") && !message[18] && message[19] == 28800);
    uint8_t snapshot[0xd2]; memset(snapshot, 0xa5, sizeof snapshot);
    domain(1, 400); assert(send(time_service) && result == 0x1c01);
    domain(1, 400); message[4] = 0x140001; message[1] = 11 | (3 << 10); message[12] = 1;
    uint64_t snapshot_descriptor = (uintptr_t)(snapshot + 1) | (0xd0ull << 48);
    memcpy((uint8_t *)message + 52, &snapshot_descriptor, 8);
    assert(send(time_service) && !result && snapshot[0] == 0xa5 && snapshot[0xd1] == 0xa5);
    int64_t snapshot_time, snapshot_steady; memcpy(&snapshot_time, snapshot + 1 + 0x40, 8); memcpy(&snapshot_steady, snapshot + 1 + 0x90, 8);
    assert(snapshot_time == snapshot_steady + offset && llabs(snapshot_time - time(NULL)) <= 1);
    assert(!memcmp(snapshot + 1 + 0x10, clock_source, 16) && !memcmp(snapshot + 1 + 0x98, clock_source, 16));
    assert(!memcmp(snapshot + 1 + 0x50, snapshot + 1 + 0x58, 8));
    assert(!memcmp(snapshot + 1 + 0x60, snapshot + 1 + 0x78, 24));
    assert(!strcmp((char *)snapshot + 1 + 0xa8, "CST-8") && !snapshot[1 + 0xcc] && snapshot[1 + 0xcd] == 1);
    if (old_zone) { assert(!setenv("TZ", old_zone, 1)); free(old_zone); } else assert(!unsetenv("TZ"));
    tzset();
    assert(service_close(time_service));
    memset(message, 0, sizeof message); message[0] = 17; message[1] = 2; memcpy(message + 2, "prepo:u", 7);
    assert(send(sm) && !result); uint32_t prepo = message[3];
    cmif(4, 10400); assert(send(prepo) && !result);
    uint64_t report_session; memcpy(&report_session, message + 8, 8); assert(report_session);
    cmif(4, 10400); assert(send(prepo) && !result && !memcmp(message + 8, &report_session, 8));
    assert(service_close(prepo));
    memset(message, 0, sizeof message); message[0] = 17; message[1] = 2; memcpy(message + 2, "nfp:user", 8);
    assert(send(sm) && !result); uint32_t nfp = message[3];
    cmif(5, 3); assert(send(nfp) && !result && message[8] == 0x1000);
    cmif(4, 0); assert(send(nfp) && !result); uint32_t nfp_user = message[3];
    cmif(5, 3); assert(send(nfp_user) && !result && message[8] == 0x1000);
    cmif(4, 19); assert(send(nfp_user) && !result && !message[8]);
    cmif(4, 0); assert(send(nfp_user) && result == 0x1c01);
    uint8_t mcu[64] = {0}; uint64_t mcu_address = (uintptr_t)mcu;
    memset(message, 0, sizeof message); message[0] = 4 | (1u << 20); message[1] = 0x80000008; message[2] = 1;
    message[5] = sizeof mcu; message[6] = (uint32_t)mcu_address;
    message[7] = (((mcu_address >> 32) & 15) << 28) | ((mcu_address >> 36) << 2);
    message[8] = 0x49434653; message[12] = 1;
    assert(send(nfp_user) && !result);
    cmif(4, 19); assert(send(nfp_user) && !result && message[8] == 1);
    uint64_t readers[10]; memset(readers, 0xa5, sizeof readers);
    cmif(4, 2); message[1] |= 3 << 10;
    uint64_t readers_descriptor = (uintptr_t)readers | ((uint64_t)sizeof readers << 48);
    memcpy(message + 10, &readers_descriptor, 8);
    assert(send(nfp_user) && !result && message[8] == 1 && readers[0] == 1);
    for (unsigned i = 1; i < 10; i++) assert(readers[i] == 0xa5a5a5a5a5a5a5a5ull);
    check_amiibo(nfp_user);
    cmif(4, 23); assert(send(nfp_user) && !result); uint32_t nfp_event = message[3];
    regs[1] = (uintptr_t)&nfp_event; regs[2] = 1; regs[3] = 0; assert(kernel_svc(0x18, regs) && regs[0] == 0xea01);
    regs[0] = nfp_event; assert(kernel_svc(0x16, regs) && !regs[0]);
    cmif(4, 1); assert(send(nfp_user) && !result);
    cmif(4, 19); assert(send(nfp_user) && !result && !message[8]);
    assert(service_close(nfp_user) && service_close(nfp));
    check_storage(sm);
    uint32_t cmif_sm = connect_sm();
    memset(message, 0, sizeof message);
    message[0] = 4; message[1] = 0x8000000b; message[2] = 1;
    message[8] = 0x49434653; message[11] = 123;
    assert(send(cmif_sm) && !result && message[4] == 0x4f434653 && message[7] == 123);
    memset(message, 0, sizeof message); message[0] = 15;
    assert(send(cmif_sm) && !result);
    assert(send(cmif_sm) && result == 0xf601);
    assert(service_close(cmif_sm) && !service_close(cmif_sm));
    assert(send(cmif_sm) && result == 0xe401);
    uint32_t reused = connect_sm(); assert(reused != cmif_sm);
    assert(service_close(reused));
    uint32_t allocated[64]; unsigned count = 0;
    while (count < 64 && service_connect("sm:", &allocated[count])) count++;
    assert(count == 63); // One live SM plus sixty-three new sessions.
    for (unsigned i = 0; i < count; i++) assert(service_close(allocated[i]));
    assert(service_close(sm));
    // Mutate a valid scalar frame and vary the allocation size under AddressSanitizer.
    sm = connect_sm();
    const uint32_t control[] = {5, 8, 0, 0, 0x49434653, 0, 3, 0, 0, 0};
    for (size_t n = 0; n <= sizeof control; n++) {
        uint8_t *buf = malloc(n ? n : 1); assert(buf);
        for (size_t i = 0; i < n; i++) {
            memcpy(buf, control, n); buf[i] ^= 0x80;
            service_request(sm, buf, n, &result);
        }
        free(buf);
    }
    assert(service_close(sm));
    kernel_destroy();
    puts("IPC checks passed");
}
