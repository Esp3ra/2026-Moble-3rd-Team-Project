
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
    pendingCommand.store(0);
    rcMode.store(false);
    requestedMotion = Motion::Stop;
    rcStopPending = false;
    forkMoving.store(false);
    forkAngle = 85;
    servoAngle.store(85);
    location = POINT_2;
    destination = NO_NODE;
    inspectionTrip = false;
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
        if (mark.node == JUNCTION_8 && isDispatchPoint(route[0])) {
            tick(7);
            tick(7);
            assert(phase == Phase::Follow && returnInspectionBarSeen);
            tick(4);
            tick(4);
            assert(phase == Phase::Follow && returnInspectionBarPassed);
        }
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
        assert(inspect.status == "202 Accepted");
        ms += CONTROL_INTERVAL_MS;
        loop();
        assert(inspectionTrip && state == State::Moving);
        tick(F_CENTER);
        tick(F_CENTER);
        tick(F_LEFT);
        tick(F_LEFT);
        tick(F_CENTER,CENTER_MS);
        assert(phase == Phase::Turn && route[nextIndex] == JUNCTION_8);
        tick(F_BOTH,TURN_MIN_MS);
        assert(state == State::Moving && phase == Phase::Turn);
        tick(0);
        tick(F_CENTER);
        tick(F_CENTER);
        assert(phase == Phase::ExitTurn);
        tick(F_BOTH);
        assert(state == State::Moving);  // Turn-8 residue is ignored.
        tick(F_CENTER);
        tick(F_CENTER);
        assert(inspectionArmed);
        tick(F_BOTH);
        assert(state == State::Inspecting && motorLeft == 0 && motorRight == 0);
        assert(route[nextIndex] == JUNCTION_8);  // Also works during the old 2s gate.
        auto status = request();
        showStatus(&status);
        assert(status.body.find("\"state\":\"inspecting\"") != std::string::npos);
        auto go = request();
        go.user_ctx = &goRequest;
        go.query = "point=" + std::to_string(item.target + 1);
        handleCommand(&go);
        assert(go.status == "202 Accepted");
        frontMask = F_BOTH;
        ms += CONTROL_INTERVAL_MS;
        loop();
        assert(state == State::Moving && !inspectionTrip && route[nextIndex] == JUNCTION_9);
        assert(phase == Phase::Follow);  // Departure bar cannot become junction 9.
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
    puts("Five inspection-to-destination routes, inspection-bar gating, five returns past the extra bar, and 1/2 transfer passed.");
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

int main() {
    controlAndTimeoutTests();
    websocketTests();
    inspectionAndRouteTests();
    sessionEdgeTests();
    puts("All tests use fake GPIO, motor UART, servo PWM, HTTP requests, sockets, and time. No physical device was driven.");
}
