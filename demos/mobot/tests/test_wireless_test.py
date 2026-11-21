import json
import unittest
from io import BytesIO
from unittest.mock import patch
from raspi_agent.wireless_test import make_report,send,MESSAGES


class WirelessTests(unittest.TestCase):
    def test_simulated_messages_never_enable_hardware_or_mapping(self):
        body=make_report({'demo':False},'boot',1,[{'event':'UNO TEST','text':MESSAGES[0]}],[])
        self.assertIsNone(body['run_id'])
        self.assertFalse(body['status']['connected'])
        self.assertFalse(body['status']['ready'])
        self.assertFalse(body['status']['camera_ok'])
        self.assertEqual(body['status']['trial'],{})
        self.assertEqual(body['status']['mode'],'LIVE')
        self.assertEqual(body['status']['events'][0]['event'],'UNO TEST')

    def test_standard_library_request_uses_existing_token_and_json_api(self):
        config=dict(server_url='https://example.invalid',device_id='uno-pi',device_token='test-token')
        body=make_report(config,'boot',1,[],[])
        with patch('urllib.request.urlopen',return_value=BytesIO(b'{"command":null}')) as opened:
            self.assertEqual(send(config,body),{'command':None})
        request=opened.call_args.args[0]
        self.assertEqual(request.full_url,'https://example.invalid/api/devices/uno-pi/poll')
        self.assertEqual(request.get_header('Authorization'),'Bearer test-token')
        self.assertEqual(json.loads(request.data)['status']['diagnostic'],'NETWORK_ONLY_SIMULATED_MESSAGES')


if __name__=='__main__':unittest.main()
