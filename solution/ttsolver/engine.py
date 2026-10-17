"""Bounded Java AST interpreter. Only reached expressions produce events.

Unknown branches suspend and are replayed with correlated path choices. They
are never implemented as a union traversal of call graph candidates.
"""
from dataclasses import dataclass
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
import ast
import re
import javalang as j
from .catalog import type_name
from .values import Unknown, Obj, TypeRef, OptionalValue, Deferred, truth, unknown, NonNull
from .events import Event


class Returned(Exception):
    def __init__(self,value): self.value = value
class JavaException(Exception):
    def __init__(self,kind,value=None): self.kind,self.value = kind,value
class BreakLoop(Exception): pass
class ContinueLoop(Exception): pass
class NeedChoice(Exception):
    def __init__(self,key,reason): self.key,self.reason = key,reason
class Limit(Exception): pass


@dataclass
class Frame:
    method: str
    obj: Obj
    env: dict


class Engine:
    def __init__(self,catalog,db,scenario='',choices=None):
        self.c = catalog
        self.db = db
        self.scenario = scenario
        self.step = 0
        self.events = []
        self.stack = []
        self.choices = choices or {}
        self.choice_counter = 0
        self.conditions = []
        self.beans = {}
        self.enums = {}
        self.callback_depth = 0
        self.operations = 0
        self.now = datetime(2026,9,15)
        from .adapters import Adapters
        self.adapters = Adapters(self)

    @property
    def mid(self): return self.stack[-1].method if self.stack else None

    def event(self,kind,target=None,site=None,evidence='',values=None,method=None,confidence='PROVEN'):
        e = Event(kind,self.scenario,self.step,method or self.mid,target,site,evidence,
                  confidence,tuple(self.conditions),values or {})
        self.events.append(e)
        return e

    def diagnostic(self,reason,node=None):
        self.event('Diagnostic',evidence=reason,site=self.c.site(self.mid,node) if node and self.mid else None,
                   confidence='UNSUPPORTED')
        return Unknown(reason)

    def branch(self,value,node=None):
        value = truth(value)
        location = getattr(node,'position',None)
        evidence = str(location or '')
        if isinstance(value,Unknown):
            key = self.choice_counter
            self.choice_counter += 1
            self.event('Branch',evidence=value.reason,values={'choice':key},confidence='AMBIGUOUS')
            if key not in self.choices:
                raise NeedChoice(key,value.reason)
            result = self.choices[key]
            self.conditions.append(f'{key}:{value.reason}={result}')
            return result
        self.event('Branch',evidence=evidence,values={'result':value})
        return value

    def bean(self,tid):
        tid = self.c.implementation(tid)
        if tid not in self.beans:
            obj = Obj(tid,{})
            self.beans[tid] = obj
        return self.beans[tid]

    def default(self,t):
        if t == 'boolean': return False
        if t in ('int','long','short','byte','float','double'): return 0
        return None

    def field(self,obj,name):
        if isinstance(obj,Unknown): return obj
        if obj is None: raise JavaException('NullPointerException')
        if isinstance(obj,(list,str)) and name=='length': return len(obj)
        if isinstance(obj,TypeRef):
            short=obj.name.rsplit('.',1)[-1]
            java_limits={'Long':(2**63-1,-2**63),'Integer':(2**31-1,-2**31),
                         'Short':(2**15-1,-2**15),'Byte':(2**7-1,-2**7)}
            if short in java_limits and name in ('MAX_VALUE','MIN_VALUE'):
                return java_limits[short][name=='MIN_VALUE']
            float_limits={'Float':('3.4028235e38','1.4e-45'),
                          'Double':('1.7976931348623157e308','4.9e-324')}
            if short in float_limits and name in ('MAX_VALUE','MIN_VALUE'):
                return Decimal(float_limits[short][name=='MIN_VALUE'])
            tid = self.c.resolve_type(obj.name,self.mid)
            declaration = self.c.type_ast.get(tid)
            if isinstance(declaration,j.tree.EnumDeclaration):
                key = (tid,name)
                if key not in self.enums:
                    const = next((v for v in declaration.body.constants if v.name == name),None)
                    if const:
                        names=[field for field,(_,_,_) in self.c.fields[tid].items()]
                        fields = {field:self.eval(v) for field,v in zip(names,const.arguments)}
                        self.enums[key] = Obj(tid,fields,name)
                if key in self.enums: return self.enums[key]
            fs = self.c.fields.get(tid,{})
            if name in fs:
                _,init,_ = fs[name]
                return self.eval(init) if init is not None else self.default(fs[name][0])
            return TypeRef(obj.name+'.'+name)
        if isinstance(obj,dict): return obj.get(name,Unknown('missing map key '+name))
        if isinstance(obj,Obj):
            if name in obj.fields: return obj.fields[name]
            fs = self.c.fields.get(obj.type,{})
            if name in fs:
                t,init,anns = fs[name]
                names = {a.name for a in anns}
                if 'Autowired' in names or 'Inject' in names or 'Resource' in names:
                    value = self.bean(self.c.resolve_type(t,self.mid))
                elif 'Value' in names:
                    value = Unknown('@Value property '+name)
                else:
                    value = self.eval(init) if init is not None else self.default(t)
                obj.fields[name] = value
                return value
            return Unknown('unmodeled field '+obj.type+'.'+name)
        return Unknown('field '+name+' on '+repr(obj)[:60])

    def name(self,name):
        if not name: return self.stack[-1].obj
        parts = name.split('.')
        f = self.stack[-1]
        if parts[0] == 'this': value = f.obj
        elif parts[0] in f.env: value = f.env[parts[0]]
        elif parts[0] in self.c.fields.get(f.obj.type,{}): value = self.field(f.obj,parts[0])
        else: value = TypeRef(self.c.resolve_type(parts[0],f.method))
        for part in parts[1:]: value = self.field(value,part)
        return value

    def assign(self,node,value):
        if isinstance(node,j.tree.MemberReference):
            f = self.stack[-1]
            if node.qualifier:
                target = self.name(node.qualifier)
                if isinstance(target,Obj): target.fields[node.member] = value
            elif node.member in f.env: f.env[node.member] = value
            else: f.obj.fields[node.member] = value
            return value
        if isinstance(node,j.tree.This) and node.selectors:
            self.stack[-1].obj.fields[node.selectors[-1].member] = value
            return value
        return self.diagnostic('unsupported assignment '+type(node).__name__,node)

    def invoke(self,mid,args,obj=None,site=None,direct=False):
        if mid not in self.c.ast:
            return self.diagnostic('missing AST '+str(mid))
        if len(self.stack) >= 65: raise Limit('call depth 65')
        m = self.c.methods[mid]
        if direct: self.event('DirectProjectCall',mid,site,'resolved reached invocation')
        self.event('MethodEntered',method=mid,site=site,evidence='source body')
        obj = obj or self.bean(m['owner'])
        node = self.c.ast[mid]
        env = {p.name: v for p,v in zip(node.parameters,args)}
        self.stack.append(Frame(mid,obj,env))
        try:
            self.block(node.body or [])
            value = None
        except Returned as r:
            value = r.value
        except JavaException as ex:
            if not getattr(ex,'recorded',False):
                self.event('Exception',evidence=ex.kind)
                ex.recorded=True
            raise
        finally:
            self.stack.pop()
        self.event('Return',method=mid,values={'value':value})
        return value

    def block(self,statements):
        for node in statements: self.statement(node)

    def statement(self,node):
        self.operations += 1
        if self.operations > 60000: raise Limit('statement budget 60000')
        if node is None: return
        if type(node) is j.tree.Statement: return  # empty Java statement ';'
        if isinstance(node,list): return self.block(node)
        if isinstance(node,j.tree.BlockStatement): return self.block(node.statements or [])
        if isinstance(node,(j.tree.LocalVariableDeclaration,j.tree.VariableDeclaration)):
            for d in node.declarators:
                self.stack[-1].env[d.name] = self.eval(d.initializer) if d.initializer is not None else self.default(type_name(node.type))
        elif isinstance(node,j.tree.StatementExpression): self.eval(node.expression)
        elif isinstance(node,j.tree.IfStatement):
            result = self.branch(self.eval(node.condition),node.condition)
            self.statement(node.then_statement if result else node.else_statement)
        elif isinstance(node,j.tree.ReturnStatement): raise Returned(self.eval(node.expression))
        elif isinstance(node,j.tree.ThrowStatement):
            value = self.eval(node.expression)
            raise JavaException(value.type if isinstance(value,Obj) else 'Exception',value)
        elif isinstance(node,j.tree.TryStatement):
            try:
                self.block(node.block)
            except JavaException as ex:
                handler = next((c for c in node.catches or [] if any(t in ('Exception','RuntimeException') or ex.kind.endswith(t) for t in c.parameter.types)),None)
                if handler is None: raise
                self.stack[-1].env[handler.parameter.name] = ex.value
                self.block(handler.block)
            finally:
                if node.finally_block: self.block(node.finally_block)
        elif isinstance(node,j.tree.ForStatement):
            control = node.control
            if isinstance(control,j.tree.EnhancedForControl):
                values = self.eval(control.iterable)
                if not isinstance(values,(list,tuple,set)):
                    raise Limit('unknown collection iteration: '+repr(values)[:150])
                if len(values)>300: raise Limit('collection loop >300')
                for value in list(values):
                    self.stack[-1].env[control.var.declarators[0].name] = value
                    try: self.statement(node.body)
                    except ContinueLoop: continue
                    except BreakLoop: break
            else:
                if isinstance(control.init,list):
                    for n in control.init: self.eval(n)
                elif control.init: self.statement(control.init)
                for _ in range(300):
                    if control.condition is not None and not self.branch(self.eval(control.condition),control.condition): break
                    try: self.statement(node.body)
                    except ContinueLoop: pass
                    except BreakLoop: break
                    for update in control.update or []: self.eval(update)
                else: raise Limit('for loop >300')
        elif isinstance(node,(j.tree.WhileStatement,j.tree.DoStatement)):
            for index in range(300):
                if not (isinstance(node,j.tree.DoStatement) and index == 0):
                    if not self.branch(self.eval(node.condition),node.condition): break
                try: self.statement(node.body)
                except ContinueLoop: pass
                except BreakLoop: break
            else: raise Limit('while loop >300')
        elif isinstance(node,j.tree.BreakStatement): raise BreakLoop()
        elif isinstance(node,j.tree.ContinueStatement): raise ContinueLoop()
        elif isinstance(node,j.tree.SwitchStatement):
            value = self.eval(node.expression)
            if isinstance(value,Unknown): raise Limit('unknown switch: '+value.reason)
            active = False
            try:
                for case in node.cases:
                    if not case.case or any(self.eval(v)==value for v in case.case): active=True
                    if active: self.block(case.statements)
            except BreakLoop: pass
        elif isinstance(node,j.tree.SynchronizedStatement): self.block(node.block)
        else:
            self.diagnostic('unsupported statement '+type(node).__name__,node)
            raise Limit('unsupported statement '+type(node).__name__)

    def binary(self,op,a,b):
        u = unknown(a,b)
        if u is not None: return u
        try:
            if op == '==': return a is b if isinstance(a,Obj) or isinstance(b,Obj) else a == b
            if op == '!=': return not self.binary('==',a,b)
            if op == '+':
                if isinstance(a,str) or isinstance(b,str): return self.java_string(a)+self.java_string(b)
                return a+b
            if op == '-': return a-b
            if op == '*': return a*b
            if op == '/': return int(a/b) if isinstance(a,int) and isinstance(b,int) else a/b
            if op == '%': return a%b
            if op == '<': return a<b
            if op == '<=': return a<=b
            if op == '>': return a>b
            if op == '>=': return a>=b
            if op == '&': return a&b
            if op == '|': return a|b
        except (TypeError,ValueError,ZeroDivisionError):
            return Unknown('invalid operands for '+op)
        return Unknown('unsupported binary '+op)

    @staticmethod
    def java_string(v):
        if v is None: return 'null'
        if isinstance(v,bool): return str(v).lower()
        return str(v)

    def eval(self,node):
        if node is None: return None
        self.operations += 1
        if self.operations>60000: raise Limit('expression budget 60000')
        if isinstance(node,j.tree.Literal):
            raw = node.value
            if raw == 'null': value = None
            elif raw in ('true','false'): value = raw == 'true'
            elif raw.startswith(('"',"'")):
                try: value = ast.literal_eval(raw)
                except (SyntaxError,ValueError): value = raw[1:-1]
            else:
                raw = raw.replace('_','')
                raw = re.sub(r'[lLfFdD]$','',raw)
                try: value = Decimal(raw) if '.' in raw else int(raw,0) if raw.startswith(('0x','0b')) else int(raw)
                except ValueError: value = Unknown('literal '+raw)
        elif isinstance(node,j.tree.MemberReference):
            value = self.field(self.name(node.qualifier),node.member) if node.qualifier else self.name(node.member)
        elif isinstance(node,j.tree.This): value = self.stack[-1].obj
        elif isinstance(node,j.tree.BinaryOperation):
            left = self.eval(node.operandl)
            if node.operator in ('&&','||'):
                decision = self.branch(left,node.operandl)
                if node.operator == '&&': value = truth(self.eval(node.operandr)) if decision else False
                else: value = True if decision else truth(self.eval(node.operandr))
            else: value = self.binary(node.operator,left,self.eval(node.operandr))
        elif isinstance(node,j.tree.Assignment):
            value = self.eval(node.value)
            if node.type != '=': value = self.binary(node.type[:-1],self.eval(node.expressionl),value)
            return self.assign(node.expressionl,value)
        elif isinstance(node,j.tree.TernaryExpression):
            value = self.eval(node.if_true if self.branch(self.eval(node.condition),node.condition) else node.if_false)
        elif isinstance(node,j.tree.Cast): value = self.eval(node.expression)
        elif isinstance(node,j.tree.MethodInvocation):
            value = self.call(self.name(node.qualifier),node)
        elif isinstance(node,j.tree.ClassCreator):
            args = [self.eval(a) for a in node.arguments]
            tid = self.c.resolve_type(type_name(node.type),self.mid)
            value = self.construct(tid,args,node)
        elif isinstance(node,j.tree.ArrayCreator):
            value = [self.eval(v) for v in node.initializer.initializers] if node.initializer else []
        elif isinstance(node,j.tree.ArrayInitializer): value = [self.eval(v) for v in node.initializers]
        elif isinstance(node,j.tree.ClassReference): value = TypeRef(self.c.resolve_type(type_name(node.type),self.mid))
        elif isinstance(node,(j.tree.LambdaExpression,j.tree.MethodReference)):
            return Deferred(node,dict(self.stack[-1].env))
        else: return self.diagnostic('unsupported expression '+type(node).__name__,node)
        for sel in getattr(node,'selectors',None) or []:
            if isinstance(sel,j.tree.MethodInvocation): value = self.call(value,sel)
            elif isinstance(sel,j.tree.MemberReference): value = self.field(value,sel.member)
            elif isinstance(sel,j.tree.ArraySelector):
                idx = self.eval(sel.index)
                if isinstance(value,(list,str)) and isinstance(idx,int):
                    if not 0<=idx<len(value): raise JavaException('IndexOutOfBoundsException')
                    value = value[idx]
                else: value = Unknown('array index')
            else: value = self.diagnostic('unsupported selector '+type(sel).__name__,sel)
        for op in reversed(getattr(node,'prefix_operators',None) or []):
            if op == '!':
                t = truth(value)
                value = t if isinstance(t,Unknown) else not t
            elif op in ('-','+'):
                if not isinstance(value,Unknown): value = -value if op == '-' else value
            elif op in ('++','--'):
                value = self.binary('+',value,1 if op == '++' else -1)
                self.assign(node,value)
            else: value = Unknown('unary '+op)
        for op in getattr(node,'postfix_operators',None) or []:
            self.assign(node,self.binary('+',value,1 if op == '++' else -1))
        return value

    def construct(self,tid,args,node=None,materialize=False):
        if tid not in self.c.types: return self.adapters.construct(tid,args)
        obj = Obj(tid,{})
        for name,(t,init,_) in self.c.fields.get(tid,{}).items():
            if init is None: obj.fields[name] = self.default(t)
        ctor = self.c.method(tid,'<init>',len(args))
        if ctor:
            body = self.c.ast[ctor].body
            if not materialize or body:
                self.invoke(ctor,args,obj,self.c.site(self.mid,node,'<init>') if node else None,direct=not materialize)
        else:
            declaration = self.c.type_ast.get(tid)
            annotations = {a.name for a in declaration.annotations} if declaration else set()
            if 'AllArgsConstructor' in annotations:
                instance_fields = [d.name for f in declaration.fields if 'static' not in f.modifiers for d in f.declarators]
                for name,value in zip(instance_fields,args): obj.fields[name] = value
            elif args:
                return self.diagnostic('no constructor model '+tid)
        return obj

    def call(self,receiver,node):
        args = [self.eval(a) for a in node.arguments]
        name = node.member
        site = self.c.site(self.mid,node)
        if receiver is None: raise JavaException('NullPointerException')
        if isinstance(receiver,Obj): tid = receiver.type
        elif isinstance(receiver,TypeRef): tid = self.c.resolve_type(receiver.name,self.mid)
        else: tid = None
        # Exact source receiver type precedes skeleton candidates. Library calls
        # with the same name must not accidentally bind a project overload.
        target = self.c.method(tid,name,len(args)) if tid else None
        if target:
            return self.invoke(target,args,receiver if isinstance(receiver,Obj) else None,site,direct=True)
        if tid in self.c.type_ast:
            declaration = self.c.type_ast[tid]
            if isinstance(declaration,j.tree.InterfaceDeclaration) and any('Repository' in type_name(x) for x in declaration.extends or []):
                return self.adapters.repository(receiver,name,args,site)
            if isinstance(receiver,Obj):
                field = name[3:4].lower()+name[4:] if name.startswith(('get','set')) else name[2:3].lower()+name[3:]
                if field not in self.c.fields.get(tid,{}) and field not in receiver.fields:
                    alternate='is'+field[:1].upper()+field[1:]
                    if alternate in self.c.fields.get(tid,{}) or alternate in receiver.fields: field=alternate
                if field in self.c.fields.get(tid,{}) or field in receiver.fields:
                    if name.startswith(('get','is')) and not args:
                        self.adapters.collection_read(receiver,field)
                        return self.field(receiver,field)
                    if name.startswith('set') and len(args)==1:
                        receiver.fields[field] = args[0]
                        return None
        if not node.qualifier:
            unit = self.c.units[self.c.methods[self.mid]['file']]
            imported = next((i.path.rsplit('.',1)[0] for i in unit.imports if i.static and i.path.endswith('.'+name)),None)
            if not imported:
                imported = next((i.path for i in unit.imports if i.static and i.wildcard and i.path.endswith('ResponseEntity') and name in ('ok','status')),None)
            if imported: receiver = TypeRef(imported)
        return self.adapters.call(receiver,name,args,site,node)
