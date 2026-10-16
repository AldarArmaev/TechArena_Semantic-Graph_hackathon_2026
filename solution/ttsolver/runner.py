from copy import deepcopy
from dataclasses import dataclass, field
from .engine import Engine, NeedChoice, JavaException, Limit
from .planner import Planner
from .events import AnnotationPolicy, serialize
from .values import Unknown, describe


@dataclass
class PathState:
    db: object
    planner: object
    events: list = field(default_factory=list)
    conditions: tuple = ()
    halted: bool = False


def run_scenario(catalog,initial_db,scenario,max_paths=8):
    db=initial_db.clone()
    paths=[PathState(db,Planner(db))]
    explain=[]
    policy=AnnotationPolicy()
    for step in scenario['steps']:
        completed=[]
        for path in paths:
            if path.halted:
                completed.append(path)
                continue
            pending=[{}]
            while pending:
                choices=pending.pop(0)
                trial=deepcopy(path)
                engine=Engine(catalog,trial.db,scenario['id'],choices)
                engine.step=step['n']; engine.conditions=list(path.conditions)
                request=trial.planner.request(step)
                response=Unknown('request not executed')
                try:
                    mid,match=catalog.route(request.verb,request.path)
                    engine.event('Grounding',evidence=request.verb+' '+request.path,values={'action':describe(request.action.__dict__),'body':request.body})
                    if mid is None or '{unresolved}' in request.path:
                        raise Limit('unresolved request: '+request.action.intent+' '+request.path)
                    engine.adapters.filter()
                    response=engine.adapters.bind_handler(mid,match,request.body)
                except NeedChoice as fork:
                    if len(completed)+len(pending)+len(paths)+1 < max_paths:
                        pending.extend([{**choices,fork.key:True},{**choices,fork.key:False}])
                        continue
                    engine.diagnostic('path budget exhausted; unresolved continuation excluded: '+fork.reason)
                    trial.halted=True
                except JavaException as ex:
                    engine.event('Exception',evidence=ex.kind)
                except Limit as ex:
                    engine.diagnostic(str(ex))
                    trial.halted=True
                trial.events.extend(engine.events)
                trial.conditions=tuple(engine.conditions)
                trial.planner.observe(request,response)
                completed.append(trial)
                explain.append({'step':step['n'],'action':describe(request.action.__dict__),
                                'request':{'verb':request.verb,'path':request.path,'body':describe(request.body)},
                                'response':describe(response),'halted':trial.halted,
                                'events':[e.json() for e in engine.events]})
        paths=completed
    projections=[policy.project(path.events,catalog) for path in paths]
    edges=set.intersection(*projections) if projections else set()
    return {'edges':serialize(edges)}, {'id':scenario['id'],'paths':len(paths),
           'halted_paths':sum(p.halted for p in paths),'steps':explain,
           'uncertain_edges':len(set.union(*projections)-edges) if projections else 0}
