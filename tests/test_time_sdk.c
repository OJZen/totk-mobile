// Real v1.4.3 SDK clock readers must agree with the host, without starting or saving a game.
#define main probe_main
#include "../host/probe.c"
#undef main
#include "../host/codec.c"
#include <assert.h>

int main(int argc, char **argv) {
    assert(argc == 2);
    char *load[] = {argv[0], argv[1], "--load-only"};
    assert(!probe_main(3, load)); initialize_guest(); alarm(20);
    call("_ZN2nn4time10InitializeEv");
    const char *readers[] = {
        "_ZN2nn4time23StandardUserSystemClock14GetCurrentTimeEPNS0_9PosixTimeE",
        "_ZN2nn4time26StandardNetworkSystemClock14GetCurrentTimeEPNS0_9PosixTimeE",
    };
    for (unsigned i = 0; i < 2; i++) {
        uint32_t (*read_clock)(int64_t *) = guest_sym(readers[i]); assert(read_clock);
        int64_t stamp = -1, before = time(NULL);
        uint32_t result = read_clock(&stamp);
        fprintf(stderr, "CLOCK_SDK reader=%u result=%x guest=%lld host=%lld\n", i, result, stamp, (int64_t)time(NULL));
        assert(!result && stamp >= before - 1 && stamp <= time(NULL) + 1);
    }
    uint32_t (*to_calendar)(void *, void *, const int64_t *) = guest_sym(
        "_ZN2nn4time14ToCalendarTimeEPNS0_12CalendarTimeEPNS0_22CalendarAdditionalInfoERKNS0_9PosixTimeE");
    void (*create_snapshot)(void *) = guest_sym("_ZN2nn4time13ClockSnapshot29CreateWithStandardSystemClockEPS1_");
    assert(to_calendar && create_snapshot);
    uint8_t calendar[8], extra[24], snapshot[0xd0];
    int64_t stamp = time(NULL); time_t seconds = stamp; struct tm local;
    assert(localtime_r(&seconds, &local));
    assert(!to_calendar(calendar, extra, &stamp));
    int16_t year; memcpy(&year, calendar, 2);
    int32_t offset; memcpy(&offset, extra + 20, 4);
    assert(year == local.tm_year + 1900 && calendar[2] == local.tm_mon + 1 && calendar[3] == local.tm_mday);
    assert(calendar[4] == local.tm_hour && calendar[5] == local.tm_min && calendar[6] == local.tm_sec && offset == local.tm_gmtoff);
    create_snapshot(snapshot); memcpy(&stamp, snapshot + 0x40, 8);
    assert(llabs(stamp - time(NULL)) <= 1);
    bool (*span_between)(int64_t *, const void *, const void *) = guest_sym(
        "_ZN2nn4time20CalculateSpanBetweenEPNS_8TimeSpanERKNS0_13ClockSnapshotES5_");
    int64_t (*manual_difference)(const void *, const void *) = guest_sym(
        "_ZN2nn4time48CalculateStandardUserSystemClockDifferenceByUserERKNS0_13ClockSnapshotES3_");
    assert(span_between && manual_difference);
    uint8_t later[0xd0]; memcpy(later, snapshot, sizeof later);
    int64_t span = -1, later_steady; memcpy(&later_steady, snapshot + 0x90, 8);
    later_steady += 86400; memcpy(later + 0x90, &later_steady, 8);
    assert(span_between(&span, snapshot, later) && span == 86400000000000LL);
    assert(span_between(&span, later, snapshot) && span == -86400000000000LL);
    assert(span_between(&span, snapshot, snapshot) && span == 0);
    assert(manual_difference(snapshot, later) == 0);
    int64_t changed_offset; memcpy(&changed_offset, later, 8); changed_offset += 3600;
    memcpy(later, &changed_offset, 8);
    assert(manual_difference(snapshot, later) == 3600000000000LL);
    later[0x98] ^= 1; // Different boot: use network timestamps.
    int64_t network; memcpy(&network, snapshot + 0x48, 8); network += 120;
    memcpy(later + 0x48, &network, 8);
    assert(span_between(&span, snapshot, later) && span == 120000000000LL);
    memset(later + 0x48, 0, 8);
    assert(!span_between(&span, snapshot, later));
    memcpy(later, snapshot, sizeof later); later_steady = INT64_MAX;
    memcpy(later + 0x90, &later_steady, 8);
    assert(!span_between(&span, snapshot, later));
    alarm(0);
    puts("Real SDK user/network clocks, local calendar, snapshots and amiibo clock comparisons passed; no save written");
}
