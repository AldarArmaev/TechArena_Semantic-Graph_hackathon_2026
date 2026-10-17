import json
import unittest
from pathlib import Path
from decimal import Decimal
import javalang as j
from ttsolver.catalog import Catalog
from ttsolver.sql import Database
from ttsolver.engine import Engine, Frame, NeedChoice, JavaException
from ttsolver.values import Obj, Unknown, TypeRef
from ttsolver.runner import run_scenario
from ttsolver.planner import Planner
from ttsolver.events import AnnotationPolicy
from ttsolver.audit import summarize

ROOT=Path(__file__).resolve().parents[2]


class ExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c=Catalog(ROOT/'skeleton/trainticket.json.gz',ROOT/'targets/trainticket')
        cls.db=Database(ROOT/'context/db-after-warmup.sql.gz')

    def engine(self):
        e=Engine(self.c,self.db.clone(),'fixture')
        tid=self.c.resolve_type('OrderInfo',module='ts-order-service')
        mid=self.c.method(tid,'getState',0)
        e.stack.append(Frame(mid,Obj(tid,{}),{}))
        return e

    def test_short_circuit(self):
        e=self.engine()
        self.assertFalse(e.eval(j.parse.parse_expression('false && missing.call()')))
        self.assertTrue(e.eval(j.parse.parse_expression('true || missing.call()')))
        self.assertTrue(all(v.kind=='Branch' for v in e.events))

    def test_null_and_empty(self):
        e=self.engine()
        self.assertFalse(e.eval(j.parse.parse_expression('null == ""')))
        self.assertTrue(e.eval(j.parse.parse_expression('"".isEmpty()')))
        with self.assertRaises(JavaException): e.eval(j.parse.parse_expression('((String)null).length()'))

    def test_unknown_branch_is_suspended(self):
        e=self.engine()
        with self.assertRaises(NeedChoice): e.branch(Unknown('missing request field'))
        self.assertFalse(any(x.kind=='DirectProjectCall' for x in e.events))

    def test_option_guards_from_actual_source(self):
        tid=self.c.resolve_type('PreserveServiceImpl',module='ts-preserve-service')
        mid=self.c.method(tid,'preserve',2)
        e=Engine(self.c,self.db.clone(),'fixture')
        dto=self.c.resolve_type('OrderTicketsInfo',mid)
        e.stack.append(Frame(mid,e.bean(tid),{'oti':Obj(dto,{'assurance':0,'foodType':0,'consigneeName':''})}))
        conditions=[n.condition for _,n in self.c.ast[mid].filter(j.tree.IfStatement)]
        checks={}
        for cond in conditions:
            calls=[n.member for _,n in cond.filter(j.tree.MethodInvocation)]
            for key in ('getAssurance','getFoodType','getConsigneeName'):
                if key in calls and key not in checks: checks[key]=e.eval(cond)
        self.assertEqual(checks,{'getAssurance':True,'getFoodType':False,'getConsigneeName':False})
        self.assertFalse(any(x.kind in ('HttpRequestSent','MessageSent') for x in e.events))

    def test_prefix_dispatch(self):
        e=self.engine()
        expr=j.parse.parse_expression('id.startsWith("G") || id.startsWith("D")')
        for train,expected in [('G1',True),('D2',True),('Z3',False)]:
            e.stack[-1].env['id']=train
            self.assertEqual(e.eval(expr),expected)

    def test_java_library_models_used_by_payment_and_cancel(self):
        e=self.engine()
        iterator=e.adapters.call([1,2],'iterator',[],None,None)
        self.assertTrue(e.adapters.call(iterator,'hasNext',[],None,None))
        self.assertEqual(e.adapters.call(iterator,'next',[],None,None),1)
        self.assertEqual(e.adapters.call(Decimal('3.5'),'add',[Decimal('1.5')],None,None),Decimal('5.0'))
        headers=Obj('HttpHeaders',{'Authorization':'token'})
        self.assertTrue(e.adapters.call(headers,'containsKey',['Authorization'],None,None))
        self.assertEqual(e.adapters.call(headers,'getFirst',['Authorization'],None,None),'token')

    def test_java_float_max_value_is_numeric(self):
        e=self.engine()
        sentinel=e.field(TypeRef('Float'),'MAX_VALUE')
        self.assertTrue(e.binary('<',Decimal('250'),sentinel))

    def test_price_request_numeric_fields_are_deserialized(self):
        e=self.engine()
        mid,match=self.c.route('POST','/api/v1/priceservice/prices')
        self.assertIsNotNone(mid)
        target=self.c.resolve_type('PriceConfig',mid)
        body={'routeId':'route-id','trainType':'GaoTieSix',
              'basicPriceRate':'0.5','firstClassPriceRate':'1.2'}
        converted=e.adapters.request_object(target,body)
        self.assertEqual(converted.fields['basicPriceRate'],Decimal('0.5'))
        self.assertEqual(converted.fields['firstClassPriceRate'],Decimal('1.2'))
        self.assertEqual(converted.fields['trainType'],'GaoTieSix')

    def test_path_variable_name_mismatch_stops_before_handler(self):
        e=self.engine()
        mid,match=self.c.route('GET','/api/v1/stationfoodservice/stationfoodstores/suzhou')
        self.assertIsNotNone(mid)
        with self.assertRaises(JavaException) as raised:
            e.adapters.bind_handler(mid,match,None)
        self.assertEqual(raised.exception.kind,'MissingPathVariableException')
        self.assertFalse(any(x.kind=='MethodEntered' and x.method==mid for x in e.events))

    def test_regular_order_endpoint_and_rebook_reference(self):
        p=Planner(self.db.clone())
        p.orders=[Obj('selected-order',{'id':'order-one','trainNumber':'Z1234','seatClass':2})]
        p.trips=[Obj('trip',{'tripId':'Z1234'}),Obj('trip',{'tripId':'Z1235'})]
        p.trip='Z1234'
        request=p.request({'n':1,'action':'Try to rebook the collected order onto the second regular trip found'})
        self.assertEqual(request.body['orderId'],'order-one')
        self.assertEqual(request.body['tripId'],'Z1235')
        self.assertEqual(request.verb,'POST')

    def test_admin_state_is_visible_to_later_search(self):
        scenario=json.loads((ROOT/'scenarios/S6.json').read_text())
        _,report=run_scenario(self.c,self.db,scenario)
        self.assertEqual(report['halted_paths'],0)
        search=next(x for x in report['steps'] if x['step']==8)
        self.assertTrue(any(e['kind']=='SqlAccess' for e in search['events']))
        self.assertNotIn('Unknown(reason=',str(search['response'])[:60])
        payment=next(x for x in report['steps'] if x['action']['intent']=='pay')
        self.assertEqual(payment['response']['fields']['body']['fields']['status'],1)
        self.assertTrue(any(e['kind']=='HttpHandlerEntered' and 'modifyOrder' in str(e['target'])
                            for e in payment['events']))

    def test_cheapest_search_has_no_float_sentinel_branches(self):
        scenario=json.loads((ROOT/'scenarios/S5.json').read_text())
        _,report=run_scenario(self.c,self.db,scenario)
        self.assertEqual(report['halted_paths'],0)
        self.assertEqual(report['paths'],1)
        self.assertFalse(any(e['kind']=='Branch' and e['confidence']=='AMBIGUOUS'
                             for x in report['steps'] for e in x['events']))

    def test_compact_audit_tracks_binding_and_state(self):
        report={'id':'fixture','paths':1,'feasible_paths':1,'inconsistent_paths':0,
                'halted_paths':0,'conditional_edges':[],
                'steps':[{'step':1,'action':{'intent':'book','actor':'passenger',
                                           'train_family':'regular'},
                          'request':{'verb':'POST','path':'/orders','body':{'seatType':2}},
                          'response':{'fields':{'body':{'fields':{'status':1}}}},
                          'events':[{'kind':'StateMutation','values':{'table':'orders_other'}},
                                    {'kind':'BindingFailure','evidence':'missing variable'},
                                    {'kind':'Branch','confidence':'AMBIGUOUS','evidence':'unknown seat',
                                     'method':'method:x'}]}]}
        audit=summarize(report)
        self.assertEqual(audit['steps'][0]['state_changes'],['orders_other'])
        self.assertEqual(audit['steps'][0]['binding_failures'],['missing variable'])
        self.assertEqual(audit['uncertainty_clusters'][0]['reason'],'unknown seat')

    def test_regular_fallback_probe_and_collected_refusal(self):
        regular=json.loads((ROOT/'scenarios/S2.json').read_text())
        _,report=run_scenario(self.c,self.db,regular)
        cancel=next(x for x in report['steps'] if x['action']['intent']=='cancel')
        sends=[e['evidence'] for e in cancel['events'] if e['kind']=='HttpRequestSent']
        self.assertTrue(any('ts-order-service/' in uri for uri in sends))
        self.assertTrue(any('ts-order-other-service/' in uri for uri in sends))
        self.assertTrue(any(e['kind']=='SqlAccess' and 'ts-order-service.orders' in str(e['target'])
                            for e in cancel['events']))
        self.assertTrue(any(e['kind']=='SqlAccess' and 'ts-order-other-service.orders_other' in str(e['target'])
                            for e in cancel['events']))
        collected=json.loads((ROOT/'scenarios/S7.json').read_text())
        _,report=run_scenario(self.c,self.db,collected)
        for intent in ('cancel','rebook'):
            refused=next(x for x in report['steps'] if x['action']['intent']==intent)
            self.assertEqual(refused['response']['fields']['body']['fields']['status'],0)
            self.assertFalse(any(e['kind']=='StateMutation' for e in refused['events']))

    def test_optional_food_and_delivery_are_guarded(self):
        cases=[('S1',1),('S2',1),('S7',0)]
        for sid,expected in cases:
            scenario=json.loads((ROOT/f'scenarios/{sid}.json').read_text())
            _,report=run_scenario(self.c,self.db,scenario)
            first=next(x for x in report['steps'] if x['action']['intent']=='preserve')
            sends=[e for e in first['events'] if e['kind']=='MessageSent']
            self.assertEqual(len(sends),expected,sid)

    def test_empty_repository_still_sql(self):
        e=Engine(self.c,self.db.clone(),'fixture')
        tid=self.c.resolve_type('ContactsServiceImpl',module='ts-contacts-service')
        mid=self.c.method(tid,'findContactsByAccountId',2)
        e.invoke(mid,['not-an-account',Obj('HttpHeaders',{})])
        self.assertEqual(len([v for v in e.events if v.kind=='SqlAccess']),1)
        self.assertEqual(next(v for v in e.events if v.kind=='RepositoryOperation').values['rows'],0)

    def test_negations(self):
        p=Planner(self.db.clone())
        a=p.parse({'n':1,'action':'Book one first-class seat without food, assurance or consignment'})
        self.assertEqual((a.food_choice,a.insurance,a.consignment,a.seat_class),(0,False,False,2))

    def test_s0_regression(self):
        scenario=json.loads((ROOT/'scenarios/S0.json').read_text())
        block,report=run_scenario(self.c,self.db,scenario)
        gold=json.loads((ROOT/'gold/S0.json').read_text())['scenarios']['S0']
        for rel,key,threshold in [('executes','methods',.75),('calls','calls',.75),('remote','remote',1),('data','data',.85)]:
            predicted={(e['source'],e['target']) for e in block['edges'] if e['relation']==rel}
            expected={('scenario:S0',x) for x in gold[key]} if rel=='executes' else {tuple(x) for x in gold[key]}
            score=len(predicted&expected)/len(predicted|expected) if predicted|expected else 1
            self.assertGreaterEqual(score,threshold)
        self.assertEqual(report['halted_paths'],0)
        # Framework callbacks execute, but no invented application -> callback calls.
        self.assertTrue(any('UserDetailsServiceImpl#loadUserByUsername' in e['target'] for e in block['edges'] if e['relation']=='executes'))
        self.assertFalse(any('UserDetailsServiceImpl#loadUserByUsername' in e['target'] for e in block['edges'] if e['relation']=='calls'))


if __name__=='__main__': unittest.main()
