from __future__ import annotations

import json
import secrets
import threading
import time
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path

import cv2

READY = "READY UNO_SIM_V1"
ACTIVE = {"STARTING", "PUSHING", "RETURNING", "WAIT_IR", "ROBOT_WAIT", "SECOND_BELT", "WAIT_IR2"}
MOTION = ACTIVE | {"JOGGING"}


def qr_details(raw):
    """Describe only data actually encoded in the QR. No inferred DB records."""
    labels = {"order_id": "주문 ID", "pallet_id": "팔레트 ID", "robot_id": "로봇 ID",
              "model": "모델", "head": "머리", "left_arm": "왼팔", "right_arm": "오른팔",
              "destination": "목적지", "parts": "부품"}
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        value = None
    structured = isinstance(value, dict)
    fields = []
    if structured:
        for key, item in value.items():
            display = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
            fields.append({"label": labels.get(key, key), "value": display})
    return {"raw": raw, "format": "JSON" if structured else "문자열 / ID",
            "fields": fields, "db_status": "QR 원문 정보 · 주문 연결 결과는 DB 영역에서 확인"}


class Bench:
    def __init__(self, root: Path, demo=False, clock=time.monotonic):
        self.root, self.demo, self.clock = root, demo, clock
        self.lock = threading.RLock()
        self.io_lock = threading.RLock()
        self.closed = threading.Event()
        self.serial = None
        self.serial_port = None
        self.serial_auto_enabled = False
        self.serial_reconnecting = False
        self.serial_opening = False
        self.serial_error = ''
        self.serial_target = None
        self.serial_stop_pending = False
        self._serial_generation = 0
        self._serial_reader = None
        self._serial_connection_lock = threading.Lock()
        self._serial_next_retry = 0.
        self._serial_last_seen = 0.
        self._serial_last_probe = 0.
        self.connected = self.ready = False
        self.protocol_ok = False
        self.ir2_clear = False
        self.timing_values = {'ir1_delay_ms':5500,'robot_wait_ms':3000,'ir2_delay_ms':2000}
        self.timing_status = 'waiting'
        self.timing_error = ''
        self.timing_pending = None
        self.ir_clear = False
        self.jog_ms = None
        self.jog = None
        self.jog_finished_at = -1000.0
        self.ir_delay_ms = None
        self.camera_reconnecting = False
        self.camera_ok = False
        self.camera_error = "카메라 준비 중"
        self.camera_at = -1000.0
        self.clear_frames = 0
        self.latest_codes = []
        self.jpeg = None
        self.preview_condition = threading.Condition()
        self.preview_jpeg = None
        self.preview_sequence = 0
        self.events = deque(maxlen=100)
        self.trial = None
        self.state = "IDLE"
        self.error = ""
        self.io_failed = False
        self.ready_deadline = 0.0
        self.owner_seen = 0.0
        self.event("SERVER_STARTED", mode="DEMO" if demo else "LIVE")

    def event(self, name, **info):
        item = {"at": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                "event": name, "mode": "DEMO" if self.demo else "LIVE", **info}
        self.events.append(item)
        if self.trial:
            item["run_id"] = self.trial["id"]
            try:
                with (self.root / self.trial["id"] / "events.jsonl").open("a", encoding="utf-8") as f:
                    f.write(json.dumps(item, ensure_ascii=False) + "\n")
            except OSError:
                self.io_failed = True
                self.error = "기록 저장 실패 — 시험을 중단합니다"

    def _port_records(self):
        import serial.tools.list_ports
        return [{"device":p.device, "description":p.description,
                 "vid":p.vid, "pid":p.pid, "serial_number":p.serial_number,
                 "location":p.location} for p in serial.tools.list_ports.comports()]

    def available_ports(self):
        if self.demo:
            return [{"device":"DEMO", "description":"가상 UNO (실제 장비 연결 안 함)"}]
        return [{"device":p['device'],"description":p['description']} for p in self._port_records()]

    @staticmethod
    def match_serial_port(target, ports):
        if not target: return None
        def same_usb(port):
            return all(target.get(key) is None or target.get(key)==port.get(key) for key in ('vid','pid'))
        candidates = [port for port in ports if same_usb(port)]
        if target.get('serial_number'):
            candidates = [p for p in candidates if p.get('serial_number')==target['serial_number']]
        elif target.get('location'):
            candidates = [p for p in candidates if p.get('location')==target['location']]
        else:
            candidates = [p for p in candidates if p['device']==target['device']]
        # Never guess between boards with indistinguishable identifiers.
        return candidates[0] if len(candidates)==1 else None

    def _clear_serial_readiness(self):
        self.ready = self.protocol_ok = self.ir_clear = self.ir2_clear = False
        self.serial_stop_pending = True
        self.jog_ms = self.ir_delay_ms = None
        self.timing_status = 'waiting'
        self.timing_error = ''
        self.timing_pending = None
        self.ready_deadline = 0.

    def connect(self, port):
        if self.demo:
            with self.lock:
                if self.connected: raise ValueError('이미 연결되어 있습니다')
                if port!='DEMO': raise ValueError('DEMO 포트를 선택하세요')
                self.connected = self.ready = self.protocol_ok = self.ir_clear = self.ir2_clear = True
                self.serial_port = 'DEMO'
                self.serial_stop_pending = False
                self.ir_delay_ms = self.timing_values['ir1_delay_ms']
                self.timing_status = 'confirmed'
                self.jog_ms = 1000
                self.error = ''
                self.event('SERIAL_READY',port='DEMO')
            return
        ports = self._port_records()
        selected = next((p for p in ports if p['device']==port),None)
        if selected is None: raise ValueError('목록에서 UNO USB 포트를 선택하세요')
        with self.lock:
            if self.closed.is_set(): raise ValueError('서버가 종료 중입니다')
            if self.connected: raise ValueError('이미 연결되어 있습니다. 먼저 연결을 해제하세요')
            self._serial_generation += 1
            generation = self._serial_generation
            self.serial_target = selected.copy()
            self.serial_auto_enabled = True
            self.serial_reconnecting = True
            self.serial_error = ''
            self.error = ''
        self._open_serial(selected,generation,raise_error=True)

    def _open_serial(self, port, generation, raise_error=False):
        import serial
        with self._serial_connection_lock:
            with self.lock:
                if self.closed.is_set() or generation!=self._serial_generation or not self.serial_auto_enabled or self.serial is not None:
                    return False
                if self._serial_reader and self._serial_reader.is_alive():
                    self.serial_error = '이전 USB 읽기 종료 대기 중'
                    if raise_error: raise OSError(self.serial_error)
                    return False
                self.serial_opening = True
            device = None
            try:
                device = serial.Serial(port['device'],115200,timeout=.05,write_timeout=.3)
                with self.lock:
                    if self.closed.is_set() or generation!=self._serial_generation or not self.serial_auto_enabled:
                        device.close();return False
                    self.serial = device
                    self.serial_port = port['device']
                    self.connected = True
                    self._clear_serial_readiness()
                    self.ready_deadline = self.clock()+8
                    self._serial_last_seen = self._serial_last_probe = self.clock()
                    self.serial_error = ''
                    self.event('SERIAL_OPEN',port=self.serial_port,reconnect=self.serial_reconnecting)
                    # Stop first, then require a valid banner and a second stop ACK.
                    self._write('s')
                    reader = threading.Thread(target=self._reader,args=(device,generation),daemon=True)
                    self._serial_reader = reader
                    reader.start()
                return True
            except Exception as exc:
                with self.lock:
                    if generation==self._serial_generation:
                        self.serial_error = 'UNO 연결 재시도 중: '+str(exc)
                        self.serial_reconnecting = self.serial_auto_enabled
                        if self.serial is device:
                            self.serial = None;self.serial_port = None;self.connected = False
                            self._clear_serial_readiness()
                if device is not None:
                    try:device.close()
                    except Exception:pass
                if raise_error: raise OSError(self.serial_error) from exc
                return False
            finally:
                with self.lock:
                    if generation==self._serial_generation:self.serial_opening = False

    def serial_reconnect_step(self, retry_interval=1.):
        if self.demo or self.closed.is_set(): return
        with self.lock:
            if not self.serial_auto_enabled: return
        try:
            ports = self._port_records()
        except Exception as exc:
            with self.lock:self.serial_error = 'USB 포트 조회 실패: '+str(exc)
            return
        lost = None
        with self.lock:
            if not self.serial_auto_enabled or self.closed.is_set(): return
            now = self.clock()
            if self.connected:
                present = any(p['device']==self.serial_port and self.match_serial_port(self.serial_target,[p]) for p in ports)
                if not present:
                    lost = (self.serial,self._serial_generation,'USB 포트가 사라졌습니다')
                elif self.ready_deadline and now>=self.ready_deadline:
                    lost = (self.serial,self._serial_generation,'UNO READY/정지 응답 시간 초과')
                elif self.protocol_ok and now-self._serial_last_seen>6:
                    lost = (self.serial,self._serial_generation,'UNO 응답 끊김')
                elif self.protocol_ok and now-self._serial_last_probe>=2:
                    self._serial_last_probe = now
                    try:self._write('?')  # Read-only timing/status probe.
                    except Exception as exc:lost=(self.serial,self._serial_generation,str(exc))
                if lost is None:return
            elif not self.serial_opening and now>=self._serial_next_retry:
                self._serial_next_retry = now+retry_interval
                selected = self.match_serial_port(self.serial_target,ports)
                if selected is None:
                    self.serial_reconnecting = True
                    self.serial_error = '선택한 UNO를 재탐색 중 · 식별 불가/중복이면 포트를 직접 선택하세요'
                    return
                generation = self._serial_generation
            else:return
        if lost:
            self._serial_lost(*lost)
            return
        self._open_serial(selected,generation)

    def _serial_lost(self, device, generation, reason):
        with self.lock:
            if device is not self.serial or generation!=self._serial_generation:return
            moving = self.state in MOTION or self.state=='AWAIT_CONFIRM'
            if moving:
                self.abort('UNO USB 연결 끊김 · 회차 중단: '+str(reason))
            else:
                try:self._write('s')
                except Exception:pass
            self.serial = None;self.serial_port = None;self.connected = False
            self._serial_generation += 1
            self._clear_serial_readiness()
            self.serial_opening = False
            self.serial_reconnecting = self.serial_auto_enabled and not self.closed.is_set()
            self.serial_error = str(reason)
            self.event('SERIAL_LOST',reason=str(reason),auto_retry=self.serial_reconnecting)
        try:device.close()
        except Exception:pass

    def _write(self, value):
        if self.demo:
            self.event("DEMO_COMMAND", command=value)
            return
        if self.serial is None:
            raise OSError("UNO 연결이 없습니다")
        with self.io_lock:
            data = (value + "\n").encode("ascii")
            if self.serial.write(data) != len(data):
                raise OSError("USB 명령 전송 불완전")

    def _reader(self, device, generation):
        pending = bytearray()
        try:
            while not self.closed.is_set():
                with self.lock:
                    if device is not self.serial or generation!=self._serial_generation:break
                chunk = device.read(128)
                for byte in chunk:
                    if byte in (10,13):
                        if pending:
                            value = pending.decode('ascii',errors='replace');pending.clear()
                            with self.lock:
                                if device is not self.serial or generation!=self._serial_generation:return
                                self._serial_last_seen = self.clock()
                                self.line(value)
                    elif len(pending)<200:pending.append(byte)
                    else:raise OSError('UNO 메시지 길이 오류')
        except Exception as exc:
            self._serial_lost(device,generation,exc)
        finally:
            try:device.close()
            except Exception:pass

    def disconnect(self):
        with self.lock:
            if self.state in MOTION:raise ValueError('먼저 전체 정지를 누르세요')
            self.serial_auto_enabled = self.serial_reconnecting = False
            self._serial_generation += 1
            device = self.serial
            self.serial = None;self.serial_port = None;self.connected = False
            self._clear_serial_readiness()
            self.serial_stop_pending = False
            self.serial_error = ''
            self.event('SERIAL_DISCONNECTED',auto_retry=False)
        if device:
            try:device.close()
            except Exception:pass

    def line(self, value):
        with self.lock:
            self.event("UNO", text=value)
            if value.startswith("READY"):
                self.ir_clear = self.ir2_clear = False
                self.timing_status = "waiting"
                self.timing_pending = None
                self.ir_delay_ms = None
                self.jog_ms = None
                self.protocol_ok = value == READY
                if not self.protocol_ok:
                    if self.state in MOTION:
                        self.abort("펌웨어 버전 불일치")
                    self.ready = False
                    self.error = "RobotWorkSimulation 펌웨어를 업로드하세요"
                    self.ready_deadline = 0
                    self.serial_auto_enabled = self.serial_reconnecting = False
                    self.serial_error = self.error
                elif self.state in MOTION:
                    self.abort("실행 중 UNO가 재부팅되었습니다")
                else:
                    self.ready = not self.serial_stop_pending
                    self.ready_deadline = self.clock()+3 if self.serial_stop_pending else 0
                    if self.state not in {'ERROR','STOPPED'}:self.error = ''
                    if self.serial_stop_pending:self._write('s')
                return
            if value.startswith("TIMING:"):
                try:
                    parts = value.split(':')
                    if len(parts) != 5: raise ValueError('잘못된 설정 응답')
                    revision = int(parts[1])
                    values = self.validate_timings(dict(zip(self.timing_values, map(int, parts[2:]))))
                except (ValueError, TypeError):
                    self.timing_status = 'error'
                    self.timing_error = 'UNO 시간 설정 응답을 확인하세요'
                    return
                if not self.protocol_ok: return
                request = self.timing_pending
                if request:
                    if revision != request['revision'] or values != request['values']: return
                elif revision != 0:
                    return
                if self.state in MOTION:
                    if values != self.timing_values: self.abort('운전 중 시간 설정 응답 불일치')
                    return
                self.timing_values = values
                self.ir_delay_ms = values['ir1_delay_ms']
                self.timing_status = 'confirmed'
                self.timing_error = ''
                self.timing_pending = None
                self.event('TIMING_CONFIRMED', **values)
                return
            if value in {'IR2:CLEAR', 'IR2:DETECTED'}:
                self.ir2_clear = value == 'IR2:CLEAR'
                return
            if value in {'ERR:TIMING_RANGE','BUSY:TIMING_LOCKED'} and self.timing_pending:
                self.timing_status = 'error'
                self.timing_error = 'UNO가 시간 설정을 거부했습니다: ' + value
                return
            if value.startswith("MANUAL_JOG_MS:"):
                try: self.jog_ms = int(value.split(":", 1)[1])
                except ValueError: self.jog_ms = None
                return
            if self.state == "JOGGING" and self.jog:
                if value == "JOG:" + self.jog["direction"].upper() + "_" + str(self.jog["duration_ms"]) + "MS":
                    self.jog["acked"] = True
                    return
                if value == "JOG:DONE_POSITION_UNVERIFIED":
                    if not self.jog["acked"]:
                        self.abort("수동 이동 시작 응답 없이 종료 응답 수신")
                    else:
                        self.jog["done"] = True
                        self.jog_finished_at = self.clock()
                        self.state = "STOPPED"
                        self.ready = self.protocol_ok
                        self.event("MANUAL_JOG_DONE_POSITION_UNVERIFIED")
                    return
                if value.startswith(("ERR:", "BUSY:")):
                    self.abort("UNO 수동 이동 거부: " + value)
                    return
            if value.startswith("AFTER_IR_MS:"):
                try: self.ir_delay_ms = int(value.split(":", 1)[1])
                except ValueError: self.ir_delay_ms = None
                return
            if value in {"IR:CLEAR", "IR:DETECTED"}:
                self.ir_clear = value == "IR:CLEAR"
                return
            if value == "STOPPED:BOTH_READY_A":
                if self.state in MOTION and not self.serial_stop_pending:
                    self.abort('운전 중 예상하지 못한 UNO 정지 응답')
                self.jog_finished_at = self.clock()
                self.ready = self.protocol_ok
                if self.protocol_ok:
                    self.serial_stop_pending = False
                    self.serial_reconnecting = False
                    self.serial_error = ''
                    self.ready_deadline = 0
                    self.event('SERIAL_READY',port=self.serial_port)
                if self.state not in {"ERROR", "STOPPED"}:
                    self.state = "STOPPED"
                return
            if not self.trial or self.state not in ACTIVE:
                return
            trial = self.trial
            trial = self.trial
            if value == "ACTUATOR:PUSH_10000MS":
                trial["acked"] = True
                trial["ack_at"] = self.clock()
                self.state = "PUSHING"
            elif value == "RETURN_AND_BELT:STARTED" and trial["acked"]:
                trial["belt_started"] = True
                self.state = "RETURNING"
            elif value == "QR:RECOGNIZED_CONTINUE_TO_IR" and trial["qr_sent"]:
                trial["qr_ack"] = True
            elif value == "IR_TIMER:STARTED" and trial["belt_started"]:
                trial["ir_triggered"] = True
                self.state = "WAIT_IR"
            elif value == "SIM:STATION_STOPPED":
                if not trial["ir_triggered"]:
                    self.abort("IR1 확인 없이 작업 위치 정지 응답 수신")
                    return
                trial["first_belt_stopped"] = True
                self.state = "ROBOT_WAIT"
            elif value == "SIM:BELT_RESTARTED":
                if not trial["first_belt_stopped"]:
                    self.abort("작업 위치 정지 확인 없이 재구동 응답 수신")
                    return
                trial["sim_resumed"] = True
                self.state = "SECOND_BELT"
            elif value == "IR2_TIMER:STARTED" and trial["sim_resumed"]:
                trial["ir2_triggered"] = True
                self.state = "WAIT_IR2"
            elif value == "BELT_STOPPED:IR2_DELAY_DONE":
                if not trial["first_belt_stopped"] or not trial["sim_resumed"] or not trial["ir2_triggered"]:
                    self.abort("두 번째 IR 확인 없이 최종 정지 응답 수신")
                    return
                trial["belt_stopped"] = True
            elif value == "CYCLE_DONE:READY_A" and trial["acked"]:
                if not trial["belt_stopped"]:
                    self.abort("IR 정지 응답 없이 동작 완료 수신")
                    return
                trial["machine_done"] = True
                self.ready = True
                if trial["qr_ok"] and trial["qr_ack"]:
                    self.state = "AWAIT_CONFIRM"
                else:
                    self.state = "QR_UNCONFIRMED"
                    self.error = "장비 종료 · QR 미확인: DB 배정과 회차 성공 처리를 차단했습니다"
                    self.event("CYCLE_QR_UNCONFIRMED")
            elif value.startswith(("ERR:", "BUSY:")):
                self.abort("UNO가 실행을 거절했습니다: " + value)

    def heartbeat(self, owner):
        with self.lock:
            if self.trial and owner == self.trial["owner"]:
                self.owner_seen = self.clock()

    def start(self, owner, position_ready):
        with self.lock:
            if not isinstance(owner, str) or not (1 <= len(owner) <= 80):
                raise ValueError("화면 식별자가 없습니다. 페이지를 새로 열어 주세요")
            if position_ready is not True:
                raise ValueError("팔레트·막대 위치를 먼저 확인하세요")
            if self.state in MOTION or self.state == "AWAIT_CONFIRM":
                raise ValueError("진행 중인 회차를 먼저 완료하거나 정지하세요")
            if self.clock() - self.jog_finished_at < .6:
                raise ValueError("수동 이동 후 잠시 기다려 주세요")
            if not self.connected or not self.ready or not self.protocol_ok:
                raise ValueError("UNO 연결 및 통합 코드 READY 확인이 필요합니다")
            if not self.ir_clear:
                raise ValueError("IR 센서 사이를 비우고 IR:CLEAR를 확인하세요")
            if not self.ir2_clear:
                raise ValueError("두 번째 IR 센서 통로 비움을 확인하세요")
            if self.timing_status != 'confirmed' or self.timing_pending or self.ir_delay_ms != self.timing_values['ir1_delay_ms']:
                raise ValueError("UNO 시간 설정 적용 확인이 필요합니다")
            if not self.camera_ok or self.clock() - self.camera_at > 2:
                raise ValueError("카메라가 준비되지 않았습니다")
            if self.clear_frames < 3:
                raise ValueError("카메라에서 기존 QR을 치우고 잠시 기다리세요")
            run_id = datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8]
            (self.root / run_id).mkdir(parents=True)
            self.trial = {"id": run_id, "owner": owner, "pallet_id": None,
                          "started": self.clock(), "acked": False, "qr_ok": False,
                          "machine_done": False, "ejection_confirmed": False,
                          "qr_sent": False, "qr_sent_at": None, "qr_ack": False,
                          "ack_at": None, "belt_started": False, "ir_triggered": False,
                          "belt_stopped": False, "last_rejected": None, "qr_result": None,
                          "first_belt_stopped": False, "sim_resumed": False, "ir2_triggered": False,
                          "timings": self.timing_values.copy()}
            self.io_failed = False
            self.owner_seen = self.clock()
            self.state = "STARTING"
            self.ready = False
            self.error = ""
            self.event("TRIAL_START", recognition="automatic")
            if self.io_failed:
                self.state = "ERROR"
                self.ready = True  # No command was sent.
                raise OSError(self.error)
            try:
                self._write("a")  # Exactly once. Never auto-retry motion commands.
            except Exception as exc:
                self.abort(f"실행 명령 전송 실패: {exc}")
                raise ValueError(self.error) from exc

    @staticmethod
    def validate_timings(values):
        limits = {'ir1_delay_ms':25000,'robot_wait_ms':30000,'ir2_delay_ms':25000}
        if not isinstance(values, dict) or set(values) != set(limits):
            raise ValueError('IR1 구동 / 작업 대기 / IR2 구동의 세 설정이 필요합니다')
        for key, limit in limits.items():
            value = values[key]
            if type(value) is not int or value < 0 or value > limit or value % 500:
                raise ValueError(f'{key}: 0~{limit/1000:g}초, 0.5초 단위로 설정하세요')
        return {key:values[key] for key in limits}

    def update_timings(self, values):
        values = self.validate_timings(values)
        with self.lock:
            if self.state in MOTION or self.state == 'AWAIT_CONFIRM':
                raise ValueError('운전과 결과 확인을 마친 뒤 시간을 수정하세요')
            if not self.connected or not self.ready or not self.protocol_ok:
                raise ValueError('UNO 연결과 정지 상태를 먼저 확인하세요')
            if self.timing_status == 'pending':
                raise ValueError('이전 시간 설정 응답을 기다리고 있습니다')
            revision = secrets.randbelow(1_000_000_000)+1
            self.timing_pending = {'revision':revision,'values':values,'sent_at':self.clock()}
            self.timing_status = 'pending'
            self.timing_error = ''
            try:
                self._write(f"T {revision} {values['ir1_delay_ms']} {values['robot_wait_ms']} {values['ir2_delay_ms']}")
            except Exception as exc:
                self.timing_status = 'error'
                self.timing_error = '시간 설정 전송 실패: ' + str(exc)
                raise OSError(self.timing_error) from exc
            return {'status':'pending','requested':values}

    def manual_jog(self, direction, owner, area_clear):
        with self.lock:
            if not isinstance(direction, str) or direction not in {"forward", "reverse"}:
                raise ValueError("전진 또는 후진을 선택하세요")
            if not isinstance(owner, str) or not 1 <= len(owner) <= 80:
                raise ValueError("화면 식별자가 없습니다")
            if area_clear is not True:
                raise ValueError("액추에이터 이동 공간을 먼저 확인하세요")
            if self.state in MOTION or self.state == "AWAIT_CONFIRM":
                raise ValueError("자동 운전·수동 이동·결과 확인 중에는 수동 이동할 수 없습니다")
            if self.timing_status != 'confirmed' or self.timing_pending:
                raise ValueError('시간 설정 적용 응답을 먼저 확인하세요')
            if not self.connected or not self.ready or not self.protocol_ok:
                raise ValueError("전체 정지 응답과 V4 연결을 먼저 확인하세요")
            if not isinstance(self.jog_ms, int) or not 100 <= self.jog_ms <= 5000:
                raise ValueError("수동 복구 기능이 포함된 새 펌웨어를 업로드하세요")
            if self.clock() - self.jog_finished_at < .6:
                raise ValueError("방향 전환 전 0.6초 이상 기다려 주세요")
            self.jog = {"direction": direction, "owner": owner,
                        "started": self.clock(), "duration_ms": self.jog_ms, "acked": False, "done": False}
            self.state = "JOGGING"
            self.ready = False
            self.error = ""
            self.event("MANUAL_JOG_REQUEST", direction=direction, duration_ms=self.jog_ms)
            try:
                self._write("j" if direction == "forward" else "k")
            except Exception as exc:
                self.abort("수동 이동 전송 실패: " + str(exc))
                raise OSError(self.error) from exc

    def abort(self, reason, manual=False):
        with self.lock:
            if self.state == "JOGGING": self.jog_finished_at = self.clock()
            self.state = "STOPPED" if manual else "ERROR"
            self.error = "" if manual else reason
            self.ready = False
            self.event("STOP_REQUESTED", reason=reason)
            if self.connected:
                try:
                    self._write("s")
                    if self.demo: self.ready = True
                except Exception as exc:
                    self.error = f"정지 전송 실패: {exc}. 모터 외부 전원을 분리하세요."
            else:
                self.error = "UNO 연결 없음 — 소프트웨어 정지를 보장할 수 없습니다. 모터 전원을 확인하세요."

    def frame(self, values, image, captured_at):
        ok, jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 75])
        if not ok: raise OSError("카메라 이미지 인코딩 실패")
        with self.lock:
            self.jpeg = jpeg.tobytes()
            recovered = self.camera_reconnecting
            self.camera_ok = True
            self.camera_reconnecting = False
            self.camera_error = ""
            if recovered: self.event("CAMERA_RECONNECTED")
            self.camera_at = captured_at
            self.latest_codes = values
            self.clear_frames = self.clear_frames + 1 if not values else 0
            trial = self.trial
            if not trial or self.state not in ACTIVE or (trial["ack_at"] is None or captured_at < trial["ack_at"] or self.clock() - captured_at > 2):
                return
            if trial["qr_ok"] or not trial["acked"] or trial["belt_stopped"]:
                return
            if __package__:
                from .pallet_repository import validate_pallet_id
            else:
                from pallet_repository import validate_pallet_id
            try:
                if len(values) != 1 or not isinstance(values[0], str):
                    raise ValueError("QR 하나만 인식해야 합니다")
                pallet_id = validate_pallet_id(values[0])
            except ValueError:
                if values and values != trial["last_rejected"]:
                    trial["last_rejected"] = values.copy()
                    self.event("QR_REJECTED", decoded=values)
                return
            # V4 q reports QR only; the independent IR timer stops the belt.
            try:
                self._write("q")
            except Exception as exc:
                self.abort(f"QR 알림 전송 실패: {exc}")
                return
            trial["pallet_id"] = pallet_id
            trial["qr_sent"] = True
            trial["qr_sent_at"] = self.clock()
            trial["qr_ok"] = True
            trial["qr_result"] = {**qr_details(values[0]),
                                  "recognized_at": datetime.now().astimezone().isoformat(timespec="seconds")}
            self.event("QR_INFO_SENT", decoded=values[0])
            try:
                (self.root / trial["id"] / "qr_confirmed.jpg").write_bytes(self.jpeg)
            except OSError as exc:
                self.abort(f"QR 이미지 저장 실패: {exc}")
                return
            self.event("QR_CONFIRMED", decoded=values[0])

    def confirm_ejection(self, owner):
        with self.lock:
            if (not self.trial or owner != self.trial["owner"] or
                    self.state != "AWAIT_CONFIRM" or not self.trial["machine_done"] or
                    not self.trial["qr_ok"] or not self.trial["qr_ack"] or not self.trial["belt_stopped"]):
                raise ValueError("장비 동작 종료와 팔레트 QR 확인 후, 시작한 화면에서 확인하세요")
            self.trial["ejection_confirmed"] = True
            self.state = "SUCCESS"
            self.event("TRIAL_SUCCESS_OPERATOR_CONFIRMED")
            if self.io_failed: self.abort("결과 기록 실패")

    def tick(self):
        with self.lock:
            now = self.clock()
            if self.demo and self.connected and not self.ready and self.state not in ACTIVE and self.ready_deadline:
                if now >= self.ready_deadline:
                    self.ready_deadline = 0
                    self.error = "READY 미수신 — 통합 코드/115200/포트 점유를 확인하고 UNO RESET을 눌러 주세요"
            if self.timing_pending:
                pending = self.timing_pending
                if self.demo and self.timing_status == 'pending':
                    values = pending['values']
                    self.line(f"TIMING:{pending['revision']}:{values['ir1_delay_ms']}:{values['robot_wait_ms']}:{values['ir2_delay_ms']}")
                elif now - pending['sent_at'] > 2 and self.timing_status == 'pending':
                    self.timing_status = 'error'
                    self.timing_error = 'UNO 시간 설정 응답 시간 초과 · 다시 적용하세요'
            if self.state == "JOGGING" and self.jog:
                elapsed = now - self.jog["started"]
                if self.demo:
                    if not self.jog["acked"]:
                        self.line("JOG:" + self.jog["direction"].upper() + "_" + str(self.jog["duration_ms"]) + "MS")
                    if elapsed >= self.jog["duration_ms"] / 1000: self.line("JOG:DONE_POSITION_UNVERIFIED")
                elif elapsed > 1 and not self.jog["acked"]:
                    self.abort("수동 이동 시작 응답 시간 초과")
                elif elapsed > self.jog["duration_ms"] / 1000 + 2:
                    self.abort("수동 이동 종료 응답 시간 초과")
                return
            if not self.trial or self.state not in ACTIVE:
                return
            age = now - self.trial["started"]
            if self.io_failed: self.abort("기록 저장 실패")
            elif now - self.owner_seen > 4: self.abort("시작한 브라우저의 연결 확인이 끊겼습니다")
            elif now - self.camera_at > 3: self.abort("카메라 영상 수신이 끊겼습니다")
            elif not self.trial["acked"] and age > 3: self.abort("UNO 실행 응답 시간 초과")
            elif (self.trial["qr_sent"] and not self.trial["qr_ack"] and
                  now - self.trial["qr_sent_at"] > 2): self.abort("QR 알림 응답 시간 초과")
            elif not self.trial["machine_done"] and age > 77 + self.trial["timings"]["robot_wait_ms"] / 1000: self.abort("장비 동작 종료 응답 시간 초과")

    def snapshot(self):
        with self.lock:
            trial = self.trial or {}
            return {"mode": "DEMO" if self.demo else "LIVE", "state": self.state,
                    "connected": self.connected, "ready": self.ready,
                    "serial_port":self.serial_port,"serial_auto_enabled":self.serial_auto_enabled,
                    "serial_reconnecting":self.serial_reconnecting,"serial_opening":self.serial_opening,
                    "serial_error":self.serial_error,
                    "jog_ms": self.jog_ms, "jog": dict(self.jog) if self.jog else None,
                    "jog_available": self.clock() - self.jog_finished_at >= .6,
                    "protocol_ok": self.protocol_ok, "ir_clear": self.ir_clear, "ir2_clear": self.ir2_clear, "ir_delay_ms": self.ir_delay_ms,
                    "timings": {"values":self.timing_values.copy(), "status":self.timing_status,
                                "error":self.timing_error,
                                "requested":self.timing_pending['values'].copy() if self.timing_pending else None},
                    "camera_ok": self.camera_ok and self.clock() - self.camera_at < 2,
                    "camera_error": self.camera_error, "camera_reconnecting": self.camera_reconnecting, "clear_view": self.clear_frames >= 3,
                    "seen": self.latest_codes, "error": self.error,
                    "qr_current": (qr_details(self.latest_codes[0])
                                   if len(self.latest_codes) == 1 and
                                   self.latest_codes[0] != "(QR 감지됨 · 판독 불가)" else None),
                    "trial": {k: trial.get(k) for k in
                              ("id", "pallet_id", "acked", "qr_ok", "qr_ack", "qr_result", "ir_triggered", "belt_stopped", "machine_done", "ejection_confirmed", "first_belt_stopped", "sim_resumed", "ir2_triggered", "timings")},
                    "events": list(self.events)[-25:]}

    def shutdown(self):
        with self.lock:
            if self.connected:
                try: self._write("s")
                except Exception: pass
            self.serial_auto_enabled = self.serial_reconnecting = False
            self._serial_generation += 1
            self.closed.set()
            if self.serial:
                try: self.serial.close()
                except Exception: pass
