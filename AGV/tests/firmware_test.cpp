
struct Mark {
    Node node;
    uint8_t mask;
    int turn;
};
constexpr uint8_t F_CENTER = 0x0c, F_LEFT = 0x0d, F_RIGHT = 0x2c, F_BOTH = 0x3f;
constexpr const char* OWNER = "server-a";

httpd_req_t request(bool authorized = true, const char* controller = OWNER) {
    httpd_req_t result;
    if (authorized) result.headers["Authorization"] = std::string("Bearer ") + CONTROL_KEY;
    result.headers["X-AGV-Controller"] = controller;
    return result;
}

void fixture() {
    ms += 10000;
    controlSession = ControlSession(CONTROL_KEY, SERVER_HEARTBEAT_TIMEOUT_MS);
    wifiDiagnostics.begin(1, ms, false);
    lastDiagnosticSerialSequence = 0;
    pendingCommand.store(0);
    pendingOperationId[0] = activeOperationId[0] = '\0';
    strcpy(deviceBootId, "d7645267-f6a5-42f9-9918-5a591ea74301");
    rcMode.store(false);
    requestedMotion = Motion::Stop;
    rcStopPending = false;
    forkMoving.store(false);
    forkAngle = 85;
    servoAngle.store(85);
    location = POINT_2;
    destination = NO_NODE;
    inspectionTrip = false;
    lineMissing = false;
    serverReady = true;
    WiFi.connected = true;
    frontMask = F_CENTER;
    drive(0, 0, true);
    publish(State::Ready);
    auto req = request();
    controlSessionRequest(&req);
    assert(req.status == "204 No Content");
}

void healthyTime(unsigned long elapsed) {
    while (elapsed > 500) {
        ms += 500;
        assert(controlSession.heartbeat(OWNER, ms));
        elapsed -= 500;
    }
    ms += elapsed;
    assert(controlSession.heartbeat(OWNER, ms));
}

void tick(uint8_t mask, unsigned long elapsed = CONTROL_INTERVAL_MS) {
    healthyTime(elapsed);
    if (reversing) updateTrip(Sample{}, ms, mask);
    else updateTrip(sample(mask), ms, 0);
}

void place(Node from, Node to) {
    location = from;
    publish(State::Ready);
    startTrip(to, ms);
    assert(state == State::Moving);
}

void forward(std::initializer_list<Mark> marks) {
    assert(!reversing);
    for (const auto& mark : marks) {
        assert(route[nextIndex] == mark.node && phase == Phase::Follow);
        tick(F_CENTER);
        tick(F_CENTER);
        tick(mark.mask);
        tick(mark.mask);
        if (mark.turn < 0) {
            assert(state == State::Arrived && location == mark.node);
        } else if (mark.turn == 0) {
            tick(F_CENTER, CENTER_MS);
            assert(phase == Phase::Cross);
            tick(F_CENTER);
            tick(F_CENTER);
            assert(phase == Phase::Follow);
        } else {
            assert(nextTurn() == mark.turn);
            tick(F_CENTER, CENTER_MS);
            assert(phase == Phase::Turn);
            tick(0, TURN_MIN_MS);
            tick(F_CENTER);
            tick(F_CENTER);
            if (mark.node >= JUNCTION_8 && mark.node <= JUNCTION_11) {
                assert(phase == Phase::ExitTurn);
                tick(F_CENTER, mark.node == JUNCTION_8 ? JUNCTION_9_DETECT_DELAY_MS : CONTROL_INTERVAL_MS);
                for (unsigned int i = 1; i < TURN_EXIT_STABLE_READS; ++i) tick(F_CENTER);
            }
            assert(phase == Phase::Follow);
        }
    }
}

void backward(std::initializer_list<Mark> marks) {
    assert(reversing);
    for (const auto& mark : marks) {
        assert(route[nextIndex] == mark.node && phase == Phase::Follow);
        tick(4);
        tick(4);
        tick(mark.mask);
        if (mark.turn >= 0) tick(mark.mask);
        if (mark.turn < 0) {
            assert(state == State::Arrived && location == mark.node);
        } else if (mark.turn == 0) {
            assert(phase == Phase::Cross);
            tick(4);
            tick(4);
            assert(phase == Phase::Follow);
        } else {
            assert(nextTurn() == mark.turn);
            tick(4, REVERSE_CENTER_MS);
            assert(phase == Phase::Turn);
            tick(0, TURN_MIN_MS);
            tick(4);
            tick(4);
            assert(phase == Phase::Follow);
        }
    }
}

void controlAndTimeoutTests() {
    fixture();
    auto unauth = request(false);
    unauth.headers["X-AGV-Control"] = "1";
    unauth.user_ctx = &goRequest;
    unauth.query = "point=1";
    handleCommand(&unauth);
    assert(unauth.status == "401 Unauthorized" && pendingCommand == 0);
    auto outsider = request(true, "server-b");
    outsider.user_ctx = &servoRequest;
    outsider.query = "angle=110";
    handleCommand(&outsider);
    assert(outsider.status == "409 Conflict" && pendingCommand == 0);
    auto otherHeartbeat = request(true, "server-b");
    heartbeatRequest(&otherHeartbeat);
    assert(otherHeartbeat.status == "409 Conflict");
    auto go = request();
    go.user_ctx = &goRequest;
    go.query = "point=1";
    handleCommand(&go);
    assert(go.status == "202 Accepted");
    ms += CONTROL_INTERVAL_MS;
    loop();
    assert(state == State::Moving && motorLeft > 0 && motorRight > 0);
    const auto age = ms;
    auto status = request();
    showStatus(&status);
    assert(status.body.find("\"control_connected\":true") != std::string::npos);
    // GET status and traffic from other clients cannot extend the heartbeat lease.
    ms = age + SERVER_HEARTBEAT_TIMEOUT_MS;
    pendingCommand.store(2);
    loop();
    assert(state == State::ControlLost && motorLeft == 0 && motorRight == 0);
    assert(location == NO_NODE && pendingCommand == 0);
    auto late = request();
    heartbeatRequest(&late);
    assert(late.status == "409 Conflict");
    auto reconnect = request();
    controlSessionRequest(&reconnect);
    assert(reconnect.status == "204 No Content");
    loop();
    assert(motorLeft == 0 && motorRight == 0 && state == State::ControlLost);

    fixture();
    startForkMove(110, ms);
    ms += 800;
    loop();
    assert(forkMoving);
    const float held = forkAngle;
    ms += 1200;
    loop();
    assert(!forkMoving && forkAngle == held && state == State::ControlLost);
    puts("HTTP authorization, controller ownership, heartbeat expiry, pending-command cancellation, and servo hold passed.");
}

void websocketTests() {
    fixture();
    rcMode.store(true);
    auto forbidden = request(false);
    forbidden.method = HTTP_GET;
    forbidden.query = "controller=server-a";
    handleControlSocket(&forbidden);
    assert(forbidden.status == "403 Forbidden");
    auto wrongOwner = request();
    wrongOwner.method = HTTP_GET;
    wrongOwner.query = std::string("key=") + CONTROL_KEY + "&controller=server-b";
    handleControlSocket(&wrongOwner);
    assert(wrongOwner.status == "403 Forbidden");
    auto socket = request();
    socket.method = HTTP_GET;
    socket.query = std::string("key=") + CONTROL_KEY + "&controller=server-a";
    handleControlSocket(&socket);
    assert(socket.status == "200 OK" && socket.sess_ctx != nullptr);
    socket.method = HTTP_POST;
    socket.input = "F80";
    handleControlSocket(&socket);
    const auto issuedAt = ms;
    ms = issuedAt + MOTOR_UPDATE_MS;
    loop();
    assert(motorLeft == 80 && motorRight == 80);
    ms = issuedAt + COMMAND_TIMEOUT_MS + MOTOR_UPDATE_MS;
    loop();
    assert(motorLeft == 0 && motorRight == 0);
    socket.input = "F80";
    healthyTime(100);
    handleControlSocket(&socket);
    ms += SERVER_HEARTBEAT_TIMEOUT_MS;
    assert(handleControlSocket(&socket) == ESP_FAIL);
    loop();
    assert(motorLeft == 0 && motorRight == 0);
    socket.free_ctx(socket.sess_ctx);
    fixture();
    rcMode.store(true);
    socket = request();
    socket.method = HTTP_GET;
    socket.query = std::string("key=") + CONTROL_KEY + "&controller=server-a";
    handleControlSocket(&socket);
    socket.method = HTTP_POST;
    socket.input = "F80";
    frameReadDelay = SERVER_HEARTBEAT_TIMEOUT_MS;
    assert(handleControlSocket(&socket) == ESP_FAIL);
    assert(requestedMotion == Motion::Stop);
    loop();
    assert(motorLeft == 0 && motorRight == 0);
    socket.free_ctx(socket.sess_ctx);
    puts("Unauthorized WebSocket handshake rejection, authenticated RC, RC timeout, and expired-session frame rejection passed.");
}

void inspectionAndRouteTests() {
    constexpr Mark out8{JUNCTION_8, F_LEFT, 3};
    constexpr Mark out9{JUNCTION_9, F_LEFT, 3};
    constexpr Mark through9{JUNCTION_9, F_LEFT, 0};
    constexpr Mark back9{JUNCTION_9, 7, 1};
    constexpr Mark back9North{JUNCTION_9, 5, 0};
    constexpr Mark back8{JUNCTION_8, 7, 1};
    constexpr Mark stop2{POINT_2, 7, -1};
    struct RouteCase {
        Node target;
        std::initializer_list<Mark> out;
        std::initializer_list<Mark> back;
    };
    const RouteCase cases[] = {
        {POINT_3, {out9,{JUNCTION_10,F_BOTH,3},{BEND_3,F_RIGHT,1},{POINT_3,F_BOTH,-1}},
                  {{BEND_3,6,3},{JUNCTION_10,7,1},back9,back8,stop2}},
        {POINT_4, {out9,{JUNCTION_10,F_BOTH,0},{POINT_4,F_BOTH,-1}},
                  {{JUNCTION_10,7,0},back9,back8,stop2}},
        {POINT_5, {out9,{JUNCTION_10,F_BOTH,1},{BEND_5,F_LEFT,3},{POINT_5,F_BOTH,-1}},
                  {{BEND_5,5,1},{JUNCTION_10,7,3},back9,back8,stop2}},
        {POINT_6, {through9,{JUNCTION_11,F_BOTH,3},{BEND_6,F_RIGHT,1},{POINT_6,F_BOTH,-1}},
                  {{BEND_6,6,3},{JUNCTION_11,5,1},back9North,back8,stop2}},
        {POINT_7, {through9,{JUNCTION_11,F_BOTH,1},{BEND_7,F_LEFT,3},{POINT_7,F_BOTH,-1}},
                  {{BEND_7,5,1},{JUNCTION_11,6,3},back9North,back8,stop2}},
    };
    for (const auto& item : cases) {
        fixture();
        auto inspect = request();
        inspectionRequest(&inspect);
        assert(inspect.status == "410 Gone" && pendingCommand == 0);
        place(POINT_2,item.target);
        forward({out8});
        assert(state == State::Moving && route[nextIndex] == JUNCTION_9);
        forward(item.out);
        place(item.target, POINT_2);
        backward(item.back);
    }
    fixture();
    place(POINT_1,POINT_2);
    backward({{JUNCTION_8,5,0},stop2});
    fixture();
    place(POINT_2,POINT_1);
    forward({{JUNCTION_8,F_LEFT,0},{POINT_1,F_BOTH,-1}});
    puts("Five direct destination routes through 8/9, five returns without an inspection bar, and 1/2 transfer passed.");
}

void sessionEdgeTests() {
    fixture();
    auto stale = request(true, "old-owner");
    queueCommand(&stale, 1);
    assert(stale.status == "409 Conflict" && pendingCommand == 0);
    auto noKey = request(false);
    controlSessionRequest(&noKey);
    assert(noKey.status == "401 Unauthorized");
    noKey = request(false);
    heartbeatRequest(&noKey);
    assert(noKey.status == "401 Unauthorized");
    noKey = request(false);
    noKey.user_ctx = &stopRequest;
    handleCommand(&noKey);
    assert(noKey.status == "401 Unauthorized" && pendingCommand == 0);
    const auto claimedAt = ms;
    ++ms;
    assert(controlSession.heartbeat(OWNER, ms));
    assert(sessionConnected(claimedAt));
    assert(controllerPermitted(OWNER, claimedAt));
    updateControlWatchdog(claimedAt);
    assert(controlSession.connected(ms));
    const auto renewedAt = ms;
    assert(controlSession.connected(renewedAt + 1999));
    assert(!controlSession.connected(renewedAt + 2000));
    assert(controlSession.takeStopRequest(renewedAt + 2000));
    ControlSession blank("",2000);
    assert(!blank.authenticate(CONTROL_KEY));
    ControlSession session(CONTROL_KEY,2000);
    assert(session.authenticate(CONTROL_KEY));
    assert(!session.authenticate("wrong"));
    assert(!session.claim("",0,true));
    assert(session.claim("wrap",UINT32_MAX-1000,true));
    assert(session.heartbeat("wrap",UINT32_MAX-500));
    assert(session.connected(200));
    assert(!session.connected(1600));
    assert(session.takeStopRequest(1600));
    assert(!session.heartbeat("wrap",1600));
    assert(!session.claim("new",1600,false));
    assert(session.claim("new",1600,true));
    assert(!session.permits("wrap",1601));
    puts("Empty-key failure, controller validation, clock wraparound, and explicit reconnect tests passed.");
}

void pauseAndOperationTests() {
    fixture();
    auto denied = request(false);
    pauseRequest(&denied);
    assert(denied.status == "401 Unauthorized");
    denied = request(true,"other");
    pauseRequest(&denied);
    assert(denied.status == "409 Conflict");
    auto resume = request();
    resumeRequest(&resume);
    assert(resume.status == "409 Conflict");

    place(POINT_2,POINT_3);
    startTurn(ms,false,TURN_SPEED);
    sawCenterLeave = true;
    matchReads = 2;
    const auto oldNext = nextIndex;
    const auto oldPhaseAt = phaseAt;
    const auto oldSegmentAt = segmentAt;
    const auto oldLeft = motorLeft, oldRight = motorRight;
    auto pause = request();
    pause.headers["X-AGV-Operation"] = "pause-uuid";
    pauseRequest(&pause);
    loop();
    assert(state == State::Paused && motorLeft == 0 && motorRight == 0);
    assert(strcmp(activeOperationId,"pause-uuid") == 0);
    const auto began = pausedAt;
    healthyTime(5000);
    loop();
    assert(state == State::Paused && nextIndex == oldNext && phase == Phase::Turn);
    auto blocked = request();
    blocked.user_ctx = &goRequest;blocked.query = "point=4";
    handleCommand(&blocked);
    assert(blocked.status == "409 Conflict");
    resume = request();
    resume.headers["X-AGV-Operation"] = "resume-uuid";
    resumeRequest(&resume);
    // Exercise the exact resume function before loop samples another line.
    pendingCommand.store(0);
    const auto resumed = ms;
    resumeTrip(resumed);
    assert(state == State::Moving && phase == Phase::Turn && nextIndex == oldNext);
    assert(sawCenterLeave && matchReads == 2);
    assert(phaseAt == oldPhaseAt + resumed - began);
    assert(segmentAt == oldSegmentAt + resumed - began);
    assert(motorLeft == oldLeft && motorRight == oldRight);

    fixture();
    startForkMove(110,ms);
    healthyTime(400);updateForkMove(ms);
    const auto angle = forkAngle;
    const auto start = forkStartedAt;
    pauseTrip(ms);
    const auto forkPausedAt = ms;
    healthyTime(5000);loop();
    assert(forkAngle == angle && !forkMoving.load());
    resumeTrip(ms);
    assert(forkMoving && forkStartedAt == start + ms - forkPausedAt);
    updateForkMove(ms);
    assert(fabsf(forkAngle-angle) < 0.01f);
    healthyTime(FORK_MOVE_MS);updateForkMove(ms);
    assert(!forkMoving && servoAngle == 110);

    // Watchdog/Wi-Fi cancellation must invalidate a paused route as well.
    fixture();place(POINT_2,POINT_3);pauseTrip(ms);
    ms += SERVER_HEARTBEAT_TIMEOUT_MS;loop();
    assert(state == State::ControlLost && location == NO_NODE);
    resume = request();resumeRequest(&resume);
    assert(resume.status == "409 Conflict");
    fixture();place(POINT_2,POINT_3);pauseTrip(ms);
    WiFi.connected=false;loop();
    assert(state == State::WifiLost && location == NO_NODE);

    fixture();
    auto move = request();
    move.headers["X-AGV-Operation"] = "operation-uuid";
    move.user_ctx=&goRequest;move.query="point=1";
    handleCommand(&move);assert(move.status == "202 Accepted");
    assert(activeOperationId[0] == '\0');
    loop();
    assert(state == State::Moving && strcmp(activeOperationId,"operation-uuid") == 0);
    auto status = request();showStatus(&status);
    assert(status.body.find("\"boot_id\":\"d7645267-f6a5-42f9-9918-5a591ea74301\"") != std::string::npos);
    assert(status.body.find("\"operation_id\":\"operation-uuid\"") != std::string::npos);
    puts("Pause/resume preserves turn/servo/timers; expiry cancels pause; applied operation and boot evidence passed.");
}

void lineLossAndCancelTests() {
    // Healthy heartbeat throughout: firmware must not rely on the server timeout.
    for (int direction = 0; direction < 2; ++direction) {
        fixture();
        place(direction ? POINT_1 : POINT_2, direction ? POINT_2 : POINT_3);
        if (!direction) { lastLineSide = 1; readyForFeature = true; }
        tick(0);
        tick(0, LINE_LOST_MS - 1);
        assert(state == State::Moving);
        tick(0, 1);
        assert(state == State::LineLost && motorLeft == 0 && motorRight == 0);
        assert(sessionConnected(ms));
        assert(location == NO_NODE && destination == NO_NODE);
    }
    // Reacquisition restarts the continuous absence interval.
    fixture();place(POINT_1,POINT_2);
    tick(0);tick(0,9000);tick(4);tick(0);tick(0,9999);
    assert(state == State::Moving);tick(0,1);assert(state == State::LineLost);
    // Phase transitions and normal pauses must not accidentally reset/consume it.
    fixture();place(POINT_1,POINT_2);tick(0);tick(0,4000);
    pauseTrip(ms);healthyTime(20000);resumeTrip(ms);
    tick(0,5999);assert(state == State::Moving);tick(0,1);assert(state == State::LineLost);
    fixture();place(POINT_2,POINT_3);startTurn(ms,false,TURN_SPEED);
    tick(0);tick(0,9999);assert(state == State::Moving);tick(0,1);
    assert(state == State::LineLost && motorLeft == 0 && motorRight == 0);
    // Exercise the real loop using GPIO-backed rear samples and a valid heartbeat.
    fixture();place(POINT_1,POINT_2);
    gpioLevels[REAR_LEFT_PIN] = gpioLevels[REAR_CENTER_PIN] = gpioLevels[REAR_RIGHT_PIN] = !REAR_BLACK_LEVEL;
    healthyTime(REVERSE_CONTROL_INTERVAL_MS);loop();
    healthyTime(9999 - (ms - lineMissingAt));loop();assert(state == State::Moving);
    healthyTime(1);loop(); // A control tick is needed to apply the elapsed deadline.
    healthyTime(REVERSE_CONTROL_INTERVAL_MS);loop();
    assert(state == State::LineLost && motorLeft == 0 && motorRight == 0);
    fixture();startReverseTest();
    updateReverseTest();healthyTime(10000);updateReverseTest();
    assert(state == State::LineLost && motorLeft == 0 && motorRight == 0);
    // Historical inspection waits cannot survive stop or accept a departure.
    fixture();location=NO_NODE;publish(State::Inspecting);
    auto stop=request();stop.user_ctx=&stopRequest;handleCommand(&stop);loop();
    assert(state == State::Stopped && motorLeft == 0 && motorRight == 0);
    auto go=request();go.user_ctx=&goRequest;go.query="point=3";
    handleCommand(&go);assert(go.status == "409 Conflict");
    puts("10-second forward/reverse/turn loss, reacquisition, pause timer, and legacy inspection cancellation passed.");
}

void diagnosticsTests() {
    WifiDiagnostics::Store storage{};
    WifiDiagnostics diagnostics(storage);
    diagnostics.begin(1, 5, false);
    diagnostics.setRssi(-57);
    diagnostics.record(WifiDiagnostics::Disconnected, 100, 201);
    auto snapshot = diagnostics.snapshot();
    assert(snapshot.count == 2 && snapshot.boot_number == 1);
    assert(WifiDiagnostics::oldest(snapshot, 1).reason == 201);
    assert(WifiDiagnostics::oldest(snapshot, 1).rssi_dbm == -57);
    diagnostics.begin(3, 0, true);
    snapshot = diagnostics.snapshot();
    assert(snapshot.boot_number == 2 && snapshot.count == 3);
    assert(snapshot.last_rssi_dbm == WifiDiagnostics::UNKNOWN_RSSI);
    assert(WifiDiagnostics::oldest(snapshot, 1).boot_number == 1);
    for (unsigned i = 0; i < 30; ++i) diagnostics.record(WifiDiagnostics::LostIp, i);
    snapshot = diagnostics.snapshot();
    assert(snapshot.count == 24);
    for (unsigned i = 1; i < snapshot.count; ++i)
        assert(WifiDiagnostics::oldest(snapshot, i).sequence == WifiDiagnostics::oldest(snapshot, i-1).sequence + 1);
    diagnostics.begin(9, 0, false); // Power/brownout: do not claim RTC retention.
    assert(diagnostics.snapshot().count == 1 && diagnostics.snapshot().boot_number == 1);

    fixture();
    wifiDiagnostics.setRssi(-56);
    arduino_event_info_t info{};
    info.wifi_sta_disconnected.reason = 202;
    onAgvWifiEvent(ARDUINO_EVENT_WIFI_STA_DISCONNECTED, info);
    onAgvWifiEvent(ARDUINO_EVENT_WIFI_STA_GOT_IP, info);
    onAgvWifiEvent(ARDUINO_EVENT_WIFI_STA_LOST_IP, info);
    auto unauthorized = request(false);
    showDiagnostics(&unauthorized);
    assert(unauthorized.status == "401 Unauthorized");
    const auto oldCommand = pendingCommand.load();
    const auto oldCount = wifiDiagnostics.snapshot().count;
    auto read = request();
    ms += SERVER_HEARTBEAT_TIMEOUT_MS + 1;
    showDiagnostics(&read);
    assert(read.status == "200 OK");
    assert(read.body.find("WIFI_DISCONNECTED") != std::string::npos);
    assert(read.body.find("\"reason\":202") != std::string::npos);
    assert(read.body.find(CONTROL_KEY) == std::string::npos);
    assert(wifiDiagnostics.snapshot().count == oldCount && pendingCommand.load() == oldCommand);
    assert(!sessionConnected(ms)); // A diagnostics read did not renew the lease.
    updateControlWatchdog(ms);
    snapshot = wifiDiagnostics.snapshot();
    assert(WifiDiagnostics::oldest(snapshot, snapshot.count-1).kind == WifiDiagnostics::HeartbeatExpired);
    const auto countAfterExpiry = snapshot.count;
    updateControlWatchdog(ms);
    assert(wifiDiagnostics.snapshot().count == countAfterExpiry);
    fixture();
    controlSession.revoke(); // Explicit Wi-Fi/control revocation is not heartbeat expiry.
    updateControlWatchdog(ms);
    snapshot = wifiDiagnostics.snapshot();
    assert(snapshot.count == 1);
    puts("Diagnostics: ring wrap, warm boot, power reset, event reasons/RSSI, auth, read-only access and expiry attribution passed.");
}

void httpStartupTests() {
    fixture();
    httpStartFailure = false;
    httpRegistrationFailureIndex = -1;
    assert(startControlServer());
    assert(httpRegisteredRoutes.size() == 18);
    assert(httpConfiguredLimit == httpRegisteredRoutes.size());
    assert(httpSocketOpen != nullptr);
    tcpOptionFailure = false;
    assert(httpSocketOpen(nullptr, 73) == ESP_OK);
    assert(configuredTcpSocket == 73 && configuredTcpNoDelay == 1);
    tcpOptionFailure = true;
    assert(httpSocketOpen(nullptr, 74) == ESP_FAIL);
    tcpOptionFailure = false;
    assert(!httpStopped);
    assert(std::find(httpRegisteredRoutes.begin(), httpRegisteredRoutes.end(), "/diagnostics") != httpRegisteredRoutes.end());
    assert(std::find(httpRegisteredRoutes.begin(), httpRegisteredRoutes.end(), "/stop") != httpRegisteredRoutes.end());
    httpRegistrationFailureIndex = 17;
    assert(!startControlServer());
    assert(httpStopped); // A partial server must never be left running.
    httpRegistrationFailureIndex = -1;
    httpStartFailure = true;
    assert(!startControlServer());
    assert(!httpStopped && httpRegisteredRoutes.empty());
    httpStartFailure = false;
    puts("HTTP startup: all 18 real routes register, capacity matches, and startup/registration failures clean up correctly.");
}

void bootStandbyTests() {
    fixture();
    controlSession = ControlSession(CONTROL_KEY, SERVER_HEARTBEAT_TIMEOUT_MS);
    location = NO_NODE;
    publish(State::Unknown);
    const auto oldServoWrites = servoDuties.size();
    initializeBootStandby();
    assert(state == State::Ready && location == POINT_2 && destination == NO_NODE);
    assert(motorLeft == 0 && motorRight == 0 && pendingCommand.load() == 0);
    assert(!forkMoving.load() && servoDuties.size() == oldServoWrites);
    assert(!sessionConnected(ms));
    auto status = request();
    showStatus(&status);
    assert(status.body.find("\"position\":2") != std::string::npos);
    assert(status.body.find("\"control_connected\":false") != std::string::npos);
    state = State::Moving; // A lost position during travel must stay unknown.
    stopTrip(State::LineLost);
    assert(location == NO_NODE && motorLeft == 0 && motorRight == 0);
    loop();
    assert(location == NO_NODE); // A fault must not restore the boot default.
    puts("Boot standby: point 2/ready with motors stopped, no control claim or movement; faults retain unknown position.");
}

int main() {
    bootStandbyTests();
    httpStartupTests();
    diagnosticsTests();
    controlAndTimeoutTests();
    websocketTests();
    inspectionAndRouteTests();
    lineLossAndCancelTests();
    sessionEdgeTests();
    pauseAndOperationTests();
    puts("All tests use fake GPIO, motor UART, servo PWM, HTTP requests, sockets, and time. No physical device was driven.");
}
