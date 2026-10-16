"""Library, Spring, HTTP, RabbitMQ and JPA effect models.

Called only after Java evaluation reaches the corresponding invocation.
"""
import re
from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit
import javalang as j
from .catalog import type_name, annotation_value
from .values import Obj, TypeRef, Unknown, OptionalValue, Deferred, NonNull, unknown
from .engine import JavaException, Limit


class Adapters:
    def __init__(self,engine): self.e = engine

    def construct(self,name,args):
        short = name.split('.')[-1]
        if short in ('ArrayList','LinkedList','HashSet','Set','List','TreeSet'):
            return list(args[0]) if args and isinstance(args[0],(list,set,tuple)) else []
        if short in ('HashMap','LinkedHashMap','TreeMap'): return {}
        if short in ('BigDecimal','Double','Float'): return self.number(args[0]) if args else Decimal(0)
        if short in ('Integer','Long'): return self.number(args[0],integer=True)
        if short == 'Date':
            if not args: return self.e.now
            if isinstance(args[0],(int,Decimal)): return datetime(1970,1,1)+timedelta(milliseconds=float(args[0]))
            return Unknown('Date constructor')
        if short in ('HttpEntity','ResponseEntity'):
            return Obj(short,{'body':args[0] if args else None})
        if short == 'UsernamePasswordAuthenticationToken':
            return Obj(short,dict(zip(('username','password'),args)))
        if short == 'SimpleDateFormat': return Obj(short,{'format':args[0]})
        return Obj(short,{'arguments':args})

    @staticmethod
    def number(v,integer=False):
        if isinstance(v,Unknown): return v
        try: return int(v) if integer else Decimal(str(v))
        except (TypeError,ValueError,InvalidOperation): raise JavaException('NumberFormatException')

    def repository(self,receiver,name,args,site):
        e,c = self.e,self.e.c
        decl = c.type_ast[receiver.type]
        bases = decl.extends or []
        base = next((b for b in bases if getattr(b,'arguments',None)),None)
        if base is None: return e.diagnostic('repository entity type missing '+receiver.type)
        entity = c.resolve_type(type_name(base.arguments[0].type),e.mid)
        if entity not in c.entities: return e.diagnostic('entity mapping missing '+entity)
        table,columns = c.entities[entity]
        targets = c.links.get(site,{}).get('data_access',[])
        primary = [t for t in targets if t.rsplit('.',1)[-1] == table]
        if not primary:
            return e.diagnostic('repository call lacks primary SQL candidate '+str(site))
        for target in primary: e.event('SqlAccess',target,site,'reached repository '+name)
        rows = e.db.rows(table,c.types[entity]['module'])
        methods = [m for m in decl.methods if m.name == name and len(m.parameters)==len(args)]
        return_type = type_name(methods[0].return_type) if methods else ''
        idfield = next((n for n,(_,_,anns) in c.fields[entity].items() if any(a.name=='Id' for a in anns)),'id')
        idcol = columns.get(idfield,idfield)
        if name in ('save','saveAndFlush'):
            obj = args[0]
            if not isinstance(obj,Obj): return e.diagnostic('save unknown entity')
            if obj.fields.get(idfield) is None: obj.fields[idfield] = e.db.new_id()
            row = {col:obj.fields.get(field) for field,col in columns.items() if col in e.db.columns[e.db.key(table)]}
            old = next((r for r in rows if r.get(idcol)==row.get(idcol)),None)
            if old is None: rows.append(row)
            else: old.update(row)
            e.event('RepositoryOperation',site=site,evidence=name,values={'table':table})
            e.event('StateMutation',site=site,evidence=name,values={'table':table,'id':row.get(idcol)})
            return obj
        if name.startswith('delete'):
            value = args[0].fields.get(idfield) if isinstance(args[0],Obj) else args[0]
            if isinstance(value,Unknown): return e.diagnostic('delete unresolved identity')
            rows[:] = [r for r in rows if r.get(idcol)!=value]
            e.event('StateMutation',site=site,evidence=name,values={'table':table,'id':value})
            return None
        if name in ('findAll','count'):
            selected = list(rows)
        elif 'By' in name:
            suffix = name.split('By',1)[1]
            predicates = re.split('And',suffix)
            if len(predicates)!=len(args): return e.diagnostic('unsupported repository predicate '+name)
            filters = []
            for predicate,value in zip(predicates,args):
                field = predicate[0].lower()+predicate[1:]
                if field not in columns:
                    return e.diagnostic('unresolved repository field '+field)
                if isinstance(value,Unknown): return value
                if isinstance(value,Obj) and 'Embedded' in {a.name for a in c.fields[entity][field][2]}:
                    for sub,val in value.fields.items():
                        if isinstance(val,Obj) and val.identity:
                            enum = c.type_ast.get(val.type)
                            val = next((i for i,x in enumerate(enum.body.constants) if x.name==val.identity),None)
                        filters.append((c.snake(sub),val))
                else: filters.append((columns[field],value))
            selected = [r for r in rows if all(r.get(col)==val for col,val in filters)]
        else: return e.diagnostic('unsupported repository method '+name)
        e.event('RepositoryOperation',site=site,evidence=name,values={'table':table,'rows':len(selected)})
        if name == 'count': return len(selected)
        if name.startswith('exists'): return bool(selected)
        objects = []
        for row in selected:
            obj = e.construct(entity,[],materialize=True)
            for field,col in columns.items():
                if col in row: obj.fields[field] = row[col]
                ft,_,anns = c.fields[entity][field]
                if any(a.name=='Embedded' for a in anns):
                    embedded = c.resolve_type(ft,module=c.types[entity]['module'])
                    values = {}
                    for sub,(st,_,_) in c.fields.get(embedded,{}).items():
                        val = row.get(c.snake(sub))
                        et = c.resolve_type(st,module=c.types[embedded]['module'])
                        enum = c.type_ast.get(et)
                        if isinstance(enum,j.tree.EnumDeclaration) and isinstance(val,int):
                            val = e.field(TypeRef(et),enum.body.constants[val].name)
                        values[sub]=val
                    obj.fields[field]=Obj(embedded,values)
            obj.identity = str(row.get(idcol))
            obj.origin = (e.mid,site,tuple(targets),table,row)
            objects.append(obj)
        if return_type == 'Optional' or (name=='findById' and not return_type):
            return OptionalValue(objects[0] if objects else None)
        if return_type in ('List','ArrayList','Set','Collection','Iterable') or name == 'findAll': return objects
        return objects[0] if objects else None

    def collection_read(self,obj,field):
        e,c = self.e,self.e.c
        if not obj.origin: return
        meta = c.fields.get(obj.type,{}).get(field)
        if not meta or not any(a.name=='ElementCollection' for a in meta[2]): return
        owner,site,targets,table,row = obj.origin
        # Auth callbacks are a separate persistence/annotation boundary. Their
        # role collection is used internally; projected lazy SQL is attributed
        # to the application repository load when it accesses the collection.
        if e.callback_depth:
            return
        expected = annotation_value(meta[2],'CollectionTable','name')
        matches = [t for t in targets if t.rsplit('.',1)[-1] == expected] if expected else [t for t in targets if t.rsplit('.',1)[-1].endswith('_'+c.snake(field))]
        for target in matches:
            e.event('SqlAccess',target,site,'lazy collection '+field,method=owner,confidence='STRONG')
            data = e.db.rows(target.rsplit('.',1)[-1])
            identity_values = {str(v) for k,v in row.items() if k=='id' or k.endswith('_id')}
            matching = [r for r in data if any(str(v) in identity_values for k,v in r.items() if k.endswith('_id'))]
            ordercol = annotation_value(meta[2],'OrderColumn','name')
            if ordercol: matching.sort(key=lambda r:r.get(ordercol,0))
            col = c.snake(field)
            obj.fields[field] = [r[col] for r in matching if col in r]

    def authentication(self,token,site):
        e,c = self.e,self.e.c
        if not isinstance(token,Obj): return e.diagnostic('unknown authentication token')
        tid = c.resolve_type('UserDetailsServiceImpl',e.mid)
        mid = c.method(tid,'loadUserByUsername',1)
        if not mid: return e.diagnostic('UserDetailsService not resolved')
        e.event('FrameworkCallback',mid,site,'AuthenticationManager -> UserDetailsService')
        e.callback_depth += 1
        try:
            user = e.invoke(mid,[token.fields['username']])
            if not isinstance(user,Obj): raise JavaException('AuthenticationException')
            for name in ('getAuthorities','getPassword','isAccountNonExpired','isAccountNonLocked','isCredentialsNonExpired','isEnabled'):
                callback = c.method(user.type,name,0)
                if callback:
                    e.event('FrameworkCallback',callback,site,'Spring UserDetails check')
                    e.invoke(callback,[],user)
        finally: e.callback_depth -= 1
        e.event('Assumption',site=site,evidence='Credentials supplied by execution context; bcrypt verification abstracted',confidence='STRONG')
        return NonNull('Authentication')

    def filter(self):
        e,c = self.e,self.e.c
        tid = c.resolve_type('JWTFilter',module='ts-common')
        mid = c.method(tid,'doFilterInternal',3)
        if mid:
            e.event('FrameworkCallback',mid,evidence='configured request security filter')
            e.event('MethodEntered',method=mid,evidence='AnnotationPolicy: opaque JWT request filter boundary',confidence='STRONG')

    def http(self,args,site):
        e,c = self.e,self.e.c
        url,verb,entity = args[:3]
        if not isinstance(url,str): return e.diagnostic('unresolved HTTP URL '+repr(url))
        verb = verb.name.rsplit('.',1)[-1] if isinstance(verb,TypeRef) else str(verb)
        parts = urlsplit(url)
        mid,match = c.route(verb,parts.path,parts.hostname)
        e.event('HttpRequestSent',site=site,evidence=verb+' '+url,values={'body':entity})
        if mid is None:
            e.diagnostic('no unique HTTP handler for '+verb+' '+url)
            raise JavaException('HttpClientErrorException')
        body = entity.fields.get('body') if isinstance(entity,Obj) else entity
        e.event('HttpHandlerEntered',mid,site,'exact verb/path/deployment match')
        self.filter()
        result=self.bind_handler(mid,match,body)
        return result if isinstance(result,Obj) and result.type in ('ResponseEntity','HttpEntity') else Obj('ResponseEntity',{'body':result})

    def bind_handler(self,mid,match,body):
        e,c = self.e,self.e.c
        astnode = c.ast[mid]
        template = next(path for _,path,pattern,m in c.routes if m==mid and pattern.fullmatch(match.string))
        names = re.findall(r'\{([^}]+)\}',template)
        pathvalues = dict(zip(names,match.groups()))
        args = []
        for p in astnode.parameters:
            anns = {a.name for a in p.annotations}
            t = type_name(p.type)
            if 'PathVariable' in anns:
                key = annotation_value(p.annotations,'PathVariable') or annotation_value(p.annotations,'PathVariable','name') or p.name
                value = pathvalues.get(key,Unknown('path variable '+key))
                if t in ('int','Integer','long','Long'): value = self.number(value,True)
                elif t in ('double','Double','float','Float'): value = self.number(value)
                elif t in ('boolean','Boolean'): value = value=='true'
                args.append(value)
            elif 'RequestHeader' in anns or t == 'HttpHeaders': args.append(Obj('HttpHeaders',{}))
            else:
                target = c.resolve_type(t,mid)
                if isinstance(body,dict):
                    value = Obj(target,{n:e.default(ft) for n,(ft,_,_) in c.fields.get(target,{}).items()})
                    value.fields.update(deepcopy(body))
                elif isinstance(body,Obj) and target in c.types:
                    value = deepcopy(body)
                    value.type = target
                else: value = deepcopy(body)
                args.append(value)
        return e.invoke(mid,args)

    def message(self,args,site):
        e,c = self.e,self.e.c
        listeners = c.links.get(site,{}).get('message_delivery',[])
        e.event('MessageSent',site=site,values={'arguments':args})
        if len(listeners)!=1: return e.diagnostic('ambiguous message delivery')
        target = listeners[0]
        e.event('MessageDelivered',target,site,'reached send; configured queue binding',confidence='STRONG')
        return e.invoke(target,[args[-1]])

    def call(self,r,name,args,site,node):
        e = self.e
        typename = r.name if isinstance(r,TypeRef) else r.type if isinstance(r,Obj) else type(r).__name__
        short = typename.rsplit(':',1)[-1].split('.')[-1]
        if short=='LoggerFactory' and name=='getLogger': return Obj('Logger',{})
        if short=='Collectors' and name=='toList': return TypeRef('Collector')
        if short=='ResponseEntity' and name=='ok': return Obj('ResponseEntity',{'body':args[0]})
        if short=='ResponseEntity' and name=='status': return Obj('ResponseBuilder',{})
        if short=='ResponseBuilder' and name=='body': return Obj('ResponseEntity',{'body':args[0]})
        if name=='builder' and isinstance(r,TypeRef) and r.name in e.c.type_ast:
            decl=e.c.type_ast[r.name]
            if any(a.name=='Builder' for a in decl.annotations): return Obj('LombokBuilder',{'type':r.name,'fields':{}})
        if short=='LombokBuilder':
            if name=='build': return Obj(r.fields['type'],dict(r.fields['fields']))
            if len(args)==1: r.fields['fields'][name]=args[0]; return r
        if short=='PasswordEncoder' and name=='encode': return 'modeled-hash:'+str(args[0])
        if short=='DiscoveryClient' and name=='getInstances':
            return [Obj('ServiceInstance',{'host':args[0],'port':0})]
        if short=='MessageFormat' and name=='format': return str(args[0])
        if name in ('info','warn','error','debug','trace') and ('Logger' in typename or 'LOGGER' in typename or 'log' == short): return None
        if name == 'authenticate' and short == 'AuthenticationManager': return self.authentication(args[0],site)
        if name == 'exchange' and short == 'RestTemplate': return self.http(args,site)
        if name == 'convertAndSend': return self.message(args,site)
        if isinstance(r,OptionalValue):
            if name == 'isPresent': return r.value is not None
            if name in ('get','orElseThrow'):
                if r.value is not None: return r.value
                if args and isinstance(args[0],Deferred):
                    value = e.eval(args[0].node.body)
                    raise JavaException(value.type if isinstance(value,Obj) else 'Exception')
                raise JavaException('NoSuchElementException')
            if name == 'orElse': return r.value if r.value is not None else args[0]
        if name == 'equals' and args:
            u = unknown(r,args[0])
            return u if u is not None else r == args[0]
        if name == 'toString' and not args:
            return r if isinstance(r,Unknown) else e.java_string(r)
        if isinstance(r,list):
            if name in ('size','length'): return len(r)
            if name == 'isEmpty': return not r
            if name in ('get','set'):
                if isinstance(args[0],Unknown): return args[0]
                idx = int(args[0])
                if not 0<=idx<len(r): raise JavaException('IndexOutOfBoundsException')
                if name == 'get': return r[idx]
                old,r[idx] = r[idx],args[1]
                return old
            if name == 'add': r.append(args[-1]); return True
            if name == 'addAll':
                if isinstance(args[0],list): r.extend(args[0]); return True
                return Unknown('addAll unknown collection')
            if name == 'contains': return args[0] in r
            if name == 'indexOf': return r.index(args[0]) if args[0] in r else -1
            if name in ('stream','parallelStream','collect','toArray'): return r
            if name == 'map': return r
            if name == 'remove':
                if args[0] in r: r.remove(args[0])
                return True
        if isinstance(r,dict):
            if name == 'get': return r.get(args[0]) if not isinstance(args[0],Unknown) else args[0]
            if name == 'put': r[args[0]]=args[1]; return None
            if name == 'isEmpty': return not r
            if name == 'values': return list(r.values())
            if name == 'keySet': return list(r)
            if name == 'entrySet': return [Obj('MapEntry',{'key':k,'value':v}) for k,v in r.items()]
            if name == 'containsKey': return args[0] in r
        if isinstance(r,str):
            if name == 'isEmpty': return r==''
            if name in ('length','size'): return len(r)
            if name == 'startsWith': return r.startswith(args[0]) if isinstance(args[0],str) else Unknown('prefix')
            if name == 'contains': return args[0] in r if isinstance(args[0],str) else Unknown('contains')
            if name in ('toLowerCase','toUpperCase'): return r.lower() if name=='toLowerCase' else r.upper()
            if name == 'trim': return r.strip()
            if name == 'replace': return r.replace(*args)
            if name == 'split': return re.split(args[0],r)
            if name == 'substring': return r[args[0]:args[1] if len(args)>1 else None]
            if name == 'charAt': return r[args[0]]
            if name == 'equalsIgnoreCase': return r.lower()==str(args[0]).lower()
        if isinstance(r,datetime):
            if name == 'before': return r<args[0] if isinstance(args[0],datetime) else Unknown('date before')
            if name == 'after': return r>args[0] if isinstance(args[0],datetime) else Unknown('date after')
            if name == 'getTime': return int((r-datetime(1970,1,1)).total_seconds()*1000)
        if short in ('StringUtils','ObjectUtils') and name=='isEmpty':
            if isinstance(args[0],Unknown): return args[0]
            return args[0] is None or args[0]=='' or args[0]==[]
        if short == 'UUID':
            if name=='randomUUID': return e.db.new_id()
            if name=='fromString': return args[0]
        if short == 'System' and name=='currentTimeMillis': return int((e.now-datetime(1970,1,1)).total_seconds()*1000)
        if short in ('Integer','Long','Double','Float','BigDecimal'):
            if name.startswith(('parse','valueOf')): return self.number(args[0],short in ('Integer','Long'))
        if short == 'String' and name=='valueOf': return e.java_string(args[0])
        if short=='Math' and name in ('min','max','abs'):
            u=unknown(*args)
            if u is not None: return u
            return min(args) if name=='min' else max(args) if name=='max' else abs(args[0])
        if short == 'Arrays' and name=='asList': return list(args[0]) if len(args)==1 and isinstance(args[0],list) else args
        if short == 'Collections':
            if name=='singletonList': return args
            if name=='emptyList': return []
            if name=='sort' and isinstance(args[0],list): return None
        if isinstance(r,Obj):
            if name.startswith(('get','is')) and not args:
                key = name[3:4].lower()+name[4:] if name.startswith('get') else name[2:3].lower()+name[3:]
                if key in r.fields: return r.fields[key]
            if short == 'SimpleDateFormat':
                if name=='parse':
                    if isinstance(args[0],Unknown): return args[0]
                    try: return datetime.fromisoformat(args[0].replace('Z','').replace('T',' '))
                    except (ValueError,AttributeError): raise JavaException('ParseException')
                if name=='format': return args[0].strftime('%Y-%m-%d %H:%M:%S') if isinstance(args[0],datetime) else Unknown('date format')
            if short=='Calendar':
                if name=='setTime': r.fields['date']=args[0]; return None
                if name=='getTime': return r.fields['date']
                if name=='get':
                    d=r.fields['date']; key=args[0].name.rsplit('.',1)[-1] if isinstance(args[0],TypeRef) else str(args[0])
                    if not isinstance(d,datetime): return Unknown('calendar date')
                    return {'YEAR':d.year,'MONTH':d.month-1,'DATE':d.day,'DAY_OF_MONTH':d.day,'HOUR_OF_DAY':d.hour,'MINUTE':d.minute}.get(key,Unknown('calendar field'))
                if name=='add' and isinstance(args[1],(int,Decimal)):
                    r.fields['date']+=timedelta(minutes=float(args[1])); return None
        if short=='Calendar' and name=='getInstance': return Obj('Calendar',{'date':e.now})
        if name in ('conveterObject','convertValue','readValue','parseObject') and args:
            value = deepcopy(args[0])
            if len(args)>1 and isinstance(args[1],TypeRef) and isinstance(value,Obj): value.type=args[1].name
            return value
        if name in ('writeValueAsString','object2Json','toJSONString') and args: return deepcopy(args[0])
        if short=='Jwts' or short=='Claims':
            if name=='claims': return {}
            return Obj('JwtBuilder',{})
        if short in ('JwtBuilder','JwtParser'):
            return NonNull('JWT') if name=='compact' else r
        if name=='setSubject' and isinstance(r,dict): return r
        if isinstance(r,Unknown): return r
        return e.diagnostic('unsupported library call '+typename+'.'+name)
