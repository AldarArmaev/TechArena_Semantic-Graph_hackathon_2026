import gzip
import json
import re
from collections import defaultdict
from pathlib import Path
import javalang as j


def type_name(node):
    if node is None:
        return ''
    name = node.name
    while getattr(node, 'sub_type', None):
        node = node.sub_type
        name += '.' + node.name
    return name


def annotation_value(annotations, name, key='value'):
    a = next((a for a in annotations if a.name.split('.')[-1] == name), None)
    if not a:
        return None
    v = a.element
    if isinstance(v, list):
        v = next((x.value for x in v if x.name == key), None)
    if isinstance(v, j.tree.Literal):
        return v.value.strip('"')
    return None


class Catalog:
    def __init__(self, skeleton, source):
        self.source = Path(source)
        self.graph = json.load(gzip.open(skeleton, 'rt', encoding='utf-8'))
        self.nodes = self.graph['nodes']
        self.methods = {k:v for k,v in self.nodes.items() if v['kind'] == 'method'}
        self.types = {k:v for k,v in self.nodes.items() if v['kind'] == 'type'}
        self.by_type = defaultdict(list)
        self.by_name = defaultdict(list)
        self.sites = defaultdict(list)
        self.links = defaultdict(lambda: defaultdict(list))
        self.ast = {}
        self.units = {}
        self.type_ast = {}
        self.fields = defaultdict(dict)
        self.routes = []
        self.entities = {}
        self.diagnostics = []
        for tid, t in self.types.items():
            self.by_type[t['fqn'].split('.')[-1]].append(tid)
        for mid, m in self.methods.items():
            self.by_name[(m['owner'], m['name'])].append(mid)
        for sid, n in self.nodes.items():
            if n['kind'] == 'call_site':
                self.sites[n['method']].append((sid,n))
        for e in self.graph['edges']:
            self.links[e['source']][e['type']].append(e['target'])
        self.data_pairs = {(self.nodes[s]['method'],t)
                           for s, links in self.links.items() if self.nodes.get(s,{}).get('kind') == 'call_site'
                           for t in links.get('data_access',[])}
        files = sorted({m['file'] for m in self.methods.values()})
        for file in files:
            try:
                unit = j.parse.parse((self.source / file).read_text(encoding='utf-8-sig'))
            except (j.parser.JavaSyntaxError, j.tokenizer.LexerError) as e:
                self.diagnostics.append({'kind':'parse', 'file':file, 'reason':str(e)})
                continue
            self.units[file] = unit
            for tid, t in self.types.items():
                if t['file'] != file:
                    continue
                simple = t['fqn'].split('.')[-1]
                candidates = [n for _,n in unit.filter(j.tree.TypeDeclaration) if n.name == simple]
                if not candidates:
                    continue
                declaration = candidates[0]
                self.type_ast[tid] = declaration
                for f in declaration.fields:
                    for d in f.declarators:
                        self.fields[tid][d.name] = (type_name(f.type), d.initializer, f.annotations)
                for mid, m in self.methods.items():
                    if m['owner'] != tid:
                        continue
                    members = declaration.constructors if m['name'] == '<init>' else declaration.methods
                    matches = [n for n in members if (m['name'] == '<init>' or n.name == m['name'])
                               and len(n.parameters) == len(m['params_as_written'])]
                    if matches:
                        best = min(matches, key=lambda n: abs(n.position.line - m['line']))
                        self.ast[mid] = best
                names = {a.name.split('.')[-1] for a in declaration.annotations}
                if 'Entity' in names:
                    table = annotation_value(declaration.annotations, 'Table', 'name') or self.snake(simple)
                    columns = {n: annotation_value(a,'Column','name') or self.snake(n)
                               for n,(_,_,a) in self.fields[tid].items()}
                    self.entities[tid] = (table, columns)
        self._routes()

    @staticmethod
    def snake(name):
        return re.sub(r'(?<!^)(?=[A-Z])','_', name).lower()

    def resolve_type(self, name, mid=None, module=None):
        name = name.split('<')[0].replace('[]','')
        if name in self.types:
            return name
        if mid:
            m = self.methods[mid]
            module = m['module']
            unit = self.units.get(m['file'])
            if unit:
                for imp in unit.imports:
                    if not imp.wildcard and imp.path.endswith('.'+name):
                        name = imp.path
                        break
        matches = [k for k,t in self.types.items() if t['fqn'] == name]
        if not matches:
            matches = self.by_type.get(name.split('.')[-1], [])
        local = [k for k in matches if self.types[k]['module'] == module]
        common = [k for k in matches if self.types[k]['module'] == 'ts-common']
        picks = local or common or matches
        return picks[0] if len(picks) == 1 else name

    def implementation(self, tid):
        candidates = [k for k, links in self.links.items() if tid in links.get('implements',[])]
        return candidates[0] if len(candidates) == 1 else tid

    def method(self, tid, name, argc):
        tid = self.implementation(tid)
        candidates = [m for m in self.by_name.get((tid,name),[]) if len(self.methods[m]['params_as_written']) == argc
                      and not self.methods[m]['abstract']]
        return candidates[0] if len(candidates) == 1 else None

    def site(self, mid, node, name=None):
        pos = getattr(node, 'position', None)
        name = name or getattr(node,'member','<init>')
        candidates = [(k,n) for k,n in self.sites.get(mid,[]) if n['callee'] == name]
        if pos:
            candidates = [(k,n) for k,n in candidates if n['line'] == pos.line]
        if not candidates:
            return None
        return min(candidates,key=lambda p: abs(p[1]['col'] - (pos.column if pos else 0)))[0]

    def _routes(self):
        for mid,m in self.methods.items():
            if not any(a['name'].split('.')[-1].endswith('Mapping') for a in m['annotations']):
                continue
            owner = self.types[m['owner']]
            def mapping(annotations):
                for a in annotations:
                    name = a['name'].split('.')[-1]
                    if name.endswith('Mapping'):
                        paths = re.findall(r'"([^"]*)"', a['args'])
                        paths = [p for p in paths if p.startswith('/') or not p] or ['']
                        verbs = re.findall(r'RequestMethod\.(\w+)', a['args'])
                        if name != 'RequestMapping':
                            verbs = [name[:-7].upper()]
                        return paths, verbs
                return [''], []
            roots, _ = mapping(owner['annotations'])
            tails, verbs = mapping(m['annotations'])
            for root in roots:
                for tail in tails:
                    path = '/' + (root.strip('/')+'/'+tail.strip('/')).strip('/')
                    pattern = re.compile('^' + re.sub(r'\\\{[^}]+\\\}',r'([^/]+)',re.escape(path)) + '/?$')
                    self.routes.append((verbs or ['GET','POST','PUT','DELETE'],path,pattern,mid))

    def route(self, verb, path, module=None):
        candidates = [(mid, pattern.fullmatch(path)) for verbs,_,pattern,mid in self.routes
                      if verb in verbs and (module is None or self.methods[mid]['module'] == module)
                      and pattern.fullmatch(path)]
        return candidates[0] if len(candidates) == 1 else (None,None)
