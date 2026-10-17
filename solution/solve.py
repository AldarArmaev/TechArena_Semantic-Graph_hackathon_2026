#!/usr/bin/env python3
"""Run only the static analyzer. Does not import/start the target application."""
import argparse
import glob
import json
import sys
import time
from pathlib import Path
from ttsolver.catalog import Catalog
from ttsolver.sql import Database
from ttsolver.runner import run_scenario


def main():
    root=Path(__file__).resolve().parents[1]
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=root)
    p.add_argument('--scenarios',nargs='+',default=['scenarios/*.json'])
    p.add_argument('-o','--output',type=Path,default=root/'solution/output/answer.json')
    p.add_argument('--explain',action='store_true')
    p.add_argument('--diagnostics',type=Path)
    p.add_argument('--audit',type=Path,help='write compact uncertainty and step audit')
    p.add_argument('--max-paths',type=int,default=8)
    args=p.parse_args()
    start=time.perf_counter()
    c=Catalog(args.root/'skeleton/trainticket.json.gz',args.root/'targets/trainticket')
    db=Database(args.root/'context/db-after-warmup.sql.gz')
    files=sorted({f for pattern in args.scenarios for f in glob.glob(str(args.root/pattern))})
    if not files: p.error('No scenario files matched')
    answer={'project':c.graph['project'],'scenarios':{}}
    diagnostics={'parser':c.diagnostics,'scenarios':{}}
    audits={}
    for file in files:
        scenario=json.loads(Path(file).read_text(encoding='utf-8'))
        block,report=run_scenario(c,db,scenario,args.max_paths)
        answer['scenarios'][scenario['id']]=block
        diagnostics['scenarios'][scenario['id']]=report
        if args.audit:
            from ttsolver.audit import summarize
            audit=summarize(report)
            audit['common_edges']=len(block['edges'])
            audits[scenario['id']]=audit
        print(f"{scenario['id']}: {len(block['edges'])} edges, {report['paths']} paths, {report['halted_paths']} halted",file=sys.stderr)
        if args.explain:
            for step in report['steps']:
                print(f" STEP {step['step']} {step['action']['intent']}: {step['request']['verb']} {step['request']['path']}",file=sys.stderr)
                for event in step['events']:
                    if event['kind'] in ('Branch','HttpRequestSent','SqlAccess','Diagnostic','Exception'):
                        print('  '+event['kind']+': '+str(event['target'] or event['evidence'])+' '+str(event['values']),file=sys.stderr)
    diagnostics['seconds']=time.perf_counter()-start
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(answer,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    if args.diagnostics:
        args.diagnostics.parent.mkdir(parents=True,exist_ok=True)
        args.diagnostics.write_text(json.dumps(diagnostics,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    if args.audit:
        args.audit.parent.mkdir(parents=True,exist_ok=True)
        args.audit.write_text(json.dumps({'scenarios':audits},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f"Wrote {args.output}; {diagnostics['seconds']:.2f}s",file=sys.stderr)


if __name__=='__main__': main()
