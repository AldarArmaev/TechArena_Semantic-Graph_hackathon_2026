import json
import unittest
from pathlib import Path
import javalang as j
from ttsolver.catalog import Catalog
from ttsolver.sql import Database
from ttsolver.engine import Engine, Frame, NeedChoice, JavaException
from ttsolver.values import Obj, Unknown
from ttsolver.runner import run_scenario
from ttsolver.planner import Planner
from ttsolver.events import AnnotationPolicy

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
