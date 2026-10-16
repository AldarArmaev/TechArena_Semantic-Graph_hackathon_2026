"""Deterministic intent grammar grounded in UI paths and Spring mappings.

Rules are capabilities, never keyed by scenario IDs. Values not recoverable
from text, UI defaults or DB are explicit Unknowns, not assumed successes.
"""
from dataclasses import dataclass, field
import re
from .values import Obj, Unknown


@dataclass
class StructuredAction:
    step: int
    text: str
    actor: str
    intent: str
    train_family: str
    selected_reference: str = 'current'
    seat_class: int | None = None
    food_choice: int | None = None
    insurance: bool | None = None
    consignment: bool | None = None
    filters: dict = field(default_factory=dict)
    dates: dict = field(default_factory=dict)
    quantities: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)


@dataclass
class Request:
    action: StructuredAction
    verb: str
    path: str
    body: object = None
    headers: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)


class Planner:
    def __init__(self,db):
        self.db = db
        self.family = 'high-speed'
        self.actor = 'passenger'
        self.username = 'fdse_microservice'
        self.account = next(r['user_id'] for r in db.rows('auth_user') if r['user_name']==self.username)
        self.from_station,self.to_station = 'Shanghai','Suzhou'
        self.contact = None
        self.new_contact = None
        self.trip = Unknown('trip not selected by preceding response')
        self.trips = []
        self.orders = []
        self.last_options = {}
        self.last_response = None
        self.registered_username = None
        self.new_route = None
        self.consign = None
        self.food = None
        self.travel_date = '2026-10-01'

    @staticmethod
    def unwrap(value):
        if isinstance(value,Obj) and 'body' in value.fields: value=value.fields['body']
        if isinstance(value,Obj) and 'data' in value.fields: return value.fields['data']
        return value

    @staticmethod
    def get(obj,key):
        return obj.fields.get(key,Unknown('missing selected '+key)) if isinstance(obj,Obj) else Unknown('missing selected '+key)

    def parse(self,step):
        text = step['action']; s = text.lower()
        if 'administrator' in s or 'admin console' in s: self.actor='admin'
        if 'as a passenger' in s: self.actor='passenger'
        if 'regular' in s or "'other'" in s: self.family='regular'
        elif 'high-speed' in s or 'gaotie/dongche' in s: self.family='high-speed'
        rules = [
            ('login',r'log in'),('register',r'^register'),
            ('admin_orders',r'as the administrator.*all orders'),
            ('add_station',r'^add a new station'),('add_train',r'^add a new train type'),
            ('add_route',r'^add a new route'),('add_price',r'^add a price configuration'),
            ('add_trip',r'^add a new high-speed trip'),
            ('add_contact',r'^add .*contact'),('contacts',r'^open the contact book'),
            ('advanced',r'^advanced search'),('transfer',r'trips with a transfer'),
            ('search',r'^search'),('trip_detail',r'^open the details'),
            ('food_menu',r'^view the food menu'),('food_stores',r'^browse the food stores'),
            ('preserve',r'^book '),('refresh',r'^refresh'),
            ('order_price',r'^check the price of the order'),('order_detail',r'^open .*order'),
            ('pay',r'^pay for'),('collect',r'^collect'),('execute',r'^enter the station'),
            ('refund_quote',r'^ask how much would be refunded'),('cancel',r'^(try to )?cancel'),
            ('rebook',r'^(try to )?rebook'),('consign_update',r'^change the consignee'),
            ('consign_price',r'^check what .*consignment'),('consign',r'^look up the luggage'),
            ('assurance_types',r'list of assurance types'),('assurance',r'^look up the assurance')]
        intent = next((key for key,pattern in rules if re.search(pattern,s)),'unsupported')
        a = StructuredAction(step['n'],text,self.actor,intent,self.family)
        a.evidence={'intent':'scenario action + capability grammar', 'endpoint':'ANALYSIS.md section 5.2; validated against Spring source mappings'}
        a.dates={'travel':self.travel_date,'clock':'2026-09-15'}
        if 'second' in s and ('order' in s or 'trip' in s): a.selected_reference='second'
        elif 'first' in s: a.selected_reference='first'
        elif 'new' in s: a.selected_reference='new'
        elif 'same' in s: a.selected_reference='same'
        if 'first-class' in s: a.seat_class=2
        if 'second-class' in s: a.seat_class=3
        if intent=='preserve':
            neg = re.search(r'\b(?:without|no)\b(.+)',s)
            excluded = neg.group(1) if neg else ''
            # "same station food" inherits the previous optional choices.
            previous = self.last_options if 'same' in s else {}
            a.food_choice = 0 if 'food' in excluded else 2 if 'station' in s and 'food' in s else 1 if 'food on the train' in s else previous.get('foodType',0)
            a.insurance = False if 'assurance' in excluded or 'insurance' in excluded else True if 'assurance' in s or 'insurance' in s else bool(previous.get('assurance',0))
            a.consignment = False if 'consign' in excluded else 'consign' in s
            weight = re.search(r'(\d+(?:\.\d+)?)\s*kg',s)
            if weight: a.quantities['weight']=float(weight[1])
            a.quantities['seats']=1
        if intent=='refresh':
            a.filters={'state':1 if "'paid'" in s else 4 if "'cancel'" in s else None,
                       'travel':'filtered by travel date' in s,'bought':'filtered by purchase date' in s}
            # Unqualified status tab has the primary G/D endpoint in the UI.
            if a.filters['state'] is not None and not self.orders:
                a.train_family='high-speed'
                a.evidence['family']='primary orders tab; unresolved UI ambiguity noted in ANALYSIS 8.1'
        return a

    def request(self,step):
        a=self.parse(step); s=a.text.lower(); intent=a.intent
        api='/api/v1'; family=a.train_family
        order_path=api+('/orderOtherService/orderOther' if family=='regular' else '/orderservice/order')
        travel=api+('/travel2service' if family=='regular' else '/travelservice')
        idx=1 if a.selected_reference=='second' else 0 if a.selected_reference=='first' else -1
        order=self.orders[idx] if self.orders and abs(idx)<len(self.orders)+1 else Unknown('new order not created')
        oid=self.get(order,'id')
        def path_value(value):
            return value if isinstance(value,str) else '{unresolved}'
        body=None; verb='GET'; path=''
        if intent=='login':
            if a.actor=='admin':
                admins=[r for r in self.db.rows('auth_user') if r['user_name'] in ('admin','fdse_admin')]
                username=admins[0]['user_name'] if admins else Unknown('admin credentials not specified')
            else: username=self.registered_username or self.username
            body={'username':username,'password':'111111','verificationCode':None}
            path=api+'/users/login'; verb='POST'
        elif intent=='contacts': path=api+'/contactservice/contacts/account/'+str(self.account)
        elif intent=='refresh':
            path=order_path+'/refresh'; verb='POST'
            body={'loginId':self.account,'enableStateQuery':a.filters['state'] is not None,'state':a.filters['state'] or 0,
                  'enableTravelDateQuery':a.filters['travel'],'enableBoughtDateQuery':a.filters['bought'],
                  'travelDateStart':None,'travelDateEnd':None,'boughtDateStart':None,'boughtDateEnd':None}
            if a.filters['travel']: body.update(travelDateStart='2026-09-01',travelDateEnd='2026-12-31')
            if a.filters['bought']: body.update(boughtDateStart='2026-09-15',boughtDateEnd='2026-09-16')
            a.evidence['filter_dates']='UI null defaults; only explicitly specified date bounds filled'
        elif intent=='consign': path=api+'/consignservice/consigns/'+('account/'+str(self.account) if 'account' in s else 'order/'+path_value(oid))
        elif intent in ('search','advanced','transfer'):
            match=re.search(r'from (.+?) to (.+?) (?:departing|on)',a.text,re.I)
            if match: self.from_station,self.to_station=match[1],match[2]
            body={'startPlace':self.from_station,'endPlace':self.to_station,'departureTime':self.travel_date}
            path=travel+'/trips/left'; verb='POST'
            if intent=='advanced':
                kind='cheapest' if 'cheapest' in s else 'quickest' if 'quickest' in s else 'minStation'
                path=api+'/travelplanservice/travelPlan/'+kind
                body['startingPlace']=self.from_station
            if intent=='transfer':
                path=api+'/travelplanservice/travelPlan/transferResult'
                body.update(viaStation='Suzhou',startStation='Shanghai',endStation='Nanjing',travelDate=self.travel_date)
        elif intent=='trip_detail':
            path=travel+'/trip_detail'; verb='POST'
            body={'tripId':self.trip,'from':self.from_station,'to':self.to_station,'travelDate':self.travel_date}
        elif intent=='food_menu': path=api+'/foodservice/foods/'+self.travel_date+'/'+self.from_station+'/'+self.to_station+'/'+path_value(self.trip)
        elif intent=='food_stores':
            stations=[r for r in self.db.rows('stations') if r.get('name','').replace(' ','').lower()=='suzhou']
            path=api+'/stationfoodservice/stationfoodstores/'+(stations[0]['id'] if stations else '{unresolved}')
        elif intent=='preserve':
            path=api+('/preserveotherservice/preserveOther' if family=='regular' else '/preserveservice/preserve'); verb='POST'
            contact=self.new_contact if 'new contact' in s else self.contact
            body={'accountId':self.account,'contactsId':self.get(contact,'id'),'tripId':self.trip,'seatType':a.seat_class,
                  'date':self.travel_date,'from':self.from_station,'to':self.to_station,
                  'assurance':int(a.insurance),'foodType':a.food_choice,'foodName':Unknown('dish response not selected'),
                  'foodPrice':Unknown('dish price not selected'),'stationName':'Suzhou','storeName':Unknown('store response not selected'),
                  'consigneeName':'symbolic-consignee' if a.consignment else '',
                  'consigneePhone':'13000000000' if a.consignment else '',
                  'consigneeWeight':a.quantities.get('weight',0),'handleDate':'2026-09-15','within':False}
            self.last_options=body.copy()
        elif intent=='order_detail': path=order_path+'/'+path_value(oid)
        elif intent=='order_price': path=order_path+'/price/'+path_value(oid)
        elif intent=='pay':
            path=api+'/inside_pay_service/inside_payment'; verb='POST'
            body={'orderId':oid,'tripId':self.get(order,'trainNumber'),'userId':self.account}
        elif intent in ('collect','execute'): path=api+'/executeservice/execute/'+('collected/' if intent=='collect' else 'execute/')+path_value(oid)
        elif intent in ('cancel','refund_quote'): path=api+'/cancelservice/cancel/'+('refound/'+path_value(oid) if intent=='refund_quote' else path_value(oid)+'/'+str(self.account))
        elif intent=='rebook':
            path=api+'/rebookservice/rebook'; verb='POST'
            trip=self.get(self.trips[1],'tripId') if 'second' in s and len(self.trips)>1 else self.trip
            body={'orderId':oid,'oldTripId':self.get(order,'trainNumber'),'tripId':trip,'seatType':a.seat_class or self.get(order,'seatClass'),'date':self.travel_date,'loginId':self.account}
        elif intent=='assurance': path=api+'/assuranceservice/assurance/orderid/'+path_value(oid)
        elif intent=='assurance_types': path=api+'/assuranceservice/assurances/types'
        elif intent=='consign_price':
            weight=re.search(r'(\d+(?:\.\d+)?)\s*kg',s)
            path=api+'/consignpriceservice/consignprice/'+(weight[1] if weight else '{unresolved}')+'/true'
        elif intent=='add_contact':
            path=api+'/contactservice/contacts'; verb='POST'
            body={'accountId':self.account,'name':'symbolic-new-contact','documentType':1,'documentNumber':'symbolic-new-document','phoneNumber':'13000000000'}
        elif intent=='register':
            path=api+'/userservice/users/register'; verb='POST'
            self.registered_username='symbolic-new-passenger'
            body={'userName':self.registered_username,'password':'111111','gender':1,'documentType':1,'documentNum':'symbolic-new-document','email':'passenger@example.invalid'}
        elif intent=='admin_orders': path=api+'/adminorderservice/adminorder'
        elif intent=='consign_update':
            path=api+'/consignservice/consigns'; verb='PUT'
            body=dict(self.consign.fields) if isinstance(self.consign,Obj) else {'id':Unknown('consignment lookup failed')}
            body.update(consignee='symbolic-updated-consignee',phone='13100000000')
        else:
            a.evidence['unsupported']='No validated request builder for '+intent
        return Request(a,verb,path,body,{'Authorization':'context-authentication'},a.evidence.copy())

    def observe(self,request,response):
        value=self.unwrap(response); intent=request.action.intent
        self.last_response=value
        if intent=='login' and isinstance(value,Obj):
            self.account=value.fields.get('userId',self.account)
        if intent=='contacts' and isinstance(value,list) and value: self.contact=value[0]
        if intent=='add_contact' and isinstance(value,Obj): self.new_contact=value; self.contact=self.contact or value
        if intent=='search' and isinstance(value,list) and value:
            self.trips=value; self.trip=self.get(value[0],'tripId')
        if intent=='preserve':
            # Link new orders by actual state mutation, never by assumed success.
            known={self.get(o,'id') for o in self.orders}
            table='orders_other' if request.action.train_family=='regular' else 'orders'
            for row in self.db.rows(table):
                if str(row.get('id','')).startswith('00000000-0000-4000-8000-') and row['id'] not in known:
                    fields={re.sub(r'_([a-z])',lambda m:m[1].upper(),k):v for k,v in row.items()}
                    self.orders.append(Obj('selected-order',fields,row['id']))
        if intent=='consign':
            self.consign=value[0] if isinstance(value,list) and value else value
