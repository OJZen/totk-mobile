// Included by services.c; all reader state is protected by services_lock.
// IPC ABI: switchbrew/libnx nx/include/switch/services/nfc.h and nfc.c.
#include "amiibo_tag.h"
enum { NFP_IDLE, NFP_SEARCHING, NFP_FOUND, NFP_REMOVED, NFP_MOUNTED };
enum { NFP_DEVICE = (64 << 9) | 115, NFP_ARGUMENT = (65 << 9) | 115,
       NFP_STATE = (73 << 9) | 115, NFP_NO_TAG = (97 << 9) | 115 };
static const uint64_t amiibo_reader_handle = 1;
static struct amiibo_reader {
    bool used;
    unsigned state, mount;
    uint64_t seen;
    amiibo_tag tag;
    struct { uint32_t write, read; } events[3];
} amiibo_readers[16];
static amiibo_tag amiibo_selected;
static bool amiibo_present;
static uint64_t amiibo_revision;

static void amiibo_event(struct amiibo_reader *reader, unsigned which, bool signal) {
    if (reader->events[which].write) {
        uint64_t regs[31] = {reader->events[which].write};
        kernel_svc(signal ? 0x11 : 0x12, regs);
    }
}
static void amiibo_found(struct amiibo_reader *reader) {
    if (reader->state == NFP_SEARCHING && amiibo_present && reader->seen != amiibo_revision) {
        reader->tag = amiibo_selected; reader->seen = amiibo_revision; reader->state = NFP_FOUND;
        amiibo_event(reader, 1, false); amiibo_event(reader, 0, true);
        fprintf(stderr, "AMIIBO tag detected\n");
    }
}
static void amiibo_close(unsigned value) {
    if (!value || value > 16) return;
    struct amiibo_reader *reader = &amiibo_readers[value - 1];
    if (!reader->used) return;
    for (unsigned i = 0; i < 3; i++) {
        uint64_t regs[31] = {reader->events[i].write};
        if (regs[0]) kernel_svc(0x16, regs);
        regs[0] = reader->events[i].read;
        if (regs[0]) kernel_svc(0x16, regs);
    }
    memset(reader, 0, sizeof *reader);
}
static void amiibo_remove_locked(void) {
    amiibo_present = false;
    for (unsigned i = 0; i < 16; i++) {
        struct amiibo_reader *r = &amiibo_readers[i];
        if (!r->used) continue;
        if (r->state == NFP_FOUND || r->state == NFP_MOUNTED) {
            r->state = NFP_REMOVED; r->mount = 0;
            amiibo_event(r, 0, false); amiibo_event(r, 1, true);
        }
    }
}
bool service_amiibo_load(const char *path, char *error, size_t capacity) {
    uint8_t bytes[573]; amiibo_tag tag;
    int fd = path ? open(path, O_RDONLY | O_NONBLOCK) : -1;
    FILE *file = fd >= 0 ? fdopen(fd, "rb") : NULL;
    if (fd >= 0 && !file) close(fd);
    const char *problem = NULL;
    if (!file) problem = "Could not open the amiibo file.";
    else {
        struct stat st;
        if (fstat(fileno(file), &st) || !S_ISREG(st.st_mode)) problem = "Select a regular amiibo .bin file.";
        else {
            size_t count = fread(bytes, 1, sizeof bytes, file);
            if (ferror(file) || !amiibo_tag_parse(bytes, count, &tag))
                problem = "Invalid raw amiibo dump. Expected 532, 540 or 572 bytes with a valid NTAG215 UID and model header.";
        }
        fclose(file);
    }
    if (problem) { if (error && capacity) snprintf(error, capacity, "%s", problem); return false; }
    pthread_mutex_lock(&services_lock);
    amiibo_remove_locked(); amiibo_selected = tag; amiibo_present = true;
    if (++amiibo_revision == 0) amiibo_revision = 1;
    for (unsigned i = 0; i < 16; i++) if (amiibo_readers[i].used) amiibo_found(&amiibo_readers[i]);
    pthread_mutex_unlock(&services_lock);
    if (error && capacity) *error = 0;
    fprintf(stderr, "AMIIBO file selected (read-only)\n");
    return true;
}
void service_amiibo_remove(void) {
    pthread_mutex_lock(&services_lock); amiibo_remove_locked(); pthread_mutex_unlock(&services_lock);
}
bool service_amiibo_selected(void) {
    pthread_mutex_lock(&services_lock); bool value = amiibo_present; pthread_mutex_unlock(&services_lock); return value;
}

bool service_amiibo_delivered(void) {
    pthread_mutex_lock(&services_lock);
    bool delivered = false;
    for (unsigned i = 0; i < 16; i++) {
        const struct amiibo_reader *r = &amiibo_readers[i];
        if (r->used && amiibo_present && r->seen == amiibo_revision &&
            (r->state == NFP_FOUND || r->state == NFP_MOUNTED)) delivered = true;
    }
    pthread_mutex_unlock(&services_lock);
    return delivered;
}

static bool amiibo_request(uint32_t *value, uint32_t command, const uint8_t *input, size_t input_size,
                          uint64_t output_address, uint64_t output_size,
                          uint8_t *bytes, size_t size, bool tipc, uint32_t token, bool domain, uint32_t *result) {
    uint32_t status = 0, scalar = 0, copied = 0;
    size_t scalar_size = 0, length = 0;
    uint8_t output[0x100] = {0};
    struct amiibo_reader *r = *value && *value <= 16 ? &amiibo_readers[*value - 1] : NULL;
    if (command == 19) { scalar = r && r->used; scalar_size = 4; }
    else if (command == 0) {
        if (r) status = NFP_STATE;
        else {
            for (unsigned i = 0; i < 16; i++) if (!amiibo_readers[i].used) {
                amiibo_readers[i] = (struct amiibo_reader){.used = true}; *value = i + 1; r = &amiibo_readers[i]; break;
            }
            if (!r) status = NFP_DEVICE;
        }
    } else if (command == 1) { amiibo_close(*value); *value = 0; }
    else if (!r || !r->used) status = NFP_STATE;
    else if (command == 2) {
        if (output_size < 8 || output_size > 80 || output_size % 8) status = NFP_ARGUMENT;
        else { memcpy(output, &amiibo_reader_handle, 8); length = 8; scalar = 1; scalar_size = 4; }
    } else {
        if (command != 23 && (input_size < 8 || wide(input, 0) != amiibo_reader_handle)) status = NFP_DEVICE;
        if (!status) switch (command) {
        case 3:
            if (r->state != NFP_IDLE && r->state != NFP_REMOVED) { status = NFP_STATE; break; }
            r->state = NFP_SEARCHING; r->mount = 0;
            amiibo_event(r, 0, false); amiibo_event(r, 1, false); amiibo_found(r); break;
        case 4:
            if (r->state == NFP_FOUND || r->state == NFP_MOUNTED) amiibo_event(r, 1, true);
            r->state = NFP_IDLE; r->mount = 0; amiibo_event(r, 0, false); break;
        case 5:
            if (input_size < 16 || word(input, 8) != 0 || word(input, 12) != 1) {
                // Only public ROM fields are available; never claim to mount encrypted RAM.
                fprintf(stderr, "AMIIBO unsupported mount target (ROM-only reader)\n");
                status = NFP_ARGUMENT; break;
            }
            if (r->state != NFP_FOUND) { status = r->state == NFP_REMOVED ? NFP_NO_TAG : NFP_STATE; break; }
            r->mount = 1; r->state = NFP_MOUNTED; break;
        case 6:
            if (r->state != NFP_MOUNTED) { status = NFP_STATE; break; }
            r->state = NFP_FOUND; r->mount = 0; break;
        case 13:
            if (r->state != NFP_FOUND && r->state != NFP_MOUNTED) { status = NFP_NO_TAG; break; }
            memcpy(output, r->tag.uid, 7); output[10] = 7;
            output[0x20] = 1; output[0x24] = 2; length = 0x58; break;
        case 16:
            if (r->state != NFP_MOUNTED || !(r->mount & 1)) { status = NFP_STATE; break; }
            memcpy(output, r->tag.model, 7); length = 0x40;
            fprintf(stderr, "AMIIBO model read\n"); break;
        case 17: case 18: case 23: {
            unsigned event = command == 17 ? 0 : command == 18 ? 1 : 2;
            if (!r->events[event].read && !kernel_event_create(&r->events[event].write, &r->events[event].read)) { status = NFP_DEVICE; break; }
            if (event == 0 && (r->state == NFP_FOUND || r->state == NFP_MOUNTED)) amiibo_event(r, event, true);
            if (event == 1 && r->state == NFP_REMOVED) amiibo_event(r, event, true);
            copied = kernel_event_copy(r->events[event].read);
            if (!copied) status = NFP_DEVICE;
            break;
        }
        case 20: scalar = r->state; scalar_size = 4; break;
        case 21: scalar = 0; scalar_size = 4; break; // Existing virtual FullKey controller: Player 1.
        default:
            fprintf(stderr, "AMIIBO unsupported read-only command=%u\n", command);
            status = NFP_STATE; break;
        }
    }
    if (!status && length) {
        if (output_size < length || !output_address || output_address > UINT64_MAX - length) status = NFP_ARGUMENT;
        else {
            dm_guest_write_prepare((void *)output_address, length);
            if (mach_vm_write(mach_task_self(), output_address, (vm_offset_t)output, (mach_msg_type_number_t)length)) {
                *result = 0xe601; return true;
            }
        }
    }
    if (status || (command != 19 && command != 20) || getenv("ZELDA_AMIIBO_TRACE"))
        fprintf(stderr, "AMIIBO reply command=%u status=%x state=%u count=%u\n", command, status, r ? r->state : 0, scalar);
    if (status) { if (reply_status(bytes, size, tipc, token, domain, status)) *result = 0; }
    else if (reply(bytes, size, tipc, token, copied, domain, 0, &scalar, scalar_size)) {
        if (copied) { uint32_t descriptor = 2; memcpy(bytes + 8, &descriptor, 4); }
        *result = 0;
    } else if (copied) { uint64_t regs[31] = {copied}; kernel_svc(0x16, regs); }
    return true;
}
