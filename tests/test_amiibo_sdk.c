// Real v1.4.3 SDK NFC calls, without loading a save or starting gameplay.
#define main probe_main
#include "../host/probe.c"
#undef main
#include "../host/codec.c"
#include "../host/services.h"
#include <assert.h>
int main(int argc, char **argv) {
    assert(argc == 2);
    char *load[] = {argv[0], argv[1], "--load-only"};
    assert(!probe_main(3, load)); initialize_guest(); alarm(20);
    void (*init)(void) = guest_sym("_ZN2nn3nfp10InitializeEv");
    uint32_t (*state)(void) = guest_sym("_ZN2nn3nfp8GetStateEv");
    uint32_t (*list)(uint64_t *, int *, int) = guest_sym("_ZN2nn3nfp11ListDevicesEPNS0_12DeviceHandleEPii");
    uint32_t (*start)(const uint64_t *) = guest_sym("_ZN2nn3nfp14StartDetectionERKNS0_12DeviceHandleE");
    uint32_t (*device_state)(const uint64_t *) = guest_sym("_ZN2nn3nfp14GetDeviceStateERKNS0_12DeviceHandleE");
    uint32_t (*get_tag)(void *, const uint64_t *) = guest_sym("_ZN2nn3nfp10GetTagInfoEPNS0_7TagInfoERKNS0_12DeviceHandleE");
    uint32_t (*mount)(const uint64_t *, uint32_t, uint32_t) = guest_sym("_ZN2nn3nfp5MountERKNS0_12DeviceHandleENS0_9ModelTypeENS0_11MountTargetE");
    uint32_t (*get_model)(void *, const uint64_t *) = guest_sym("_ZN2nn3nfp12GetModelInfoEPNS0_9ModelInfoERKNS0_12DeviceHandleE");
    assert(init && state && list && start && device_state && get_tag && mount && get_model);
    init(); uint32_t rc; fprintf(stderr, "NFP_SDK state=%u\n", state()); assert(state() == 1);
    uint64_t devices[10] = {0}; int count = -1;
    rc = list(devices, &count, 10);
    fprintf(stderr, "NFP_SDK list=%x count=%d first=%llu\n", rc, count, (unsigned long long)devices[0]);
    assert(!rc && count == 1 && devices[0]);
    uint32_t (*attach)(void *, const uint64_t *) = guest_sym("_ZN2nn3nfp19AttachActivateEventEPNS_2os15SystemEventTypeERKNS0_12DeviceHandleE");
    uint32_t (*npad)(uint32_t *, const uint64_t *) = guest_sym("_ZN2nn3nfp9GetNpadIdEPjRKNS0_12DeviceHandleE");
    void (*destroy_event)(void *) = guest_sym("_ZN2nn2os18DestroySystemEventEPNS0_15SystemEventTypeE");
    assert(attach && npad && destroy_event);
    _Alignas(16) uint8_t event[0x100] = {0};
    assert(!attach(event, devices));
    uint32_t player = UINT32_MAX; assert(!npad(&player, devices) && player == 0);
    assert(!service_amiibo_delivered());
    rc = start(devices); fprintf(stderr, "NFP_SDK start=%x state=%u\n", rc, device_state(devices)); assert(!rc && device_state(devices) == 1);
    unsigned char data[540] = {4, 1, 2, 0x8f, 3, 4, 5, 6, 4};
    memcpy(data + 12, "\xf1\x10\xff\xee", 4);
    memcpy(data + 0x54, "\x01\x02\x03\x00\x04\x05\x09\x02", 8);
    char path[] = "work/tmp/amiibo-sdk-XXXXXX", error[256]; int fd = mkstemp(path); assert(fd >= 0);
    assert(write(fd, data, sizeof data) == sizeof data); close(fd);
    assert(service_amiibo_load(path, error, sizeof error)); unlink(path);
    assert(device_state(devices) == 2 && service_amiibo_delivered());
    uint8_t tag[0x68], model[0x50]; memset(tag, 0xa5, sizeof tag); memset(model, 0xa5, sizeof model);
    rc = get_tag(tag, devices); fprintf(stderr, "NFP_SDK tag=%x uid_length=%u\n", rc, tag[10]);
    assert(!rc && tag[0] == 4 && tag[10] == 7 && tag[0x58] == 0xa5);
    rc = mount(devices, 0, 1); fprintf(stderr, "NFP_SDK mount=%x\n", rc); assert(!rc);
    rc = get_model(model, devices); fprintf(stderr, "NFP_SDK model=%x\n", rc);
    assert(!rc && model[0] == 2 && model[1] == 1 && model[3] == 9 && model[4] == 5 && model[0x40] == 0xa5);
    service_amiibo_remove(); assert(device_state(devices) == 3);
    assert(!service_amiibo_delivered()); destroy_event(event);
    call("_ZN2nn3nfp8FinalizeEv"); assert(state() == 0);
    alarm(0); puts("Real SDK amiibo enumerate, detect, ROM mount, tag/model read and remove passed; no save written");
}
