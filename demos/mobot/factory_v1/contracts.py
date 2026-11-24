from .common import *

EVENT_RULES={
 'PALLET_DISPENSED':('LINE','DISPENSE',{'evidence':('SENSOR','TIMER','OPERATOR')}),
 'ASSEMBLY_STATION_STOPPED':('LINE','MOVE_TO_ASSEMBLY',{'ir1_detected':True,'belt_command':'STOPPED'}),
 'PICKUP_STATION_STOPPED':('LINE','MOVE_TO_PICKUP',{'ir2_detected':True,'belt_command':'STOPPED'}),
 'ARM_STANDBY_AFTER_ASSEMBLY':('ARM','ASSEMBLE',{'standby_verified':True,'assembly_steps_verified':True}),
 'LIFT_FINISHED':('AGV','PICK_UP',{'station':1,'fork_target_deg':110,'fork_moving':False}),
 'INSPECTION_ARRIVED':('AGV','GO_TO_INSPECTION',{'state':'inspecting'}),
 'DESTINATION_ARRIVED':('AGV','DELIVER',{'destination':(3,4,5,6,7)}),
 'UNLOAD_FINISHED':('AGV','UNLOAD',{'destination':(3,4,5,6,7),'fork_target_deg':60,'fork_moving':False}),
 'STANDBY_RETURNED':('AGV','RETURN_STANDBY',{'station':2,'fork_target_deg':85,'fork_moving':False})}
PAUSED={'LINE':{'belt_output_stopped':True,'actuator_output_stopped':True},'ARM':{'arm_motion_stopped':True},'AGV':{'drive_stopped':True,'fork_motion_stopped':True}}


def fixed(data,fields):
    object_fields(data,fields)
    for key,wanted in fields.items():
        value=data[key]
        if isinstance(wanted,tuple):require(type(value)==type(wanted[0]) and value in wanted,'관측 허용값을 확인하세요.')
        else:require(type(value)==type(wanted) and value==wanted,'필수 완료 증거가 없습니다.',422,'CONFLICT')


def device_status(data,device=None):
    object_fields(data,('device_id','boot_id','mode','state','cycle_id','active_command_id','last_completed_command_id','observed_at','online'))
    require(data['device_id'] in DEVICES and (device is None or data['device_id']==device),'장비 ID가 다릅니다.',403,'FORBIDDEN')
    identifier(data['boot_id'])
    require(data['mode'] in ('AUTO','MANUAL') and data['state'] in ('IDLE','BUSY','PAUSED','FAULT','UNKNOWN'),'장비 상태 코드 오류')
    require(type(data['online']) is bool,'online은 bool입니다.')
    for key in ('cycle_id','active_command_id','last_completed_command_id'):
        if data[key] is not None:identifier(data[key])
    utc(data['observed_at'])
    return data


def error_payload(data):
    object_fields(data,('code','message','retry','trace_id'),('device_id','cycle_id','command_id'))
    require(data['code'] in ('BAD_REQUEST','UNAUTHORIZED','FORBIDDEN','NOT_FOUND','CONFLICT','STALE_CYCLE','BAD_TRANSITION','OUT_OF_STOCK','INVALID_ADDRESS','DEVICE_UNAVAILABLE','RECONCILE_REQUIRED','STALE_CALIBRATION','INTERNAL_ERROR','RATE_LIMITED','UNSUPPORTED_VERSION'),'오류 코드 오류')
    text(data['message'],400)
    require(data['retry'] in ('NONE','QUERY_FIRST','SAME_REQUEST','OPERATOR'),'재시도 코드 오류')
    identifier(data['trace_id'])
    for key in ('cycle_id','command_id'):
        if key in data:identifier(data[key])


def pose_set(data):
    object_fields(data,('spec','points'))
    spec(data['spec'])
    names=('head_pick','head_place','left_pick','left_place','right_pick','right_place')
    object_fields(data['points'],names)
    for pose in data['points'].values():
        object_fields(pose,('frame','tool_frame','calibration_id','x_mm','y_mm','z_mm','rx_deg','ry_deg','rz_deg','captured_at'))
        require(pose['frame']=='ROBOT_BASE' and pose['tool_frame']=='GRIP_CENTER','좌표 기준 오류',422,'STALE_CALIBRATION')
        text(pose['calibration_id'],100)
        utc(pose['captured_at'])
        for key in ('x_mm','y_mm','z_mm','rx_deg','ry_deg','rz_deg'):
            require(type(pose[key]) in (int,float) and __import__('math').isfinite(pose[key]),'좌표 값 오류')


def command(data):
    object_fields(data,('command_id','device_id','cycle_id','action','params'))
    identifier(data['command_id'])
    require(data['device_id'] in DEVICES,'장비 ID 오류')
    if data['cycle_id'] is not None:identifier(data['cycle_id'])
    action=data['action']
    expected={rule[1]:rule[0] for rule in EVENT_RULES.values()}
    require(action in (*expected,'PAUSE','RESUME'),'동작 코드 오류')
    if action in expected:
        require(data['device_id']==expected[action] and data['cycle_id'] is not None,'동작 대상 또는 회차 오류',422,'CONFLICT')
    params=data['params']
    if action=='ASSEMBLE':pose_set(params)
    elif action in ('DELIVER','UNLOAD'):
        object_fields(params,('destination','inspection_id'))
        require(type(params['destination']) is int and params['destination'] in (3,4,5,6,7),'목적지 오류')
        identifier(params['inspection_id'])
    elif action=='RESUME':
        object_fields(params,('pause_command_id',));identifier(params['pause_command_id'])
    else:object_fields(params,())
    return data


def vision(data):
    object_fields(data,('inspection_id','cycle_id','frame_id','captured_at','image_ref','model_version','calibration_id','qr_read','head','left','right'))
    for key in ('inspection_id','cycle_id'):identifier(data[key])
    for key,limit in [('frame_id',128),('image_ref',256),('model_version',100),('calibration_id',100)]:text(data[key],limit)
    utc(data['captured_at'])
    require(data['qr_read'] is None or isinstance(data['qr_read'],str) and 0<len(data['qr_read'])<=120,'QR 관측값 오류')
    for slot in ('head','left','right'):
        row=data[slot];object_fields(row,('observation','observed_id','confidence'))
        require(row['observation'] in ('SEEN','MISSING','UNKNOWN'),'관측 분류 오류')
        if row['observation']=='SEEN':require(row['observed_id'] in sum((tuple(v) for v in SPEC.values()),()),'검출 라벨 오류')
        else:require(row['observed_id'] is None,'미검출 관측에는 부품 라벨을 넣을 수 없습니다.')
        conf=row['confidence']
        require(conf is None or type(conf) in (int,float) and 0<=conf<=1,'신뢰도 오류')


def event(data):
    object_fields(data,('event_id','device_id','boot_id','event_seq','cycle_id','command_id','occurred_at','kind','data'))
    for key in ('event_id','boot_id'):identifier(data[key])
    require(type(data['event_seq']) is int and data['event_seq']>=1,'이벤트 순번 오류')
    utc(data['occurred_at'])
    require(data['device_id'] in (*DEVICES,'VISION'),'이벤트 장비 ID 오류')
    for key in ('cycle_id','command_id'):
        if data[key] is not None:identifier(data[key])
    kind=data['kind'];detail=data['data']
    if kind in EVENT_RULES:
        device,_,fields=EVENT_RULES[kind]
        require(data['device_id']==device and data['cycle_id'] is not None and data['command_id'] is not None,'완료 이벤트의 장비·회차·명령 오류',422,'CONFLICT')
        fixed(detail,fields)
    elif kind in ('DEVICE_PAUSED','DEVICE_RESUMED'):
        require(data['device_id'] in DEVICES and data['command_id'] is not None,'정지·재개 명령 ID 오류')
        fixed(detail,PAUSED[data['device_id']] if kind=='DEVICE_PAUSED' else {})
    elif kind=='DEVICE_FAILED':
        require(data['device_id'] in DEVICES,'오류 발신 장비 오류')
        error_payload(detail)
    elif kind in ('FEED_QR_READ','INSPECTION_CAPTURED','INSPECTION_UNAVAILABLE'):
        require(data['device_id']=='VISION' and data['cycle_id'] is not None and data['command_id'] is None,'비전 이벤트 회차 오류')
        if kind=='FEED_QR_READ':
            object_fields(detail,('frame_id','pallet_id','attempt','read_id'))
            identifier(detail['read_id'])
            require(type(detail['attempt']) is int and 1<=detail['attempt']<=3,'QR 시도 번호 오류')
            require(detail['pallet_id'] is None or isinstance(detail['pallet_id'],str) and re.fullmatch(r'PALLET-0[1-8]',detail['pallet_id']),'팔레트 ID 오류')
            require(detail['frame_id'] is None or isinstance(detail['frame_id'],str) and 0<len(detail['frame_id'])<=100,'프레임 ID 오류')
            require(detail['frame_id'] is not None or detail['pallet_id'] is None,'프레임 없는 QR 성공은 허용하지 않습니다.')
        elif kind=='INSPECTION_CAPTURED':vision(detail)
        else:fixed(detail,{'inspection_id':detail.get('inspection_id') if isinstance(detail,dict) else '', 'reason':'NO_FRAME'});identifier(detail['inspection_id'])
    else:raise Fault(400,'BAD_REQUEST','알 수 없는 이벤트 종류입니다.')
    return data


def inspect_result(value,expected,pallet,hub):
    reasons=[]
    missing=any(value[slot]['observation']=='MISSING' for slot in ('head','left','right'))
    unknown=any(value[slot]['observation']=='UNKNOWN' for slot in ('head','left','right'))
    wrong=any(value[slot]['observation']=='SEEN' and value[slot]['observed_id']!=expected[key]
              for slot,key in [('head','target_head_id'),('left','target_arm_l_id'),('right','target_arm_r_id')])
    if missing:reasons.append('PART_MISSING')
    if wrong:reasons.append('WRONG_PART')
    if value['qr_read'] is None:reasons.append('QR_UNREADABLE')
    elif value['qr_read']!=pallet:reasons.append('QR_MISMATCH')
    if unknown:reasons.append('OCCLUDED')
    if missing:return 'PART_MISSING',reasons,6
    if wrong or 'QR_MISMATCH' in reasons:return 'MISMATCH',reasons,7
    if reasons:return 'INDETERMINATE',reasons,7
    return 'PASS',[],hub
