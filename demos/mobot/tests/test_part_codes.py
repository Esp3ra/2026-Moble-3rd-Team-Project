import os
import tempfile
import unittest
from contextlib import closing
from html.parser import HTMLParser
from itertools import product
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from app import create_app
from orders import connect, initialize_orders, order_response


class OptionsParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.choices=[]
        self.symbols=set()

    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs)
        if 'id' in attrs:self.symbols.add(attrs['id'])
        if tag=='input' and attrs.get('type')=='radio':self.choices.append(attrs)


class PartCodeTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path=Path(temp.name)/'test.sqlite3'
        with patch.dict(os.environ,{'MOBOT_DB':str(self.path),'MOBOT_EQUIPMENT_MODE':'DEMO'}):self.app=create_app()
        self.app.config.update(LEGACY_API_ENABLED=True,V1_BACKGROUND=False)
        self.client=self.app.test_client()

    def payload(self,head='H1',left='L1',right='R1'):
        return dict(company='부품 코드 검증',phone='010-0000-0000',address='서울특별시',
                    head=head,left=left,right=right,request_id=str(uuid4()))

    def test_all_eighteen_combinations_save_canonical_codes(self):
        for head,left,right in product(['H1','H2'],['L1','L2','L3'],['R1','R2','R3']):
            response=self.client.post('/api/orders',json=self.payload(head,left,right))
            self.assertEqual(response.status_code,201,response.json)
            with closing(connect(self.path)) as db:
                row=db.execute('SELECT * FROM ORDERS WHERE job_id=?',(response.json['id'],)).fetchone()
                self.assertEqual((row['target_head_id'],row['target_arm_l_id'],row['target_arm_r_id'],row['model_name']),
                                 (head,left,right,f'{head}-{left}-{right}'))

    def test_old_web_payload_and_new_payload_are_same_idempotent_order(self):
        old=self.payload('mono','weld','fasten')
        first=self.client.post('/api/orders',json=old)
        second=self.client.post('/api/orders',json={**old,'head':'H2','left':'L2','right':'R3'})
        self.assertEqual((first.status_code,second.status_code),(201,200))
        self.assertEqual(first.json['id'],second.json['id'])

    def test_wrong_side_codes_rejected(self):
        for field,value in [('head','H3'),('left','R1'),('right','L1')]:
            response=self.client.post('/api/orders',json={**self.payload(),field:value})
            self.assertEqual(response.status_code,400)
            self.assertEqual(response.json['field'],field)

    def test_web_choices_keep_svg_preview_symbol(self):
        page=self.client.get('/order').get_data(as_text=True)
        parser=OptionsParser()
        parser.feed(page)
        choices={item['name']:{} for item in parser.choices}
        for item in parser.choices:
            choices[item['name']][item['value']]=item['data-symbol']
            self.assertIn(item['data-symbol'],parser.symbols)
        self.assertEqual(choices,{'head':{'H1':'dual','H2':'mono'},
                                  'left':{'L1':'grip','L2':'weld','L3':'fasten'},
                                  'right':{'R1':'grip','R2':'weld','R3':'fasten'}})
        self.assertIn('집기팔 (L1)',page)
        self.assertIn('나사팔 (R3)',page)

    def test_existing_assembly_and_inspection_records_migrate_without_losing_links(self):
        with closing(connect(self.path)) as db, db:
            db.execute("INSERT INTO ORDERS(job_id,company_name,model_name,target_head_id,target_arm_l_id,target_arm_r_id) VALUES('mobot-1','기존 주문','B3b','head-B','arm-b','arm-3')")
            mapping=db.execute("INSERT INTO JOB_PALLET_MAPPINGS(job_id,pallet_id) VALUES('mobot-1','PALLET-01')").lastrowid
            db.execute("INSERT INTO ASSEMBLY_LOGS(mapping_id,stopper_clamped,assembled_head_id,assembled_arm_l_id,assembled_arm_r_id) VALUES(?,1,'head-A','arm-a','arm-2')",(mapping,))
            db.execute("INSERT INTO INSPECTION_2ND_LOGS(mapping_id,head_model_match,actual_head_id,overall_result,assigned_lane) VALUES(?,1,'head-B','PASS',3)",(mapping,))
        initialize_orders(self.path)
        initialize_orders(self.path)
        with closing(connect(self.path)) as db:
            row=db.execute('SELECT * FROM ORDERS').fetchone()
            self.assertEqual((row['job_id'],row['model_name'],row['target_head_id'],row['target_arm_l_id'],row['target_arm_r_id']),('mobot-1','H2-L2-R3','H2','L2','R3'))
            assembly=db.execute('SELECT assembled_head_id,assembled_arm_l_id,assembled_arm_r_id FROM ASSEMBLY_LOGS').fetchone()
            self.assertEqual(tuple(assembly),('H1','L1','R2'))
            self.assertEqual(db.execute('SELECT actual_head_id FROM INSPECTION_2ND_LOGS').fetchone()[0],'H2')
            self.assertEqual(order_response(row)['head'],'mono')
            self.assertEqual(db.execute('SELECT mapping_id FROM JOB_PALLET_MAPPINGS').fetchone()[0],mapping)
            self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall())


if __name__=='__main__':unittest.main()
