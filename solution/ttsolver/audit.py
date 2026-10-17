"""Compact, uncertainty-oriented report over a solver diagnostic record."""
from collections import defaultdict


def summarize(report):
    steps={}
    clusters=defaultdict(lambda:{'steps':set(),'methods':set(),'occurrences':0})
    for run in report['steps']:
        number=run['step']
        action=run['action']
        request=run['request']
        if number not in steps:
            steps[number]={'step':number,'intent':action['intent'],'actor':action['actor'],
                           'train_family':action['train_family'],
                           'request':request['verb']+' '+request['path'],
                           'dto':request['body'],'variants':0,'responses':set(),
                           'state_changes':set(),'http_sends':set(),'mq_sends':set(),
                           'sql':set(),'unknown_conditions':set(),'exceptions':set(),
                           'binding_failures':set()}
        row=steps[number]
        row['variants']+=1
        response=run['response']
        status=response.get('fields',{}).get('body',{}).get('fields',{}).get('status') if isinstance(response,dict) else None
        row['responses'].add(str(status))
        for event in run['events']:
            kind=event['kind']
            if kind=='StateMutation': row['state_changes'].add(str(event['values'].get('table')))
            elif kind=='HttpRequestSent': row['http_sends'].add(event['evidence'])
            elif kind=='MessageSent': row['mq_sends'].add(str(event['site']))
            elif kind=='SqlAccess': row['sql'].add(str(event['target']))
            elif kind=='Exception': row['exceptions'].add(event['evidence'])
            elif kind=='BindingFailure': row['binding_failures'].add(event['evidence'])
            elif kind=='Branch' and event['confidence']=='AMBIGUOUS':
                reason=event['evidence']
                row['unknown_conditions'].add(reason)
                cluster=clusters[reason]
                cluster['steps'].add(number)
                cluster['methods'].add(event['method'])
                cluster['occurrences']+=1
    for row in steps.values():
        for key in ('responses','state_changes','http_sends','mq_sends','sql',
                    'unknown_conditions','exceptions','binding_failures'):
            row[key]=sorted(row[key])
    conditional=report.get('conditional_edges',[])
    ranked=[]
    for reason,info in clusters.items():
        affected=[edge for edge in conditional if any(reason in condition
                  for origin in edge['origins'] for condition in origin['conditions'])]
        ranked.append({'reason':reason,'steps':sorted(info['steps']),
                       'methods':sorted(str(x) for x in info['methods']),
                       'occurrences':info['occurrences'],
                       'conditional_edges':len(affected),
                       'blast_radius':len(affected)})
    ranked.sort(key=lambda x:(-x['blast_radius'],-x['occurrences'],x['reason']))
    return {'scenario':report['id'],'paths':report['paths'],
            'feasible_paths':report.get('feasible_paths',report['paths']),
            'discarded_inconsistent_paths':report.get('inconsistent_paths',0),
            'halted_paths':report['halted_paths'],
            'common_edges':None,'conditional_edges':conditional,
            'uncertainty_clusters':ranked,'steps':[steps[k] for k in sorted(steps)]}
