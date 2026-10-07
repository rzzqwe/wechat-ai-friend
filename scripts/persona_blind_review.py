'''Prepare anonymous same-input comparisons and score evidence-linked reviews.'''
import argparse
from hashlib import sha256
import json
from pathlib import Path
import secrets


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def write_new(path, value):
    with Path(path).open('x',encoding='utf-8') as handle:
        json.dump(value,handle,ensure_ascii=False,indent=2)


def load_complete(folder):
    folder=Path(folder)
    cases=read(folder/'cases.json')
    summary=read(folder/'summary.json')
    verification=read(folder/'verification.json')
    if not verification['integrity_ok'] or not verification['all_planned_turns_completed']:
        raise ValueError('Incomplete or unaudited source experiment')
    expected=sum(len(c['turns']) for c in cases)
    if len(cases)<32 or len({c['id'] for c in cases})!=len(cases):
        raise ValueError('Need at least 32 distinct cases')
    if summary['actual_replies']!=expected or summary['failures']:
        raise ValueError('Cannot hide failed or missing turns')
    result={c['id']:c for c in summary['cases']}
    if set(result)!={c['id'] for c in cases}:
        raise ValueError('Case coverage mismatch')
    output={}
    for case in cases:
        records=result[case['id']]['results']
        if len(records)!=len(case['turns']):
            raise ValueError('Turn coverage mismatch')
        if any(r['turn']!=i or r['user']!=case['turns'][i-1] or not r['reply'].strip()
               for i,r in enumerate(records,1)):
            raise ValueError('Turn input/order mismatch')
        output[case['id']]=[r['reply'] for r in records]
    return cases,output


def prepare(candidate,baseline,destination):
    candidate,baseline,destination=map(Path,(candidate,baseline,destination))
    cases,candidate_replies=load_complete(candidate)
    other,baseline_replies=load_complete(baseline)
    if cases!=other:
        raise ValueError('Inputs, seeded histories and retrieval switches must be identical')
    if destination.exists():
        raise ValueError('Do not overwrite or relabel an attempted review')
    destination.mkdir(parents=True)
    pairs=[]
    mapping={}
    for case in cases:
        label=secrets.choice(('A','B'))
        mapping[case['id']]=label
        replies={label:candidate_replies[case['id']],
                 'B' if label=='A' else 'A':baseline_replies[case['id']]}
        pairs.append(dict(id=case['id'],name=case.get('name',''),category=case.get('category',''),
                          history=case.get('history',[]),users=case['turns'],checks=case['checks'],**replies))
    pack=dict(method='Random labels per case; same user turns; each branch keeps its own actual replies.',
              scope='Synthetic evaluation, not an online population success rate.',cases=pairs)
    write_new(destination/'blind-pack.json',pack)
    write_new(destination/'mapping-sealed.json',dict(
        candidate_labels=mapping,pack_sha256=digest(destination/'blind-pack.json'),
        sources={name:{'directory':str(folder.resolve()),'summary_sha256':digest(folder/'summary.json'),
                       'method_sha256':digest(folder/'method.json'),'cases_sha256':digest(folder/'cases.json')}
                 for name,folder in [('candidate',candidate),('baseline',baseline)]}))
    write_new(destination/'review-instructions.json',dict(
        instruction='Review blind-pack without reading mapping-sealed. Supply every case, both labels, exact evidence quotes, and preference reasons. Do not discard ties or failures.',
        schema={'cases':[{'id':'case id','A':{'natural':True,'evidence':[{'turn':1,'quote':'exact reply excerpt','reason':'why appropriate or not'}],
                                             'clear_errors':[]},
                          'B':{'natural':False,'evidence':[{'turn':1,'quote':'exact reply excerpt','reason':'why appropriate or not'}],
                                             'clear_errors':[{'turn':1,'quote':'exact excerpt','reason':'specific factual/capability/boundary/task error'}]},
                          'winner':'A, B, or tie','preference_reason':'case-specific comparative evidence'}]}))
    return dict(directory=str(destination.resolve()),cases=len(cases),pack_sha256=digest(destination/'blind-pack.json'))


def summarize(pack,review,mapping):
    cases=pack['cases']
    if len(cases)<32 or len({c['id'] for c in cases})!=len(cases):
        raise ValueError('At least 32 distinct cases required')
    rows=review['cases']
    if len(rows)!=len(cases) or len({r['id'] for r in rows})!=len(cases):
        raise ValueError('Review must include every case once')
    indexed={r['id']:r for r in rows}
    if set(indexed)!={c['id'] for c in cases} or set(mapping)!={c['id'] for c in cases}:
        raise ValueError('Review or mapping coverage mismatch')
    totals={name:dict(natural=0,clear_error_cases=0,wins=0,losses=0,ties=0) for name in ('candidate','baseline')}
    for case in cases:
        row=indexed[case['id']]
        if row.get('winner') not in ('A','B','tie') or not row.get('preference_reason','').strip():
            raise ValueError('Missing comparative judgment')
        if mapping[case['id']] not in ('A','B'):
            raise ValueError('Invalid blinded mapping')
        for label in ('A','B'):
            grade=row[label]
            if type(grade.get('natural')) is not bool or not isinstance(grade.get('clear_errors'),list):
                raise ValueError('Invalid grade')
            if not isinstance(grade.get('evidence'),list) or not grade['evidence']:
                raise ValueError('Evidence is required for every grade')
            for evidence in grade['evidence']+grade['clear_errors']:
                turn=evidence.get('turn')
                quote=evidence.get('quote')
                if type(turn) is not int or not 1<=turn<=len(case[label]) or not isinstance(quote,str) or not quote.strip():
                    raise ValueError('Invalid evidence anchor')
                if quote not in case[label][turn-1] or not evidence.get('reason','').strip():
                    raise ValueError('Evidence must quote an actual reply and explain its relevance')
            if grade['clear_errors'] and grade['natural']:
                raise ValueError('An explicitly erroneous case cannot pass natural-and-appropriate')
            name='candidate' if label==mapping[case['id']] else 'baseline'
            totals[name]['natural']+=grade['natural']
            totals[name]['clear_error_cases']+=bool(grade['clear_errors'])
            totals[name]['wins']+=row['winner']==label
            totals[name]['ties']+=row['winner']=='tie'
            totals[name]['losses']+=row['winner'] not in (label,'tie')
    size=len(cases)
    for values in totals.values():
        values.update(natural_fraction=values['natural']/size,win_fraction=values['wins']/size,loss_fraction=values['losses']/size)
    candidate=totals['candidate']
    passed=candidate['natural_fraction']>=.8 and candidate['clear_error_cases']==0 and candidate['win_fraction']>=.6 and candidate['loss_fraction']<=.2
    return dict(cases=size,totals=totals,holdout_quality_gate_passed=passed,
                remaining_gates='Old regressions, runtime equivalence, deployment and post-deployment verification are separate requirements.',
                scope='Synthetic case review only; not an online population success rate.')


def score(directory,review_path):
    directory,review_path=map(Path,(directory,review_path))
    review=read(review_path)
    pack=read(directory/'blind-pack.json')
    # Validate coverage and evidence without opening real label identities.
    summarize(pack,review,{case['id']:'A' for case in pack['cases']})
    # Lock the submitted review hash before revealing label identities.
    lock=directory/'review-lock.json'
    checksum=digest(review_path)
    if lock.exists():
        if read(lock)['review_sha256']!=checksum:
            raise ValueError('Blinded review changed after identity reveal')
    else:
        write_new(lock,dict(review_sha256=checksum,review_path=str(review_path.resolve())))
    mapping=read(directory/'mapping-sealed.json')
    if mapping['pack_sha256']!=digest(directory/'blind-pack.json'):
        raise ValueError('Blind pack changed')
    for source in mapping['sources'].values():
        for filename in ('summary','method','cases'):
            if digest(Path(source['directory'])/(filename+'.json'))!=source[filename+'_sha256']:
                raise ValueError('Source experiment changed')
    result=summarize(pack,review,mapping['candidate_labels'])
    result['review_sha256']=checksum
    write_new(directory/'score.json',result)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    sub=parser.add_subparsers(dest='command',required=True)
    setup=sub.add_parser('prepare')
    setup.add_argument('candidate');setup.add_argument('baseline');setup.add_argument('destination')
    grading=sub.add_parser('score')
    grading.add_argument('directory');grading.add_argument('review')
    args=parser.parse_args()
    result=(prepare(args.candidate,args.baseline,args.destination) if args.command=='prepare' else score(args.directory,args.review))
    print(json.dumps(result,ensure_ascii=False,indent=2))
