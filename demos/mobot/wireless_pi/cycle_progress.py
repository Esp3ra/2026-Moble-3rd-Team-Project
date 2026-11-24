"""State names from confirmed UNO serial messages, never from guessed timers."""
from datetime import datetime,timezone

STAGES={'STARTING','DISPENSING','REVERSAL','TO_IR1','IR1_DELAY','WAIT_QR','ROBOT_WAIT','TO_IR2','IR2_DELAY','WAIT_RETRACT','DONE','STOPPED','FAILED','UNKNOWN'}
MESSAGES={
 'ACTUATOR:PUSH_10000MS':'DISPENSING',
 'ACTUATOR:PAUSE_500MS':'REVERSAL',
 'RETURN_AND_BELT:STARTED':'TO_IR1',
 'IR_TIMER:STARTED':'IR1_DELAY',
 'SIM:STATION_STOPPED':'WAIT_QR',
 'QR:WAITING':'WAIT_QR',
 'QR:RECOGNIZED_ROBOT_WAIT':'ROBOT_WAIT',
 'SIM:BELT_RESTARTED':'TO_IR2',
 'IR2_TIMER:STARTED':'IR2_DELAY',
 'BELT_STOPPED:IR2_DELAY_DONE':'WAIT_RETRACT',
 'CYCLE_DONE:READY_A':'DONE',
 'STOPPED:BOTH_READY_A':'STOPPED',
}


def progress(command_id,stage,line):
    return dict(command_id=command_id,stage=stage,line=line[:120],changed_at=datetime.now(timezone.utc).isoformat().replace('+00:00','Z'))
