#pragma once

#include <cstdint>
#include <cstring>

// The sketch guards all mutable session operations with controlLock.
class ControlSession {
public:
    static constexpr unsigned int MAX_CONTROLLER_LENGTH = 48;

    ControlSession(const char* key, uint32_t timeoutMs)
        : key_(key), timeoutMs_(timeoutMs) {}

    bool authenticate(const char* supplied) const {
        const auto length = std::strlen(key_);
        if (length < 32 || length > 96 || supplied == nullptr ||
            std::strlen(supplied) != length) {
            return false;
        }
        unsigned char difference = 0;
        for (unsigned int i = 0; i < length; ++i) {
            if (!validCharacter(key_[i])) return false;
            difference |= static_cast<unsigned char>(key_[i] ^ supplied[i]);
        }
        return difference == 0;
    }

    static bool validController(const char* id) {
        if (id == nullptr || id[0] == '\0') return false;
        unsigned int length = 0;
        for (; id[length] != '\0'; ++length) {
            const char c = id[length];
            if (length >= MAX_CONTROLLER_LENGTH || !validCharacter(c)) {
                return false;
            }
        }
        return true;
    }

    bool connected(uint32_t now) {
        if (open_ && static_cast<uint32_t>(now - lastHeartbeat_) >= timeoutMs_) {
            revoke();
        }
        return open_;
    }

    bool permits(const char* id, uint32_t now) {
        return connected(now) && validController(id) && std::strcmp(owner_, id) == 0;
    }

    bool claim(const char* id, uint32_t now, bool actuatorsIdle) {
        if (!validController(id)) return false;
        const bool live = connected(now);
        if (stopRequested_ || (live && std::strcmp(owner_, id) != 0) ||
            (!live && !actuatorsIdle)) {
            return false;
        }
        std::strcpy(owner_, id);
        lastHeartbeat_ = now;
        open_ = true;
        return true;
    }

    bool heartbeat(const char* id, uint32_t now) {
        if (!permits(id, now)) return false;
        lastHeartbeat_ = now;
        return true;
    }

    void revoke() {
        if (open_) stopRequested_ = true;
        open_ = false;
    }

    bool takeStopRequest(uint32_t now) {
        connected(now);
        const bool requested = stopRequested_;
        stopRequested_ = false;
        return requested;
    }

private:
    static bool validCharacter(char c) {
        return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
            (c >= '0' && c <= '9') || c == '-' || c == '_';
    }

    const char* key_;
    uint32_t timeoutMs_;
    char owner_[MAX_CONTROLLER_LENGTH + 1] = {};
    uint32_t lastHeartbeat_ = 0;
    bool open_ = false;
    bool stopRequested_ = false;
};
