import unittest
from raspi_agent.serial_probe import forward,report


class SerialProbeTests(unittest.TestCase):
    def test_readiness_and_camera_remain_disabled(self):
        event=dict(at='2026-11-21T00:00:00Z',event='UNO',text='PI_TEST:001')
        status=report([event])
        self.assertEqual(status['events'][0]['text'],'PI_TEST:001')
        self.assertFalse(status['ready'])
        self.assertFalse(status['clear_view'])
        self.assertFalse(status['camera_ok'])
        self.assertEqual(status['trial'],{})

    def test_commands_are_reported_rejected_instead_of_executed(self):
        class FakeClient:
            def post(self,action,data,boot):
                self.data=data
                return {'command':{'command_id':'test-start','action':'start'}}
        client=FakeClient()
        results=forward(client,'boot',1,[],[])
        self.assertFalse(results[0]['ok'])
        self.assertEqual(results[0]['command_id'],'test-start')
        self.assertIsNone(client.data['run_id'])


if __name__=='__main__':unittest.main()
