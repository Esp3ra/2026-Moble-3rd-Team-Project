"""Validate actual Flask responses against GitHub's OpenAPI, entirely in memory."""
import argparse
import base64
import json
import os
import sys
import urllib.request
import urllib.parse
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator,FormatChecker

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from test_factory_v1 import FactoryTests
from factory_v1.common import uid

REVISION='5b33db700f86638c971dcf40315b3fdc8ccffbbd'


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--stdin',action='store_true');args=parser.parse_args()
    if args.stdin:source=json.loads(sys.stdin.readline())['openapi']
    else:
        path=urllib.parse.quote('docs/통신규약/openapi.yaml',safe='/')
        url=f'https://api.github.com/repos/Esp3ra/2026-Moble-3rd-Team-Project/contents/{path}?ref={REVISION}'
        headers={'Accept':'application/vnd.github+json'}
        if os.environ.get('GITHUB_TOKEN'):headers['Authorization']='Bearer '+os.environ['GITHUB_TOKEN']
        with urllib.request.urlopen(urllib.request.Request(url,headers=headers),timeout=20) as response:
            source=base64.b64decode(json.load(response)['content']).decode()
    document=yaml.safe_load(source)
    case=FactoryTests('test_stock_shared_and_public_privacy');case.setUp();checks=[]
    def call(path,method='get',data=None,auth=None,actual=None):
        kwargs={}
        if data is not None:kwargs['json']=data
        if auth is not None:kwargs['headers']=auth
        response=getattr(case.client,method)(actual or path,**kwargs)
        operation=document['paths'][path][method]
        expected=operation['responses'].get(str(response.status_code))
        assert expected,f'Undeclared status {response.status_code}: {method} {path} {response.json}'
        if '$ref' in expected:expected=document['components']['responses'][expected['$ref'].split('/')[-1]]
        content=expected.get('content',{})
        if 'application/json' in content:
            schema={'$schema':'https://json-schema.org/draft/2020-12/schema',**content['application/json']['schema'],'components':document['components']}
            Draft202012Validator(schema,format_checker=FormatChecker()).validate(response.json)
        elif 'image/jpeg' in content:
            assert response.data.startswith(b'\xff\xd8')
            assert response.headers['Cache-Control']=='no-store' and response.headers['X-Captured-At'].endswith('Z')
        checks.append((method,path,response.status_code))
        return response
    try:
        a,s=case.auth,case.super_auth
        call('/api/v1/catalog');call('/api/v1/auth/login','post',{'login_id':'admin','password':'test'})
        call('/api/v1/auth/me',auth=a);call('/api/v1/orders',auth=a);call('/api/v1/pallets',auth=a)
        call('/api/v1/plant',auth=a);call('/api/v1/stock',auth=a);call('/api/v1/accounts',auth=s)
        data=dict(request_id=uid(),company='규약 검증',phone='000-0000',address='광주광역시 서구',spec=dict(target_head_id='H1',target_arm_l_id='L1',target_arm_r_id='R1'),payment_method='card')
        order=call('/api/v1/orders','post',data).json;order_id=order['order_id']
        call('/api/v1/orders','post',data)
        call('/api/v1/orders/lookup','post',{'order_id':order_id})
        call('/api/v1/orders/{order_id}','get',auth=a,actual='/api/v1/orders/'+order_id)
        call('/api/v1/orders/{order_id}/accept','post',{'request_id':uid()},a,'/api/v1/orders/'+order_id+'/accept')
        call('/api/v1/orders/cancel','post',{'request_id':uid(),'order_id':order_id})
        ticket=call('/api/v1/plant/actions','post',{'request_id':uid(),'action':'START'},a).json
        call('/api/v1/plant/actions/{request_id}',auth=a,actual='/api/v1/plant/actions/'+ticket['request_id'])
        case.tick(80)
        detail=call('/api/v1/orders/{order_id}',auth=a,actual='/api/v1/orders/'+order_id).json
        assert detail['factory_status']=='COMPLETED'
        history=call('/api/v1/orders/{order_id}/history',auth=a,actual='/api/v1/orders/'+order_id+'/history').json
        inspection=next(row['inspection_id'] for row in history['events'] if row['inspection_id'])
        info=call('/api/v1/inspections/{inspection_id}',auth=a,actual='/api/v1/inspections/'+inspection).json
        call('/api/v1/inspections/{inspection_id}/image',auth=a,actual='/api/v1/inspections/'+inspection+'/image')
        for camera in ('CAM1','CAM2','CAM3','CAM4'):call('/api/v1/cameras/{camera_id}/frame',auth=a,actual='/api/v1/cameras/'+camera+'/frame')
        call('/api/v1/stock/restocks','post',dict(request_id=uid(),stock_key='ARM_1',added_units=1),s)
        account=call('/api/v1/accounts','post',dict(request_id=uid(),login_id='schema-user',display_name='검증 계정',role='ADMIN',initial_password='test-only'),s).json
        call('/api/v1/accounts/{account_id}/role','patch',dict(request_id=uid(),role='SUPER_ADMIN'),s,'/api/v1/accounts/'+account['account_id']+'/role')
        call('/api/v1/manual-actions','post',dict(request_id=uid(),device_id='LINE',procedure_id='unapproved',reason='규약 검증'),a)
        call('/api/v1/manual-actions/{command_id}',auth=a,actual='/api/v1/manual-actions/'+uid())
        call('/api/v1/commands/{command_id}/reconcile','post',dict(request_id=uid(),observation='UNRESOLVED',reason='규약 검증'),a,'/api/v1/commands/'+uid()+'/reconcile')
        call('/api/v1/settings/{device_id}',auth=s,actual='/api/v1/settings/LINE')
        call('/api/v1/settings/{device_id}','put',dict(request_id=uid(),device_id='LINE',validated_profile_id='unapproved',expected_revision=1),s,'/api/v1/settings/LINE')
        call('/api/v1/settlements/query','post',dict(from_utc='2026-01-01T00:00:00Z',to_utc='2027-01-01T00:00:00Z'),s)
        call('/api/v1/archive/{record_id}/delete','post',dict(request_id=uid(),reason='검증'),s,'/api/v1/archive/'+info['archive_id']+'/delete')
        call('/api/v1/inspections/{inspection_id}/image',auth=a,actual='/api/v1/inspections/'+inspection+'/image')
        agv=case.engine.clients['AGV'];event=agv.log[0]
        call('/api/v1/device-events','post',event,{'Authorization':'Bearer agv-test'})
        control={'Authorization':'Bearer control-test'}
        call('/device/v1/status',auth=control)
        command=next(iter(agv.records))
        call('/device/v1/commands/{command_id}',auth=control,actual='/device/v1/commands/'+command)
        call('/device/v1/events',auth=control,actual=f'/device/v1/events?boot_id={agv.boot}&after_seq=0')
        call('/device/v1/commands','post',json.loads(agv.payloads[command]),{**control,'X-Expected-Boot-Id':agv.boot})
        call('/api/v1/auth/logout','post',auth=a)
        print(f'PASS: {len(checks)} actual responses validated against main {REVISION[:7]} (OpenAPI held only in memory).')
    finally:case.doCleanups()


if __name__=='__main__':main()
