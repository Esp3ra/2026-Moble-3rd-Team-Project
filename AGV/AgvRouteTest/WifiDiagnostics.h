#pragma once
#include <cstdint>
#include <cstring>

// Fixed-size POD storage; caller synchronizes access from the Wi-Fi task,
// HTTP task and main loop. No credentials, heap allocation or I/O here.
class WifiDiagnostics {
public:
    static constexpr uint32_t MAGIC = 0x41475632;
    static constexpr unsigned CAPACITY = 24;
    static constexpr int UNKNOWN_RSSI = -32768;
    enum Kind : uint8_t { Boot, GotIp, Disconnected, LostIp, HeartbeatExpired };
    struct Event {
        uint32_t sequence, boot_number, uptime_ms;
        int32_t reason, rssi_dbm;
        Kind kind;
    };
    struct Store {
        uint32_t magic, boot_number, next_sequence, head, count;
        int32_t reset_reason, last_rssi_dbm;
        Event events[CAPACITY];
    };

    explicit WifiDiagnostics(Store& store) : store_(store) {}
    void begin(int resetReason, uint32_t now, bool retain) {
        if (!retain || store_.magic != MAGIC || store_.head >= CAPACITY ||
                store_.count > CAPACITY || store_.next_sequence == 0) {
            std::memset(&store_, 0, sizeof(store_));
            store_.magic = MAGIC;
            store_.next_sequence = 1;
        }
        ++store_.boot_number;
        store_.reset_reason = resetReason;
        store_.last_rssi_dbm = UNKNOWN_RSSI;
        record(Boot, now, resetReason);
    }
    void setRssi(int rssi) { store_.last_rssi_dbm = rssi; }
    void record(Kind kind, uint32_t now, int reason = 0) {
        store_.events[store_.head] = {store_.next_sequence++, store_.boot_number,
                                     now, reason, store_.last_rssi_dbm, kind};
        store_.head = (store_.head + 1) % CAPACITY;
        if (store_.count < CAPACITY) ++store_.count;
    }
    Store snapshot() const { return store_; }
    static const Event& oldest(const Store& snapshot, unsigned index) {
        return snapshot.events[(snapshot.head + CAPACITY - snapshot.count + index) % CAPACITY];
    }
    static const char* name(Kind kind) {
        switch (kind) {
            case Boot: return "BOOT";
            case GotIp: return "GOT_IP";
            case Disconnected: return "WIFI_DISCONNECTED";
            case LostIp: return "LOST_IP";
            case HeartbeatExpired: return "HEARTBEAT_EXPIRED";
        }
        return "UNKNOWN";
    }
private:
    Store& store_;
};
