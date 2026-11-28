import json
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[3]
sys.path[:0]=[str(ROOT/'server/mobot/scripts'),str(ROOT/'AGV'),str(ROOT/'AGV/tests')]
import live_agv_host
import test_device_adapter as inert


class LiveHostTests(unittest.TestCase):
    def test_idle_recovery_preserves_journal_and_http_listener_without_travel(self):
        physical=inert.Device();physical.raw['control_connected']=False
        class Client:
            heartbeat_error=None
            def status(self):return physical.status()
            def connect(self):physical.raw['control_connected']=True
            def suspend_heartbeat(self):pass
            def stop(self):physical.raw.update(state='stopped',fork_moving=False)
            def close(self):self.stop();physical.raw['control_connected']=False
            def set_position(self,point):physical.raw.update(state='ready',position=point)
            def set_fork(self,angle,operation_id=None):physical.raw.update(servo_angle=angle,fork_moving=False,operation_id=operation_id)
        with tempfile.TemporaryDirectory() as directory,patch.object(live_agv_host,'AGVClient',side_effect=lambda *a,**kw:Client()):
            journal=Path(directory)/'agv.sqlite3'
            p=dict(listen_host='127.0.0.1',listen_port=0,device_url='http://127.0.0.1:9',heartbeat_seconds=.3,
                device_request_timeout_seconds=1,journal_path=str(journal),command_timeout_seconds=90,
                events_url='http://127.0.0.1:9/api/v1/device-events',poll_seconds=.01)
            host=live_agv_host.LiveAgvHost(p,'test-key','controller-'+32*'x','event-'+32*'y')
            try:
                host.start(2);port=host.server.server_port;boot=host.adapter.boot_id
                physical.raw.update(state='line_lost',servo_angle=110,position=0)
                host.recover_standby('Operator confirmed physical point 2 and unloaded stopped hardware')
                self.assertEqual(host.server.server_port,port)
                self.assertNotEqual(host.adapter.boot_id,boot)
                self.assertEqual((physical.raw['position'],physical.raw['servo_angle']),(2,85))
                self.assertEqual(host.adapter.status()['state'],'IDLE')
                self.assertEqual(physical.calls,[],'Recovery must not request travel')
                with closing(sqlite3.connect(journal)) as db:
                    self.assertEqual(db.execute('select count(*) from agv_commands').fetchone()[0],0)
                    self.assertEqual(db.execute('select count(*) from agv_boots').fetchone()[0],2)
                    self.assertFalse(json.loads(db.execute("select value from agv_meta where key='lane'").fetchone()[0])['recovery'])
                    self.assertIsNotNone(db.execute("select value from agv_meta where key='operator_recovery'").fetchone())
                host.adapter.lane['cycle_id']='active-cycle'
                with self.assertRaises(ValueError):host.recover_standby('Do not abandon active work')
                host.adapter.lane['cycle_id']=None
            finally:host.close()


if __name__=='__main__':unittest.main()
