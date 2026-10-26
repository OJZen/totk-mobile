#include "services.h"
#include "kernel.h"
#include "audio.h"
#include "filesystem.h"
#include <opus.h>
#include "dm.h"
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <pthread.h>
#include <stdlib.h>
#include <time.h>
#include <fcntl.h>
#include <errno.h>
#include <math.h>
#include <sys/stat.h>
#include <mach/mach.h>
#include "mach_memory.h"

enum { SM = 1, LM, LOGGER, APM, APM_SESSION, APPLET, APP_PROXY, COMMON_STATE, SELF,
       WINDOW, AUDIO, DISPLAY, LIBRARY, APPLICATION, DEBUG, AOC, ECTX, FS, PCTL, PCTL_SESSION,
       TIME, USER_CLOCK, NETWORK_CLOCK, STEADY_CLOCK, TIMEZONE, LOCAL_CLOCK, ROM_STORAGE, ACCOUNT, FONTS, AUD_MANAGER, AUD_RENDERER, AUD_DEVICE, OPUS_MANAGER,
       LAUNCH_STORAGE, LAUNCH_ACCESSOR, CACHE_READER, ACCOUNT_MANAGER, ACCOUNT_PROFILE, SAVE_FS, TEMP_FS, LOCAL_FILE, CACHE_FS, PREPO, NFP, NFP_USER,
       INVALID_HANDLE = 0xe401, INVALID_MESSAGE = 0x1c01 };
static const char *kind_names[] = {"none", "sm", "lm", "logger", "apm", "apm-session",
    "appletOE", "application-proxy", "common-state", "self-controller", "window-controller",
    "audio-controller", "display-controller", "library-applet-creator", "application-functions", "debug-functions",
    "aoc:u", "ectx:aw", "fsp-srv", "pctl", "parental-control", "time:u", "user-clock", "network-clock", "steady-clock", "timezone", "local-clock", "rom-storage", "acc:u0", "pl:u", "audren:u", "audio-renderer", "audio-device", "hwopus", "launch-storage", "launch-accessor", "cache-reader", "account-manager", "account-profile", "save-filesystem", "temporary-filesystem", "local-file", "cache-filesystem", "prepo:u", "nfp:user", "nfp-user"};
// Current main.npdm service-access list; optional privileged services must get SM NotAllowed.
static const char *allowed_services[] = {"acc:u0", "aoc:u", "apm", "appletOE", "audin:u", "audout:u", "audren:u",
    "banana", "bcat:u", "bsd:u", "bsdcfg", "caps:su", "caps:u", "csrng", "ectx:aw", "friend:u", "fsp-srv", "hid",
    "htc", "htc:tenv", "htcs", "hwopus", "irs", "ldn:u", "ldr:ro", "lm", "mii:u", "mm:u", "mnpp:app", "nfc:mf:u",
    "nfc:user", "nfp:user", "ngc:u", "ngct:u", "nifm:u", "nim:eca", "notif:a", "nsd:u", "ntc", "nvdrv", "pcm",
    "pctl", "pl:u", "prepo:u", "set", "sfdnsres", "ssl", "time:u", "vi:u"};
static pthread_mutex_t services_lock = PTHREAD_MUTEX_INITIALIZER;
struct service_context { uint32_t kind, objects[16], value, values[16]; local_file *file, *files[16]; unsigned refs; bool registered, domain; };
static struct session { uint32_t handle; bool closed; struct service_context *context; } sessions[64];
static uint32_t next_handle = 0x10000;
static _Thread_local char blocker[80] = "unsupported IPC";
static uint32_t performance_config[2];
static bool performance_config_set[2];
static bool request_exit_on_execute;
static struct { uint32_t write, read; } notifications[8]; // Applet, audio routing and NFC availability changes.
static bool initial_focus_pending = true;
static struct { bool foreground, operation_notification, performance_notification, restart_notification, suspend_out_of_focus; uint8_t focus_handling[3]; uint32_t idle_extension; } applet_state;
static uint8_t clock_page[0x1000];
static bool clock_initialized;
static uint32_t cpu_boost_mode;
static int romfs_fd = -1;
static uint64_t romfs_size;
static uint64_t romfs_reads, romfs_bytes;
static uint8_t *font_memory;
static uint32_t font_size;
static const char *audio_devices[] = {"AudioTvOutput", "AudioBuiltInSpeakerOutput", "AudioStereoJackOutput", "MainAudioOut", "AudioExternalOutput", "AudioUsbDeviceOutput"};
static float device_volume[6] = {1, 1, 1, 1, 1, 1};
static float expected_volume[2] = {1, 1};
static bool preselected_user_pending = true;
static const uint32_t preselected_user[0x88 / 4] = {0xc79497ca, 1, 1}; // One local offline profile, UID 1.
#define FONT_MEMORY_SIZE 0x1100000

static void close_fonts(void) { free(font_memory); font_memory = NULL; }
static uint32_t font_be32(const uint8_t *p) {
    return (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 | (uint32_t)p[2] << 8 | p[3];
}
static bool has_truetype_outlines(const uint8_t *data, size_t size) {
    size_t face = 0;
    if (size < 12) return false;
    if (!memcmp(data, "ttcf", 4)) {
        uint32_t count = font_be32(data + 8);
        if (!count || count > (size - 12) / 4) return false;
        face = font_be32(data + 12);
    }
    // The SDK's glyph loader dereferences loca/glyf even when a CFF face was accepted at initialization.
    if (face > size - 12 || memcmp(data + face, "\0\1\0\0", 4)) return false;
    unsigned count = (unsigned)data[face + 4] << 8 | data[face + 5];
    if (count > (size - face - 12) / 16) return false;
    bool glyf = false, loca = false;
    for (unsigned i = 0; i < count; i++) {
        const uint8_t *table = data + face + 12 + i * 16;
        uint32_t offset = font_be32(table + 8), length = font_be32(table + 12);
        if (offset > size || length > size - offset) return false;
        if (!memcmp(table, "glyf", 4) && length) glyf = true;
        if (!memcmp(table, "loca", 4) && length >= 4) loca = true;
    }
    return glyf && loca;
}
static bool initialize_fonts(void) {
    if (font_memory) return true;
    const char *path = getenv("ZELDA_SHARED_FONT");
    // shortcut: one installed CJK font substitutes all six faces; platform-specific symbols may be absent.
    FILE *file = fopen(path && *path ? path : "/System/Library/Fonts/Supplemental/AppleGothic.ttf", "rb");
    if (!file) return false;
    struct stat st;
    if (fstat(fileno(file), &st) || st.st_size < 12 || st.st_size > FONT_MEMORY_SIZE - 8) { fclose(file); return false; }
    uint8_t *data = calloc(1, FONT_MEMORY_SIZE);
    if (!data) { fclose(file); return false; }
    bool valid = fread(data + 8, 1, (size_t)st.st_size, file) == (size_t)st.st_size;
    fclose(file);
    valid = valid && has_truetype_outlines(data + 8, (size_t)st.st_size);
    if (!valid) { free(data); return false; }
    font_memory = data; font_size = (uint32_t)st.st_size;
    if (atexit(close_fonts)) { close_fonts(); return false; }
    fprintf(stderr, "FONT host substitute loaded bytes=%u\n", font_size);
    return true;
}

static void initialize_clock(void) {
    if (clock_initialized) return;
    struct { int64_t base; uint8_t source[16]; } steady = {0};
    arc4random_buf(steady.source, sizeof steady.source);
    struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts);
    struct { int64_t offset, timestamp; uint8_t source[16]; } system = {.offset = time(NULL) - ts.tv_sec, .timestamp = ts.tv_sec};
    memcpy(system.source, steady.source, 16);
    // libnx/SDK double-buffered clock contexts; both snapshots are initially identical.
    memcpy(clock_page + 8, &steady, sizeof steady); memcpy(clock_page + 8 + sizeof steady, &steady, sizeof steady);
    for (unsigned offset = 0x38; offset <= 0x80; offset += 0x48) {
        memcpy(clock_page + offset + 8, &system, sizeof system);
        memcpy(clock_page + offset + 8 + sizeof system, &system, sizeof system);
    }
    // The v1.4.3 SDK reads the continuous-adjustment context at 0xd0, not just the legacy steady context.
    struct { int64_t point, rate, shift, lower, upper; uint8_t source[16]; } adjustment = {0};
    memcpy(adjustment.source, steady.source, 16);
    memcpy(clock_page + 0xd8, &adjustment, sizeof adjustment);
    memcpy(clock_page + 0xd8 + sizeof adjustment, &adjustment, sizeof adjustment);
    clock_initialized = true;
}

static bool calendar_time(int64_t timestamp, uint8_t out[32]) {
    time_t seconds = timestamp; struct tm calendar;
    if (!localtime_r(&seconds, &calendar) || calendar.tm_year < -1900 || calendar.tm_year > INT16_MAX - 1900) return false;
    memset(out, 0, 32); uint16_t year = calendar.tm_year + 1900;
    memcpy(out, &year, 2); out[2] = calendar.tm_mon + 1; out[3] = calendar.tm_mday;
    out[4] = calendar.tm_hour; out[5] = calendar.tm_min; out[6] = calendar.tm_sec;
    int32_t weekday = calendar.tm_wday, yearday = calendar.tm_yday, dst = calendar.tm_isdst > 0, offset = (int32_t)calendar.tm_gmtoff;
    memcpy(out + 8, &weekday, 4); memcpy(out + 12, &yearday, 4);
    if (calendar.tm_zone) memcpy(out + 16, calendar.tm_zone, strnlen(calendar.tm_zone, 7));
    memcpy(out + 24, &dst, 4); memcpy(out + 28, &offset, 4);
    return true;
}

const char *service_blocker(void) { return blocker; }
static bool blocked(const char *name) { snprintf(blocker, sizeof blocker, "%s", name); return false; }

static void close_romfs(void) {
    if (romfs_fd >= 0) {
        fprintf(stderr, "ROMFS reads=%llu bytes=%llu\n", romfs_reads, romfs_bytes);
        close(romfs_fd);
    }
    romfs_fd = -1;
}
static void close_romfs_on_exit(void) {
    // Fatal exits can originate on another guest thread while a storage read is in progress.
    pthread_mutex_lock(&services_lock);
    close_romfs();
}
static bool open_romfs(void) {
    if (romfs_fd >= 0) return true;
    const char *path = getenv("ZELDA_ROMFS");
    int fd = open(path && *path ? path : "work/romfs.bin", O_RDONLY | O_CLOEXEC | O_NONBLOCK);
    if (fd < 0) return blocked("merged RomFS unavailable (ZELDA_ROMFS)");
    struct stat stat; uint64_t header[10];
    bool valid = !fstat(fd, &stat) && S_ISREG(stat.st_mode) && stat.st_size >= 0x50 &&
        pread(fd, header, sizeof header, 0) == sizeof header && header[0] == 0x50;
    if (valid) {
        for (unsigned i = 1; i < 9; i += 2) {
            valid &= header[i] >= 0x50 && header[i] <= (uint64_t)stat.st_size &&
                header[i + 1] <= (uint64_t)stat.st_size - header[i];
            for (unsigned j = 1; j < i; j += 2)
                if (header[i] < header[j] + header[j + 1] && header[j] < header[i] + header[i + 1]) valid = false;
        }
        valid &= header[4] >= 24 && header[9] >= 0x50 && header[9] <= (uint64_t)stat.st_size;
    }
    if (!valid) { close(fd); return blocked("invalid merged RomFS header or table bounds"); }
    // Structural checks cannot establish the game version; supply the merged v1.4.3 image.
    romfs_fd = fd; romfs_size = (uint64_t)stat.st_size;
    if (atexit(close_romfs_on_exit)) { close_romfs(); return blocked("RomFS cleanup registration failed"); }
    fprintf(stderr, "ROMFS opened bytes=%llu\n", romfs_size);
    return true;
}

static struct session *session(uint32_t handle) {
    for (size_t i = 0; i < sizeof sessions / sizeof *sessions; i++)
        if (sessions[i].handle == handle && sessions[i].context) return &sessions[i];
    return NULL;
}

static uint32_t open_session(unsigned kind, struct service_context *shared) {
    if (next_handle == UINT32_MAX) return 0;
    for (size_t i = 0; i < sizeof sessions / sizeof *sessions; i++) if (!sessions[i].context) {
        struct service_context *context = shared ? shared : calloc(1, sizeof *context);
        if (!context) return 0;
        if (!shared) context->kind = kind;
        context->refs++;
        sessions[i] = (struct session){.handle = next_handle++, .context = context};
        return sessions[i].handle;
    }
    return 0;
}

static void amiibo_close(unsigned value);
static void close_object(unsigned kind, unsigned value, local_file *file) {
    if (kind == NFP_USER) amiibo_close(value);
    if (kind == AUD_RENDERER) audio_close();
    filesystem_close(file);
    if (kind == SAVE_FS || kind == TEMP_FS || kind == CACHE_FS) filesystem_unmount(value);
}
static bool close_session(uint32_t handle) {
    struct session *s = session(handle);
    if (!s) return false;
    if (!--s->context->refs) {
        if (!s->context->domain) close_object(s->context->kind, s->context->value, s->context->file);
        else for (unsigned i = 0; i < 16; i++) close_object(s->context->objects[i], s->context->values[i], s->context->files[i]);
        free(s->context);
    }
    memset(s, 0, sizeof *s);
    return true;
}

static bool connect_port(const char *name, uint32_t *handle) {
    if (strcmp(name, "sm:")) return blocked("unknown named port");
    *handle = open_session(SM, NULL);
    return *handle ? true : blocked("service session table full");
}

static uint32_t word(const uint8_t *buffer, size_t offset) {
    uint32_t value;
    memcpy(&value, buffer + offset, sizeof value);
    return value;
}
static uint64_t wide(const uint8_t *buffer, size_t offset) {
    uint64_t value; memcpy(&value, buffer + offset, sizeof value); return value;
}
static void mapped_buffer(const uint8_t *buffer, size_t offset, uint64_t *address, uint64_t *length) {
    uint32_t high = word(buffer, offset + 8);
    *length = word(buffer, offset) | ((uint64_t)((high >> 24) & 15) << 32);
    *address = word(buffer, offset + 4) | ((uint64_t)(high >> 28) << 32) | ((uint64_t)((high >> 2) & 0x3fffff) << 36);
}

static bool reply(uint8_t *bytes, size_t size, bool tipc, uint32_t token, uint32_t moved,
                  bool domain, uint32_t object,
                  const void *payload, size_t length) {
    size_t raw = moved ? 16 : 8, header = tipc ? raw : (raw + 15) & ~(size_t)15;
    size_t body = tipc ? 4 : 16, domain_header = domain ? 16 : 0;
    size_t end = (header + domain_header + body + length + (object ? 4 : 0) + 3) & ~(size_t)3;
    if (end > size) return false;
    uint32_t out[64] = {0};
    if (end > sizeof out) return false;
    out[1] = (uint32_t)((end - raw) / 4) | (moved ? 0x80000000 : 0);
    if (moved) { out[2] = 0x20; out[3] = moved; }
    if (domain) { out[header / 4] = object ? 1 : 0; header += domain_header; }
    if (!tipc) { out[header / 4] = 0x4f434653; out[header / 4 + 3] = token; }
    if (length) memcpy((uint8_t *)out + header + body, payload, length);
    if (object) memcpy((uint8_t *)out + header + body + length, &object, 4);
    memcpy(bytes, out, end);
    return true;
}

static bool reply_status(uint8_t *bytes, size_t size, bool tipc, uint32_t token, bool domain, uint32_t status) {
    if (!reply(bytes, size, tipc, token, 0, domain, 0, NULL, 0)) return false;
    memcpy(bytes + (tipc ? 8 : domain ? 40 : 24), &status, sizeof status);
    return true;
}

#include "amiibo_service.inc"

static bool request(uint32_t handle, void *buffer, size_t size, uint32_t *result) {
    struct session *entry = session(handle);
    if (!entry) { *result = INVALID_HANDLE; return true; }
    if (entry->closed) { *result = 0xf601; return true; }
    struct service_context *s = entry->context;
    *result = INVALID_MESSAGE;
    if (!buffer || size < 8) return true;
    uint8_t *bytes = buffer;
    uint32_t h0 = word(bytes, 0), h1 = word(bytes, 4);
    unsigned type = h0 & 0xffff;
    if (type == 2 || type == 15) { entry->closed = true; *result = 0; return true; }
    bool tipc = type >= 16, control = type == 5 || type == 7;
    if (!tipc && !control && type != 4 && type != 6) return blocked("unsupported IPC type");
    unsigned statics = (h0 >> 16) & 15, sends = (h0 >> 20) & 15, receives = (h0 >> 24) & 15, exchanges = h0 >> 28;
    unsigned receive_mode = (h1 >> 10) & 15;
    if (receive_mode == 1 || receive_mode == 2) return blocked("IPC automatic receive list");
    size_t start = 8;
    bool pid = false;
    uint32_t copied = 0, transfer = 0;
    if (h1 >> 31) {
        if (size < 12) return true;
        uint32_t special = word(bytes, 8);
        if (special & ~0x1fu || ((special >> 1) & 15) > 2) return blocked("IPC handle descriptors");
        pid = special & 1;
        start += 4 + (pid ? 8 : 0);
        for (unsigned i = 0; i < ((special >> 1) & 15); i++) {
            if (start > size || size - start < 4) return true;
            uint32_t handle = word(bytes, start); start += 4;
            uint64_t address, length;
            if (handle == 0xffff8001 && !copied) copied = handle;
            else if (!transfer && kernel_transfer_memory(handle, &address, &length)) transfer = handle;
            else return blocked("IPC unknown copied handle");
        }
    }
    size_t descriptors = start;
    start += statics * 8 + (sends + receives + exchanges) * 12;
    size_t data_size = (h1 & 1023) * 4, payload = start;
    if (start > size || data_size > size - start) return true;
    uint64_t receive_address = 0, receive_length = 0;
    for (unsigned i = 0; receive_mode > 2 && i < receive_mode - 2; i++) {
        size_t offset = start + data_size + i * 8;
        if (offset > size || size - offset < 8) return true;
        uint64_t descriptor = wide(bytes, offset), length = descriptor >> 48;
        if (length) {
            if (receive_length) return blocked("IPC multiple receive-static buffers");
            receive_address = descriptor & 0xffffffffffffull; receive_length = length;
        }
    }
    uint32_t command = type - 16, token = 0;
    bool domain = s->domain && !control && !tipc;
    unsigned kind = s->kind;
    uint32_t *object_value = &s->value;
    local_file *file = s->file, *opened_file = NULL;
    unsigned open_kind = 0, opened_value = 0;
    if (!tipc) {
        size_t aligned = (start + 15) & ~(size_t)15;
        if (aligned - start > data_size || data_size - (aligned - start) < 16) return true;
        if (domain) {
            uint32_t d0 = word(bytes, aligned), object = word(bytes, aligned + 4);
            if (!object || object > 16 || !s->objects[object - 1]) return true;
            if (d0 == 2 && !pid && !copied && !statics && !sends && !receives && !exchanges) {
                close_object(s->objects[object - 1], s->values[object - 1], s->files[object - 1]); s->files[object - 1] = NULL;
                s->objects[object - 1] = 0; s->values[object - 1] = 0;
                memset(bytes, 0, 8); *result = 0; return true;
            }
            if ((d0 & 0xffff) != 1) return blocked("unsupported domain request");
            size_t domain_size = d0 >> 16;
            if (domain_size < 16 || domain_size > start + data_size - aligned - 16) return true;
            kind = s->objects[object - 1];
            file = s->files[object - 1];
            object_value = &s->values[object - 1];
            aligned += 16;
            data_size = aligned + domain_size - start;
        }
        if (word(bytes, aligned) != 0x49434653 || word(bytes, aligned + 4) > 1) return true;
        command = word(bytes, aligned + 8); token = word(bytes, aligned + 12);
        payload = aligned + 16;
        data_size -= payload - start;
    }
    if ((copied || transfer) && (control || command != 0 ||
        !((kind == APPLET && copied && !transfer) || (kind == AUD_MANAGER && copied && transfer)))) {
        fprintf(stderr, "IPC handle request service=%s command=%u pid=%u process=%u transfer=%u bytes=%zu\n",
                kind_names[kind], command, pid, !!copied, !!transfer, data_size);
        return blocked("IPC unexpected copied handles");
    }
    if (!control && kind == NFP_USER && ((command != 19 && command != 20) || getenv("ZELDA_AMIIBO_TRACE")))
        fprintf(stderr, "AMIIBO IPC command=%u initialized=%u X=%u A=%u B=%u C=%llu input=%zu\n",
                command, *object_value != 0, statics, sends, receives, (unsigned long long)receive_length, data_size);
    if (receive_length && (control || !((kind == LAUNCH_ACCESSOR && command == 11) || (kind == NFP_USER && (command == 2 || (command >= 13 && command <= 16))) || (kind == TIME && command == 400)))) return blocked("IPC receive-static buffer for unsupported command");
    if (!control && kind == AUD_DEVICE && getenv("ZELDA_IPC_LOG")) fprintf(stderr, "AUDIO device command=%u X=%u A=%u B=%u W=%u\n", command, statics, sends, receives, exchanges);
    if (!control && kind == LOGGER && command == 0 && statics <= 1 && sends <= 1 && !receives && !exchanges) {
        uint64_t address = 0, length = 0;
        if (statics) {
            uint32_t d = word(bytes, descriptors);
            length = d >> 16;
            address = word(bytes, descriptors + 4) | ((uint64_t)((d >> 12) & 15) << 32) | ((uint64_t)((d >> 6) & 63) << 36);
        }
        if (sends) {
            size_t offset = descriptors + statics * 8;
            uint64_t n, mapped; mapped_buffer(bytes, offset, &mapped, &n);
            if (n) {
                if (length) return true; // AutoSelect uses either the static or mapped buffer.
                length = n;
                address = mapped;
            }
        }
        if (length < 24 || length > 65536 || address > UINT64_MAX - length) return true;
        uint8_t *packet = malloc((size_t)length);
        if (!packet) { *result = 0xce01; return true; }
        mach_vm_size_t got = 0;
        if (mach_vm_read_overwrite(mach_task_self(), address, length, (mach_vm_address_t)packet, &got) || got != length) {
            free(packet); *result = 0xe601; return true;
        }
        bool valid = word(packet, 20) == length - 24;
        if (valid && reply(bytes, size, tipc, token, 0, domain, 0, NULL, 0)) {
            // Log payload can contain paths and user data; retain only packet metadata.
            static unsigned packets;
            if (packets++ < 8) fprintf(stderr, "GUEST LOG severity=%u bytes=%llu\n", packet[18], length - 24);
            *result = 0;
        }
        free(packet);
        return true;
    }
    if (!control && kind == AOC && command == 3 && !statics && !sends && receives == 1 && !exchanges) {
        if (!pid || data_size < 16) return true;
        uint64_t address, capacity; mapped_buffer(bytes, descriptors, &address, &capacity);
        if (capacity % 4 || word(bytes, payload + 4) > capacity / 4 || address > UINT64_MAX - capacity) return true;
        const uint32_t count = 0; // This title's local resources contain no installed add-on content.
        if (reply(bytes, size, tipc, token, 0, domain, 0, &count, sizeof count)) *result = 0;
        return true;
    }
    if (!control && kind == ROM_STORAGE && command == 0 && !statics && !sends && receives == 1 && !exchanges) {
        if (data_size < 16) return true;
        uint64_t address, capacity; mapped_buffer(bytes, descriptors, &address, &capacity);
        uint64_t offset = wide(bytes, payload), length = wide(bytes, payload + 8);
        if (offset > romfs_size || length > romfs_size - offset || length > capacity || length > UINT64_MAX - address) return true;
        // IPC also runs on small guest audio stacks; the services mutex serializes this scratch buffer.
        static uint8_t chunk[65536];
        for (uint64_t done = 0; done < length;) {
            size_t count = length - done < sizeof chunk ? (size_t)(length - done) : sizeof chunk;
            ssize_t got = pread(romfs_fd, chunk, count, (off_t)(offset + done));
            if (got < 0 && errno == EINTR) continue;
            if (got <= 0) return blocked("RomFS read failed or image changed");
            dm_guest_write_prepare((void *)(address + done), (size_t)got);
            if (mach_vm_write(mach_task_self(), address + done, (vm_offset_t)chunk, (mach_msg_type_number_t)got)) {
                *result = 0xe601; return true;
            }
            done += (uint64_t)got;
        }
        romfs_reads++; romfs_bytes += length;
        if (reply(bytes, size, tipc, token, 0, domain, 0, NULL, 0)) *result = 0;
        return true;
    }
    if (!control && kind == AUD_DEVICE && (command == 7 || command == 8) && statics <= 1 && sends <= 1 &&
        (statics || sends) && !receives && !exchanges) {
        uint64_t address = 0, length = 0;
        if (statics) {
            uint32_t d = word(bytes, descriptors); length = d >> 16;
            address = word(bytes, descriptors + 4) | ((uint64_t)((d >> 12) & 15) << 32) | ((uint64_t)((d >> 6) & 63) << 36);
        }
        if (sends) {
            uint64_t mapped, mapped_length; mapped_buffer(bytes, descriptors + statics * 8, &mapped, &mapped_length);
            if (mapped_length) {
                if (length) return true;
                address = mapped; length = mapped_length;
            }
        }
        char name[256]; mach_vm_size_t got;
        if (length < sizeof name || mach_vm_read_overwrite(mach_task_self(), address, sizeof name, (mach_vm_address_t)name, &got) || got != sizeof name || !memchr(name, 0, sizeof name)) return true;
        unsigned device = 0; while (device < 6 && strcmp(name, audio_devices[device])) device++;
        if (device == 6) return blocked("unknown audio output device");
        if (command == 7) {
            float volume;
            if (data_size < sizeof volume) return true;
            memcpy(&volume, bytes + payload, sizeof volume);
            if (getenv("ZELDA_IPC_LOG")) fprintf(stderr, "AUDIO device=%u volume=%g\n", device, (double)volume);
            if (!isfinite(volume) || volume < 0 || volume > 128) return true;
            device_volume[device] = volume;
            if (device == 0 || device == 3) audio_volume(volume);
        }
        if (reply(bytes, size, tipc, token, 0, domain, 0, &device_volume[device], command == 8 ? 4 : 0)) *result = 0;
        return true;
    }
    if (!control && kind == AUD_DEVICE && (command == 0 || command == 3 || command == 6 || command == 10 || command == 13 || command == 14) &&
        !statics && !sends && receives == 1 && !exchanges) {
        uint64_t address, capacity; mapped_buffer(bytes, descriptors, &address, &capacity);
        char names[3][256] = {{0}};
        uint32_t count = 1;
        if (command == 0 || command == 6 || command == 14) {
            count = 3;
            for (unsigned i = 0; i < count; i++) strcpy(names[i], audio_devices[i]);
        } else strcpy(names[0], "AudioTvOutput");
        if (capacity < sizeof names[0]) return true;
        if (count > capacity / sizeof names[0]) count = (uint32_t)(capacity / sizeof names[0]);
        if (mach_vm_write(mach_task_self(), address, (vm_offset_t)names, count * sizeof names[0])) { *result = 0xe601; return true; }
        bool list = command == 0 || command == 6 || command == 14;
        if (reply(bytes, size, tipc, token, 0, domain, 0, &count, list ? 4 : 0)) *result = 0;
        return true;
    }
    if (!control && kind == AUD_RENDERER && command == 4 && !statics && sends == 1 && receives == 2 && !exchanges) {
        uint64_t address, length, destination, capacity;
        mapped_buffer(bytes, descriptors, &address, &length);
        mapped_buffer(bytes, descriptors + 12, &destination, &capacity);
        if (length < 64 || length > (4 << 20) || capacity < 64 || capacity > (4 << 20)) return true;
        uint8_t *input = malloc(length), *output = malloc(capacity); mach_vm_size_t got;
        if (!input || !output) { free(input); free(output); *result = 0xce01; return true; }
        if (mach_vm_read_overwrite(mach_task_self(), address, length, (mach_vm_address_t)input, &got) || got != length) {
            free(input); free(output); *result = 0xe601; return true;
        }
        size_t written = audio_update(input, length, output, capacity); free(input);
        if (!written) { free(output); return blocked("unsupported audio update layout or voice parameters"); }
        kern_return_t status = mach_vm_write(mach_task_self(), destination, (vm_offset_t)output, (mach_msg_type_number_t)written);
        free(output);
        if (status) { *result = 0xe601; return true; }
        if (reply(bytes, size, tipc, token, 0, domain, 0, NULL, 0)) *result = 0;
        return true;
    }
    if (!control && kind == LAUNCH_ACCESSOR && command == 11 && !statics && !sends && receives <= 1 && !exchanges) {
        if (data_size < 8) return true;
        uint64_t address = receive_address, length = receive_length;
        if (receives) {
            uint64_t mapped, mapped_length; mapped_buffer(bytes, descriptors, &mapped, &mapped_length);
            if (mapped_length) {
                if (length) return true;
                address = mapped; length = mapped_length;
            }
        }
        uint64_t offset = wide(bytes, payload);
        if (offset > sizeof preselected_user || length > sizeof preselected_user - offset) return true;
        if (length && mach_vm_write(mach_task_self(), address, (vm_offset_t)((const uint8_t *)preselected_user + offset), (mach_msg_type_number_t)length)) { *result = 0xe601; return true; }
        if (reply(bytes, size, tipc, token, 0, domain, 0, NULL, 0)) *result = 0;
        return true;
    }
    if (!control && kind == CACHE_READER && command == 0 && !statics && !sends && receives == 1 && !exchanges) {
        uint64_t address, capacity; mapped_buffer(bytes, descriptors, &address, &capacity);
        uint8_t infos[FILESYSTEM_CACHE_COUNT][0x60] = {{0}};
        uint64_t count = 0; unsigned next = *object_value;
        while (next < FILESYSTEM_CACHE_COUNT && count < capacity / sizeof infos[0]) {
            uint64_t storage_size;
            uint32_t status = filesystem_cache_info(next, &storage_size);
            if (status == 0x202) { next++; continue; }
            if (status) { if (reply_status(bytes, size, tipc, token, domain, status)) *result = 0; return true; }
            uint64_t id = next + 1, program = 0x0100f2c0115b6000ull; uint16_t index = next++;
            memcpy(infos[count], &id, 8); infos[count][8] = 1; infos[count][9] = 5;
            memcpy(infos[count] + 0x28, &program, 8); memcpy(infos[count] + 0x30, &storage_size, 8);
            memcpy(infos[count++] + 0x38, &index, 2);
        }
        if (count) {
            dm_guest_write_prepare((void *)address, count * sizeof infos[0]);
            if (mach_vm_write(mach_task_self(), address, (vm_offset_t)infos, (mach_msg_type_number_t)(count * sizeof infos[0]))) { *result = 0xe601; return true; }
        }
        if (reply(bytes, size, tipc, token, 0, domain, 0, &count, sizeof count)) { *object_value = next; *result = 0; }
        return true;
    }
    if (!control && (kind == SAVE_FS || kind == TEMP_FS || kind == CACHE_FS) && command <= 8 && statics == (command == 5 || command == 6 ? 2u : 1u) && !sends && !receives && !exchanges) {
        char paths[2][0x301] = {{0}};
        for (unsigned i = 0; i < statics; i++) {
            uint32_t d = word(bytes, descriptors + i * 8); size_t length = d >> 16;
            uint64_t address = word(bytes, descriptors + i * 8 + 4) | ((uint64_t)((d >> 12) & 15) << 32) | ((uint64_t)((d >> 6) & 63) << 36);
            mach_vm_size_t got;
            if (!length || length > sizeof paths[0]) return true;
            if (mach_vm_read_overwrite(mach_task_self(), address, length, (mach_vm_address_t)paths[i], &got) || got != length) { *result = 0xe601; return true; }
            if (!memchr(paths[i], 0, length)) return true;
        }
        if ((command == 0 && (data_size < 16 || word(bytes, payload))) || (command == 8 && data_size < 4)) return true;
        uint64_t value = 0, argument = command == 0 ? wide(bytes, payload + 8) : command == 8 ? word(bytes, payload) : 0;
        uint32_t status = filesystem_path(*object_value, command, paths[0], paths[1], argument, &opened_file, &value);
        if (status) { if (reply_status(bytes, size, tipc, token, domain, status)) *result = 0; return true; }
        if (opened_file) { open_kind = LOCAL_FILE; goto create_object; }
        if (reply(bytes, size, tipc, token, 0, domain, 0, &value, command == 7 ? 4 : 0)) *result = 0;
        return true;
    }
    if (!control && kind == LOCAL_FILE && command <= 1 && !statics && !exchanges && sends == command && receives == 1 - command) {
        if (data_size < 24 || word(bytes, payload) > 1) return true;
        uint64_t address, capacity; mapped_buffer(bytes, descriptors, &address, &capacity);
        uint64_t length = wide(bytes, payload + 16), offset = wide(bytes, payload + 8);
        if (length > capacity || length > (4 << 20)) return true;
        uint8_t *data = malloc(length ? length : 1); mach_vm_size_t got;
        if (!data) { *result = 0xce01; return true; }
        if (command && length && (mach_vm_read_overwrite(mach_task_self(), address, length, (mach_vm_address_t)data, &got) || got != length)) { free(data); *result = 0xe601; return true; }
        uint64_t count = 0;
        uint32_t status = filesystem_file(file, command, offset, data, (size_t)length, &count);
        if (!status && !command && count) {
            dm_guest_write_prepare((void *)address, (size_t)count);
            if (mach_vm_write(mach_task_self(), address, (vm_offset_t)data, (mach_msg_type_number_t)count)) { free(data); *result = 0xe601; return true; }
        }
        free(data);
        if (!status && command && word(bytes, payload)) status = filesystem_file(file, 2, 0, NULL, 0, NULL);
        if (status) { if (reply_status(bytes, size, tipc, token, domain, status)) *result = 0; }
        else if (reply(bytes, size, tipc, token, 0, domain, 0, &count, command ? 0 : 8)) *result = 0;
        return true;
    }
    // ClockSnapshot comparisons used by periodic benefits, including amiibo rewards.
    if (!control && kind == TIME && (command == 500 || command == 501) &&
        statics == 2 && !sends && !receives && !exchanges) {
        uint8_t snapshots[2][0xd0];
        for (unsigned i = 0; i < 2; i++) {
            size_t at = descriptors + i * 8;
            uint32_t d = word(bytes, at);
            if ((d >> 16) != sizeof snapshots[i]) return true;
            uint64_t address = word(bytes, at + 4) | ((uint64_t)((d >> 12) & 15) << 32) |
                               ((uint64_t)((d >> 6) & 63) << 36);
            mach_vm_size_t read_size = 0;
            if (mach_vm_read_overwrite(mach_task_self(), address, sizeof snapshots[i],
                                      (mach_vm_address_t)snapshots[i], &read_size) || read_size != sizeof snapshots[i]) {
                *result = 0xe601; return true;
            }
        }
        const uint8_t *a = snapshots[0], *b = snapshots[1];
        uint32_t status = 0;
        __int128 seconds = 0;
        if (command == 501) {
            if (!memcmp(a + 0x98, b + 0x98, 16))
                seconds = (__int128)(int64_t)wide(b, 0x90) - (int64_t)wide(a, 0x90);
            else if (wide(a, 0x48) && wide(b, 0x48))
                seconds = (__int128)(int64_t)wide(b, 0x48) - (int64_t)wide(a, 0x48);
            else status = (200 << 9) | 116; // TimeNotFound: no comparable clock.
        } else if (!memcmp(a + 0x10, b + 0x10, 16)) {
            // A manual wall-clock change is the change in context offset.
            bool corrected = a[0xcc] && b[0xcc] &&
                (!memcmp(a + 0x30, a + 0x98, 16) || !memcmp(b + 0x30, b + 0x98, 16));
            if (!corrected) seconds = (__int128)(int64_t)wide(b, 0) - (int64_t)wide(a, 0);
        }
        __int128 ns = seconds * 1000000000;
        if (ns > INT64_MAX || ns < INT64_MIN) status = (201 << 9) | 116;
        int64_t span = status ? 0 : (int64_t)ns;
        fprintf(stderr, "CLOCK comparison command=%u status=%x span_ns=%lld\n", command, status, (long long)span);
        if (status) { if (reply_status(bytes, size, tipc, token, domain, status)) *result = 0; }
        else if (reply(bytes, size, tipc, token, 0, domain, 0, &span, sizeof span)) *result = 0;
        return true;
    }
    if ((h0 >> 16) && !(kind == NFP_USER && !control && command == 0 && sends == 1 && !statics && !receives && !exchanges)) {
        snprintf(blocker, sizeof blocker, "%s buffers command=%u X=%u A=%u B=%u W=%u", kind_names[kind], command, statics, sends, receives, exchanges);
        return false;
    }
    if (control && command == 0) {
        if (s->domain) return blocked("repeated domain conversion");
        const uint32_t root = 1;
        if (reply(bytes, size, false, token, 0, false, 0, &root, sizeof root)) {
            s->domain = true; s->objects[0] = s->kind; s->values[0] = s->value;
            s->files[0] = s->file; s->file = NULL; *result = 0;
            fprintf(stderr, "SERVICE domain enabled kind=%u\n", s->kind);
        }
        return true;
    }
    if (control && command == 3) {
        // Paths, NFC enumeration and clock snapshots use the SDK's pointer-buffer budget.
        const uint16_t pointer_buffer_size = kind == FS || kind == SAVE_FS || kind == TEMP_FS || kind == CACHE_FS || kind == LOCAL_FILE || kind == NFP || kind == NFP_USER || kind == TIME ? 0x1000 : 0;
        if (reply(bytes, size, false, token, 0, false, 0, &pointer_buffer_size, sizeof pointer_buffer_size)) *result = 0;
        return true;
    }
    if (control && (command == 2 || command == 4)) {
        if (command == 4 && data_size < 4) return true;
        uint32_t clone = open_session(0, s);
        if (!clone) { *result = 0xce01; return true; }
        if (reply(bytes, size, false, token, clone, false, 0, NULL, 0)) *result = 0;
        else close_session(clone);
        return true;
    }
    if (!control && kind == SM && command == 0 && pid && (tipc || data_size >= 8)) {
        if (s->registered) return blocked("repeated SM registration");
        if (reply(bytes, size, tipc, token, 0, domain, 0, NULL, 0)) {
            s->registered = true; *result = 0;
            fprintf(stderr, "SERVICE sm registered protocol=%s\n", tipc ? "TIPC" : "CMIF");
        }
        return true;
    }
    if (!control && (kind == SAVE_FS || kind == TEMP_FS || kind == CACHE_FS) && command == 10) {
        if (reply_status(bytes, size, tipc, token, domain, filesystem_commit(*object_value))) *result = 0;
        return true;
    }
    if (!control && kind == LOCAL_FILE && command >= 2 && command <= 4) {
        if (command == 3 && data_size < 8) return true;
        uint64_t value = 0;
        uint32_t status = filesystem_file(file, command, command == 3 ? wide(bytes, payload) : 0, NULL, 0, &value);
        if (status) { if (reply_status(bytes, size, tipc, token, domain, status)) *result = 0; }
        else if (reply(bytes, size, tipc, token, 0, domain, 0, &value, command == 4 ? 8 : 0)) *result = 0;
        return true;
    }
    if (!control && kind == AUDIO && command <= 2) {
        if (!command) {
            float levels[2];
            if (data_size < sizeof levels) return true;
            memcpy(levels, bytes + payload, sizeof levels);
            for (unsigned i = 0; i < 2; i++) if (!isfinite(levels[i]) || levels[i] < 0 || levels[i] > 1) return true;
            memcpy(expected_volume, levels, sizeof levels);
        }
        if (reply(bytes, size, tipc, token, 0, domain, 0, expected_volume + (command == 2), command ? 4 : 0)) *result = 0;
        return true;
    }
    if (!control && kind == LAUNCH_STORAGE && command == 0) open_kind = LAUNCH_ACCESSOR;
    if (!control && kind == LAUNCH_ACCESSOR && command == 0) {
        const uint64_t length = sizeof preselected_user;
        if (reply(bytes, size, tipc, token, 0, domain, 0, &length, sizeof length)) *result = 0;
        return true;
    }
    if (!control && kind == SM && command == 1 && s->registered && data_size >= 8) {
        const char *allowed = NULL;
        for (size_t i = 0; i < sizeof allowed_services / sizeof *allowed_services; i++) {
            char name[8] = {0}; memcpy(name, allowed_services[i], strlen(allowed_services[i]));
            if (!memcmp(bytes + payload, name, 8)) { allowed = allowed_services[i]; break; }
        }
        if (!allowed) {
            if (reply_status(bytes, size, tipc, token, domain, 0x1015)) *result = 0;
            return true;
        }
        const unsigned services[] = {LM, APM, APPLET, AOC, ECTX, FS, PCTL, TIME, ACCOUNT, FONTS, AUD_MANAGER, OPUS_MANAGER, PREPO, NFP};
        for (size_t i = 0; i < sizeof services / sizeof *services; i++)
            if (!strcmp(allowed, kind_names[services[i]])) { open_kind = services[i]; break; }
        if (!open_kind) return blocked(allowed);
    }
    if (!control && kind == LM && command == 0 && pid && data_size >= 8) open_kind = LOGGER;
    if (!control && kind == NFP && command == 0) open_kind = NFP_USER;
    if (!control && kind == NFP_USER) {
        if (command == 0) {
            if (!pid || data_size < 16 || sends != 1 || statics || receives || exchanges) return true;
            uint64_t address, length; mapped_buffer(bytes, descriptors, &address, &length);
            if (length > 4096 || address > UINT64_MAX - length) return true;
        }
        return amiibo_request(object_value, command, bytes + payload, data_size,
                              receive_address, receive_length, bytes, size, tipc, token, domain, result);
    }
    if (!control && kind == OPUS_MANAGER && (command == 1 || command == 5 || command == 8)) {
        if (data_size < 8) return true;
        uint32_t rate = word(bytes, payload), channels = word(bytes, payload + 4);
        if ((rate != 8000 && rate != 12000 && rate != 16000 && rate != 24000 && rate != 48000) || channels < 1 || channels > 2)
            return blocked("unsupported Opus configuration");
        uint32_t work_size = (uint32_t)(opus_decoder_get_size(channels) + 4095) & ~4095u;
        fprintf(stderr, "OPUS decoder rate=%u channels=%u work=%u\n", rate, channels, work_size);
        if (reply(bytes, size, tipc, token, 0, domain, 0, &work_size, sizeof work_size)) *result = 0;
        return true;
    }
    if (!control && kind == AUD_MANAGER && ((command == 2 && data_size >= 8) || (command == 4 && data_size >= 16))) open_kind = AUD_DEVICE;
    if (!control && kind == AUD_DEVICE && command == 5) {
        const uint32_t channels = 2;
        if (reply(bytes, size, tipc, token, 0, domain, 0, &channels, sizeof channels)) *result = 0;
        return true;
    }
    if (!control && kind == AUD_MANAGER && command == 1) {
        if (data_size < 0x34) return true;
        uint32_t rate = word(bytes, payload), samples = word(bytes, payload + 4), voices = word(bytes, payload + 16);
        if ((rate != 32000 && rate != 48000) || samples != rate / 200 || voices > 1024) return blocked("unsupported audio renderer configuration");
        fprintf(stderr, "AUDIO renderer rate=%u samples=%u voices=%u revision=%x\n", rate, samples, voices, word(bytes, payload + 0x30));
        // Host rendering state is separate from the guest's opaque DSP work area.
        const uint64_t work_size = 0x1000;
        if (reply(bytes, size, tipc, token, 0, domain, 0, &work_size, sizeof work_size)) *result = 0;
        return true;
    }
    if (!control && kind == AUD_MANAGER && command == 0 && pid && copied && transfer && data_size >= 0x48) {
        uint64_t address, length;
        if (!kernel_transfer_memory(transfer, &address, &length) || length != wide(bytes, payload + 0x38)) return true;
        if (!audio_open(bytes + payload, data_size)) return blocked("audio renderer creation failed");
        open_kind = AUD_RENDERER;
    }
    if (!control && kind == AUD_RENDERER && command == 7) {
        uint32_t handle = audio_event();
        if (!handle) { *result = 0xce01; return true; }
        if (reply(bytes, size, tipc, token, handle, domain, 0, NULL, 0)) {
            uint32_t descriptor = 2; memcpy(bytes + 8, &descriptor, 4); *result = 0;
        } else { uint64_t regs[31] = {handle}; kernel_svc(0x16, regs); }
        return true;
    }
    if (!control && kind == AUD_RENDERER && (command <= 3 || command == 9)) {
        uint32_t value = audio_value(command);
        if (reply(bytes, size, tipc, token, 0, domain, 0, &value, sizeof value)) *result = 0;
        return true;
    }
    if (!control && kind == AUD_RENDERER && (command == 5 || command == 6 || command == 8)) {
        if (command == 8 && data_size < 4) return true;
        if (!audio_set(command, command == 8 ? word(bytes, payload) : 0)) return true;
        if (reply(bytes, size, tipc, token, 0, domain, 0, NULL, 0)) *result = 0;
        return true;
    }
    if (!control && kind == FONTS && command <= 4) {
        if (!initialize_fonts()) return blocked("shared font needs TrueType glyf/loca within 17 MiB (ZELDA_SHARED_FONT)");
        if (command < 4) {
            if (data_size < 4 || word(bytes, payload) >= 6) return true;
            uint32_t value = command == 1 ? 1 : command == 2 ? font_size : 8;
            if (reply(bytes, size, tipc, token, 0, domain, 0, &value, command ? 4 : 0)) *result = 0;
        } else {
            uint32_t handle = kernel_readonly_memory(font_memory, FONT_MEMORY_SIZE);
            if (!handle) { *result = 0xce01; return true; }
            if (reply(bytes, size, tipc, token, handle, domain, 0, NULL, 0)) {
                uint32_t descriptor = 2; memcpy(bytes + 8, &descriptor, 4); *result = 0;
            } else { uint64_t regs[31] = {handle}; kernel_svc(0x16, regs); }
        }
        return true;
    }
    if (!control && kind == ACCOUNT && (command == 100 || command == 140 || command == 160)) {
        if (!pid || data_size < 8) return true;
        if (reply(bytes, size, tipc, token, 0, domain, 0, NULL, 0)) { s->registered = true; *result = 0; }
        return true;
    }
    if (!control && kind == ACCOUNT && command == 150 && s->registered) {
        const uint8_t switch_locked = 0;
        if (reply(bytes, size, tipc, token, 0, domain, 0, &switch_locked, sizeof switch_locked)) *result = 0;
        return true;
    }
    if (!control && kind == ACCOUNT && (command == 5 || command == 101) && s->registered) {
        if (data_size < 16 || memcmp(bytes + payload, preselected_user + 2, 16)) return true;
        open_kind = command == 5 ? ACCOUNT_PROFILE : ACCOUNT_MANAGER;
    }
    if (!control && kind == ACCOUNT_PROFILE && command == 1) {
        uint8_t profile[0x38] = {0}; memcpy(profile, preselected_user + 2, 16); memcpy(profile + 24, "Local Player", 13);
        if (reply(bytes, size, tipc, token, 0, domain, 0, profile, sizeof profile)) *result = 0;
        return true;
    }
    if (!control && kind == AOC && command == 50) {
        // No add-on content is installed or mounted in this local title.
        if (reply(bytes, size, tipc, token, 0, domain, 0, NULL, 0)) *result = 0;
        return true;
    }
    if (!control && kind == APM && command == 0) open_kind = APM_SESSION;
    if (!control && kind == TIME && command <= 4) open_kind = USER_CLOCK + command;
    if (!control && kind == PREPO && command == 10400) {
        static uint64_t session_id;
        if (!session_id) { arc4random_buf(&session_id, sizeof session_id); if (!session_id) session_id = 1; }
        if (reply(bytes, size, tipc, token, 0, domain, 0, &session_id, sizeof session_id)) *result = 0;
        return true;
    }
    if (!control && kind == TIMEZONE && command == 101) {
        if (data_size < 8) return true;
        uint8_t out[32]; if (!calendar_time((int64_t)wide(bytes, payload), out)) return true;
        if (reply(bytes, size, tipc, token, 0, domain, 0, out, sizeof out)) *result = 0;
        return true;
    }
    if (!control && kind == TIME && command == 400) {
        if (data_size < 1 || bytes[payload] > 1 || receive_length != 0xd0) return true;
        initialize_clock();
        uint8_t snapshot[0xd0] = {0}, calendar[32];
        memcpy(snapshot, clock_page + 0x40, 32); memcpy(snapshot + 0x20, clock_page + 0x88, 32);
        struct timespec now; if (clock_gettime(CLOCK_MONOTONIC, &now)) return true;
        int64_t steady = now.tv_sec; memcpy(snapshot + 0x90, &steady, 8);
        memcpy(snapshot + 0x98, clock_page + 16, 16);
        for (unsigned i = 0; i < 2; i++) {
            int64_t offset; memcpy(&offset, snapshot + i * 32, 8);
            int64_t timestamp = steady + offset;
            if (!calendar_time(timestamp, calendar)) return true;
            memcpy(snapshot + 0x40 + i * 8, &timestamp, 8);
            memcpy(snapshot + 0x50 + i * 8, calendar, 8);
            memcpy(snapshot + 0x60 + i * 24, calendar + 8, 24);
        }
        char zone_path[1024] = {0}; const char *zone = getenv("TZ");
        if (!zone || !*zone) {
            ssize_t n = readlink("/etc/localtime", zone_path, sizeof zone_path - 1);
            char *name = n > 0 ? strstr(zone_path, "/zoneinfo/") : NULL;
            zone = name ? name + 10 : (const char *)calendar + 16;
        }
        strlcpy((char *)snapshot + 0xa8, zone, 0x24);
        snapshot[0xcd] = bytes[payload]; // Automatic correction stays disabled for this local clock.
        dm_guest_write_prepare((void *)receive_address, sizeof snapshot);
        if (mach_vm_write(mach_task_self(), receive_address, (vm_offset_t)snapshot, sizeof snapshot)) { *result = 0xe601; return true; }
        if (reply(bytes, size, tipc, token, 0, domain, 0, NULL, 0)) *result = 0;
        return true;
    }
    if (!control && kind == TIME && command == 20) {
        initialize_clock();
        uint32_t handle = kernel_readonly_page(clock_page);
        if (!handle) { *result = 0xce01; return true; }
        if (reply(bytes, size, tipc, token, handle, domain, 0, NULL, 0)) {
            uint32_t descriptor = 2; memcpy(bytes + 8, &descriptor, 4); *result = 0;
        } else { uint64_t regs[31] = {handle}; kernel_svc(0x16, regs); }
        return true;
    }
    if (!control && kind == PCTL && (command == 0 || command == 1) && pid && data_size >= 8) open_kind = PCTL_SESSION;
    if (!control && kind == PCTL_SESSION && command == 1) {
        if (reply(bytes, size, tipc, token, 0, domain, 0, NULL, 0)) { s->registered = true; *result = 0; }
        return true;
    }
    if (!control && kind == FS && command == 1 && pid && data_size >= 8) {
        if (reply(bytes, size, tipc, token, 0, domain, 0, NULL, 0)) { s->registered = true; *result = 0; }
        return true;
    }
    if (!control && kind == FS && command == 1005 && s->registered) {
        const uint32_t access_log_mode = 0; // Filesystem path logging is disabled.
        if (reply(bytes, size, tipc, token, 0, domain, 0, &access_log_mode, sizeof access_log_mode)) *result = 0;
        return true;
    }
    if (!control && kind == FS && command == 200 && s->registered) {
        if (!open_romfs()) return false;
        open_kind = ROM_STORAGE;
    }
    if (!control && kind == FS && command == 51 && s->registered) {
        if (data_size < 0x48) return true;
        fprintf(stderr, "SAVE mount space=%u type=%u has_user=%u program=%llx\n", bytes[payload], bytes[payload + 0x28],
            !!(wide(bytes, payload + 0x10) | wide(bytes, payload + 0x18)), wide(bytes, payload + 8));
        bool temporary = bytes[payload] == 3 && bytes[payload + 0x28] == 4;
        bool savedata = bytes[payload] == 1 && bytes[payload + 0x28] == 1 && !memcmp(bytes + payload + 0x10, preselected_user + 2, 16);
        bool cache = bytes[payload] == 1 && bytes[payload + 0x28] == 5;
        uint16_t index; memcpy(&index, bytes + payload + 0x2a, 2);
        uint64_t program = wide(bytes, payload + 8);
        if ((!temporary && !savedata && !cache) || (cache && index >= FILESYSTEM_CACHE_COUNT)) return blocked("unsupported save mount identity");
        if (program && program != 0x0100f2c0115b6000ull) {
            // Only this title has local storage; optional imports from other games are absent.
            if (reply_status(bytes, size, tipc, token, domain, 0x7d402)) *result = 0; // Fs TargetNotFound, not PathNotFound.
            return true;
        }
        opened_value = cache ? FILESYSTEM_CACHE_BASE + index : temporary;
        uint64_t cache_size;
        uint32_t status = cache ? filesystem_cache_info(index, &cache_size) : 0;
        if (!status) status = filesystem_mount(opened_value);
        if (status) { if (reply_status(bytes, size, tipc, token, domain, status)) *result = 0; return true; }
        open_kind = cache ? CACHE_FS : temporary ? TEMP_FS : SAVE_FS;
    }
    if (!control && kind == FS && command == 62 && s->registered) open_kind = CACHE_READER;
    if (!control && kind == FS && command == 33 && s->registered) {
        if (data_size < 2) return true;
        uint16_t index; memcpy(&index, bytes + payload, 2);
        if (reply_status(bytes, size, tipc, token, domain, filesystem_cache_delete(index))) *result = 0;
        return true;
    }
    if (!control && kind == ROM_STORAGE && command == 4) {
        if (reply(bytes, size, tipc, token, 0, domain, 0, &romfs_size, sizeof romfs_size)) *result = 0;
        return true;
    }
    if (!control && kind == APPLET && command == 0 && pid && copied && data_size >= 8) open_kind = APP_PROXY;
    if (!control && kind == APPLICATION && command == 21) {
        // shortcut: use the reference host's Simplified Chinese default until a language setting exists.
        const char language[8] = "zh-Hans";
        if (reply(bytes, size, tipc, token, 0, domain, 0, language, sizeof language)) *result = 0;
        return true;
    }
    if (!control && kind == APPLICATION && command == 22 && data_size >= 4) {
        fprintf(stderr, "APPLICATION terminate result=%x\n", word(bytes, payload));
        if (reply(bytes, size, tipc, token, 0, domain, 0, NULL, 0)) *result = 0;
        return true;
    }
    if (!control && kind == APPLICATION && command == 23) {
        const char version[16] = "1.4.3";
        if (reply(bytes, size, tipc, token, 0, domain, 0, version, sizeof version)) *result = 0;
        return true;
    }
    if (!control && kind == APPLICATION && command == 27) {
        if (data_size < 24) return true;
        uint16_t index; memcpy(&index, bytes + payload, 2);
        uint32_t status = filesystem_cache_create(index, wide(bytes, payload + 8), wide(bytes, payload + 16));
        const struct { uint32_t media, reserved; uint64_t required; } out = {1, 0, 0};
        if (status) { if (reply_status(bytes, size, tipc, token, domain, status)) *result = 0; }
        else if (reply(bytes, size, tipc, token, 0, domain, 0, &out, sizeof out)) *result = 0;
        return true;
    }
    if (!control && kind == APPLICATION && command == 40) {
        const uint8_t ready = 1; // NotifyRunning: this local host has no HOME-menu transition to wait for.
        if (reply(bytes, size, tipc, token, 0, domain, 0, &ready, sizeof ready)) *result = 0;
        return true;
    }
    if (!control && kind == APPLICATION && command == 1 && data_size >= 4) {
        if (word(bytes, payload) == 2 && preselected_user_pending) open_kind = LAUNCH_STORAGE;
        else {
            if (reply_status(bytes, size, tipc, token, domain, 0x480)) *result = 0;
            return true;
        }
    }
    if (!control && kind == APPLICATION && command == 123) {
        const int32_t previous_program = -1; // First launch, not an ExecuteProgram transition.
        if (reply(bytes, size, tipc, token, 0, domain, 0, &previous_program, sizeof previous_program)) *result = 0;
        return true;
    }
    if (!control && kind == APP_PROXY) {
        switch (command) {
            case 0: open_kind = COMMON_STATE; break;
            case 1: open_kind = SELF; break;
            case 2: open_kind = WINDOW; break;
            case 3: open_kind = AUDIO; break;
            case 4: open_kind = DISPLAY; break;
            case 11: open_kind = LIBRARY; break;
            case 20: open_kind = APPLICATION; break;
            case 1000: open_kind = DEBUG; break;
        }
    }
    if (!control && kind == APM && command == 1) {
        const uint32_t mode = 1; // Keep the docked graphics profile for performance comparisons.
        if (reply(bytes, size, tipc, token, 0, domain, 0, &mode, sizeof mode)) *result = 0;
        return true;
    }
    if (!control && ((kind == WINDOW && command == 1) ||
                     (kind == COMMON_STATE && (command == 5 || command == 6 || command == 9)) ||
                     (kind == SELF && command == 90))) {
        // shortcut: applet focus remains foreground; the frontend clears input when its window loses focus.
        uint64_t value = kind == WINDOW ? (uint64_t)getpid() : kind == SELF ? 0 : 1;
        size_t length = kind == WINDOW || kind == SELF ? 8 : command == 6 ? 4 : 1;
        if (reply(bytes, size, tipc, token, 0, domain, 0, &value, length)) *result = 0;
        return true;
    }
    if (!control && kind == COMMON_STATE && command == 900) {
        if (reply(bytes, size, tipc, token, 0, domain, 0, NULL, 0)) {
            request_exit_on_execute = true; *result = 0;
        }
        return true;
    }
    if (!control && kind == COMMON_STATE && command == 66 && data_size >= 4) {
        uint32_t mode = word(bytes, payload);
        if (mode > 2) return blocked("invalid CPU boost mode");
        if (reply(bytes, size, tipc, token, 0, domain, 0, NULL, 0)) { cpu_boost_mode = mode; *result = 0; }
        return true;
    }
    if (!control && kind == WINDOW && command == 10) {
        if (reply(bytes, size, tipc, token, 0, domain, 0, NULL, 0)) { applet_state.foreground = true; *result = 0; }
        return true;
    }
    if (!control && kind == SELF && (command == 62 || command == 63)) {
        if (command == 62) {
            if (data_size < 4 || word(bytes, payload) > 2) return true;
            // The local guest has no inactivity suspension; retain its policy for readback.
            applet_state.idle_extension = word(bytes, payload);
        }
        if (reply(bytes, size, tipc, token, 0, domain, 0, &applet_state.idle_extension, command == 63 ? 4 : 0)) *result = 0;
        return true;
    }
    if (!control && kind == SELF && (command == 11 || command == 12 || command == 13 || command == 14 || command == 16)) {
        size_t count = command == 13 ? 3 : 1;
        if (data_size < count) return true;
        uint8_t flags[3] = {0};
        for (size_t i = 0; i < count; i++) flags[i] = bytes[payload + i] & 1;
        if (reply(bytes, size, tipc, token, 0, domain, 0, NULL, 0)) {
            if (command == 11) applet_state.operation_notification = flags[0];
            if (command == 12) applet_state.performance_notification = flags[0];
            if (command == 13) memcpy(applet_state.focus_handling, flags, 3);
            if (command == 14) applet_state.restart_notification = flags[0];
            if (command == 16) applet_state.suspend_out_of_focus = flags[0];
            *result = 0;
        }
        return true;
    }
    if (!control && kind == COMMON_STATE && command == 1) {
        const uint32_t message = 15; // Initial FocusStateChanged notification from the applet manager.
        if (reply(bytes, size, tipc, token, 0, domain, 0, &message, sizeof message)) {
            if (!initial_focus_pending) {
                const uint32_t no_messages = 0x680;
                memcpy(bytes + (tipc ? 8 : domain ? 40 : 24), &no_messages, 4);
            } else {
                initial_focus_pending = false;
                if (notifications[1].write) { uint64_t regs[31] = {notifications[1].write}; kernel_svc(0x12, regs); }
            }
            *result = 0;
        }
        return true;
    }
    if (!control && ((kind == SELF && command == 91) || (kind == COMMON_STATE && command == 0) ||
                     (kind == APPLICATION && (command == 130 || command == 210)) ||
                     (kind == AUD_DEVICE && (command == 4 || command == 11 || command == 12)) || (kind == NFP_USER && command == 23))) {
        // The selected operation mode is fixed for this launch, so its notification event stays unsignaled.
        unsigned index = kind == NFP_USER ? 7 : kind == AUD_DEVICE ? (command == 4 ? 4 : command == 11 ? 5 : 6) :
                         kind == SELF ? 0 : kind == COMMON_STATE ? 1 : command == 210 ? 3 : 2;
        uint32_t *w = &notifications[index].write, *r = &notifications[index].read;
        if (!*r && !kernel_event_create(w, r)) { *result = 0xce01; return true; }
        if (index == 1 && initial_focus_pending) { uint64_t regs[31] = {*w}; kernel_svc(0x11, regs); }
        uint32_t copy = kernel_event_copy(*r);
        if (!copy) { *result = 0xce01; return true; }
        if (reply(bytes, size, tipc, token, copy, domain, 0, NULL, 0)) {
            uint32_t descriptor = 2; // One copied handle, rather than a moved service session.
            memcpy(bytes + 8, &descriptor, sizeof descriptor); *result = 0;
        } else { uint64_t regs[31] = {copy}; kernel_svc(0x16, regs); }
        return true;
    }
    if (!control && kind == APM_SESSION && command <= 1 && data_size >= (command ? 4u : 8u)) {
        uint32_t mode = word(bytes, payload);
        if (mode > 1) return blocked("invalid performance mode");
        if (!command) {
            uint32_t config = word(bytes, payload + 4);
            if (reply(bytes, size, tipc, token, 0, domain, 0, NULL, 0)) {
                performance_config[mode] = config; performance_config_set[mode] = true; *result = 0;
            }
        } else {
            if (!performance_config_set[mode]) return blocked("unconfigured performance mode");
            if (reply(bytes, size, tipc, token, 0, domain, 0, &performance_config[mode], 4)) *result = 0;
        }
        return true;
    }
create_object:
    if (open_kind) {
        if (domain) {
            for (unsigned i = 0; i < 16; i++) if (!s->objects[i]) {
                if (reply(bytes, size, false, token, 0, true, i + 1, NULL, 0)) {
                    if (open_kind == LAUNCH_STORAGE) preselected_user_pending = false;
                    s->objects[i] = open_kind; *result = 0;
                    s->files[i] = opened_file; s->values[i] = opened_value;
                    fprintf(stderr, "SERVICE object %s\n", kind_names[open_kind]);
                }
                else close_object(open_kind, opened_value, opened_file);
                return true;
            }
            close_object(open_kind, opened_value, opened_file); *result = 0xce01; return true;
        }
        uint32_t child = open_session(open_kind, NULL);
        if (!child) { close_object(open_kind, opened_value, opened_file); *result = 0xce01; return true; }
        session(child)->context->file = opened_file;
        session(child)->context->value = opened_value;
        if (reply(bytes, size, tipc, token, child, false, 0, NULL, 0)) {
            if (open_kind == LAUNCH_STORAGE) preselected_user_pending = false;
            *result = 0;
            fprintf(stderr, "SERVICE opened %s\n", kind_names[open_kind]);
        }
        else close_session(child);
        return true;
    }
    snprintf(blocker, sizeof blocker, "%s %s command=%u", kind_names[kind],
             control ? "control" : "request", command);
    return false;
}

bool service_connect(const char *name, uint32_t *handle) {
    pthread_mutex_lock(&services_lock);
    bool handled = connect_port(name, handle);
    pthread_mutex_unlock(&services_lock);
    return handled;
}
bool service_close(uint32_t handle) {
    pthread_mutex_lock(&services_lock);
    bool handled = close_session(handle);
    pthread_mutex_unlock(&services_lock);
    return handled;
}
bool service_request(uint32_t handle, void *buffer, size_t size, uint32_t *result) {
    pthread_mutex_lock(&services_lock);
    bool handled = request(handle, buffer, size, result);
    if (handled && *result && getenv("ZELDA_IPC_LOG")) {
        unsigned command = UINT32_MAX;
        for (size_t p = 8; p + 12 <= size; p += 4) if (word(buffer, p) == 0x49434653) { command = word(buffer, p + 8); break; }
        fprintf(stderr, "IPC rejected command=%u result=%x\n", command, *result);
    }
    pthread_mutex_unlock(&services_lock);
    return handled;
}
