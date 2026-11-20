
#include <algorithm>
#include <cstdlib>
#include <cstring>
#include <string>
#include <map>
#include <utility>
#include "ControlSession.h"
#define AGV_DEVICE_KEY "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
#include <array>
#include <atomic>
#include <cassert>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <initializer_list>
using portMUX_TYPE = int;
constexpr int portMUX_INITIALIZER_UNLOCKED = 0;
void portENTER_CRITICAL(int*) {}
void portEXIT_CRITICAL(int*) {}
#include <vector>
using std::min;
using std::max;
int constrain(int value, int low, int high) { return std::clamp(value, low, high); }
unsigned long ms = 0;
unsigned long millis() { return ms; }
constexpr int LOW = 0;
constexpr int HIGH = 1;
int gpioLevels[40] = {};
unsigned int rearReadCount = 0;
int digitalRead(uint8_t pin) {
    ++rearReadCount;
    return gpioLevels[pin];
}
void delay(unsigned long duration) { ms += duration; }
struct {
    template<class... T> void printf(T...) {}
    template<class T> void print(T) {}
    template<class T> void println(T) {}
} Serial;
constexpr int WL_CONNECTED = 1;
struct {
    bool connected = true;
    int status() { return connected ? WL_CONNECTED : 0; }
    int localIP() { return 0; }
} WiFi;
std::vector<uint32_t> servoDuties;
bool ledcWrite(uint8_t pin, uint32_t duty) {
    assert(pin == 23);
    servoDuties.push_back(duty);
    return true;
}
struct {
    std::vector<std::array<uint8_t, 5>> packets;
    void write(const uint8_t* data, size_t length) {
        assert(length == 5);
        packets.push_back({data[0], data[1], data[2], data[3], data[4]});
    }
    void flush() {}
} Serial2;

using esp_err_t = int;
constexpr int ESP_OK = 0, ESP_FAIL = -1, ESP_ERR_NOT_FOUND = -2, ESP_ERR_NO_MEM = -3;
constexpr int HTTP_GET = 0, HTTP_POST = 1;
constexpr int HTTPD_400_BAD_REQUEST = 400, HTTPD_403_FORBIDDEN = 403, HTTPD_500_INTERNAL_SERVER_ERROR = 500;
constexpr int HTTPD_RESP_USE_STRLEN = -1, HTTPD_WS_TYPE_TEXT = 1;
struct httpd_req_t {
    int method = HTTP_POST;
    void* user_ctx = nullptr;
    void* sess_ctx = nullptr;
    void (*free_ctx)(void*) = nullptr;
    std::map<std::string,std::string> headers;
    std::string query, body, input;
    std::string status = "200 OK";
    int socket = 1;
};
struct httpd_ws_frame_t {
    int type = HTTPD_WS_TYPE_TEXT;
    size_t len = 0;
    uint8_t* payload = nullptr;
};
struct String : std::string {
    using std::string::string;
    String(int value) : std::string(std::to_string(value)) {}
    void replace(const char* from, const String& to) {
        size_t offset = 0;
        while ((offset = find(from, offset)) != npos) {
            std::string::replace(offset, std::strlen(from), to);
            offset += to.size();
        }
    }
};
int copyHttpValue(const std::string& value, char* output, size_t size) {
    if (value.size() >= size) return ESP_FAIL;
    std::memcpy(output,value.c_str(),value.size()+1);
    return ESP_OK;
}
int httpd_req_get_hdr_value_str(httpd_req_t* request,const char* key,char* output,size_t size) {
    auto entry=request->headers.find(key);
    if(entry==request->headers.end()) return ESP_ERR_NOT_FOUND;
    return copyHttpValue(entry->second,output,size);
}
int httpd_req_get_url_query_str(httpd_req_t* request,char* output,size_t size) {
    if(request->query.empty()) return ESP_ERR_NOT_FOUND;
    return copyHttpValue(request->query,output,size);
}
int httpd_query_key_value(const char* raw,const char* key,char* output,size_t size) {
    const std::string query(raw), prefix=std::string(key)+"=";
    size_t offset=0;
    while(offset<query.size()) {
        const size_t end=query.find('&',offset);
        const auto part=query.substr(offset,end==std::string::npos?end:end-offset);
        if(part.rfind(prefix,0)==0) return copyHttpValue(part.substr(prefix.size()),output,size);
        if(end==std::string::npos) break;
        offset=end+1;
    }
    return ESP_ERR_NOT_FOUND;
}
int httpd_resp_set_status(httpd_req_t* request,const char* value) {request->status=value;return ESP_OK;}
int httpd_resp_set_type(httpd_req_t*,const char*) {return ESP_OK;}
int httpd_resp_set_hdr(httpd_req_t*,const char*,const char*) {return ESP_OK;}
int httpd_resp_send(httpd_req_t* request,const char* data,int length) {
    request->body=data?std::string(data,length<0?std::strlen(data):static_cast<size_t>(length)):"";
    return ESP_OK;
}
int httpd_resp_sendstr(httpd_req_t* request,const char* data) {return httpd_resp_send(request,data,-1);}
int httpd_resp_send_err(httpd_req_t* request,int code,const char* data) {
    request->status=std::to_string(code);
    return httpd_resp_sendstr(request,data);
}
int httpd_req_to_sockfd(httpd_req_t* request) {return request->socket;}
int httpd_ws_recv_frame(httpd_req_t* request,httpd_ws_frame_t* frame,size_t size) {
    frame->len=request->input.size();
    if(size) {
        if(frame->len>size) return ESP_FAIL;
        std::memcpy(frame->payload,request->input.data(),frame->len);
    }
    return ESP_OK;
}
int httpd_ws_send_frame(httpd_req_t*,httpd_ws_frame_t*) {return ESP_OK;}
const char CONTROL_PAGE[]="", RC_PAGE[]="";
