'''Export recorded model replies; never invent or repair test dialogue.'''
import argparse
from hashlib import sha256
import json
from pathlib import Path


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def export(folders, destination):
    lines=['小登 / 全 Flash 开发测试原始对话',
           '以下是实际 API 最终输出，保留失败和不理想案例。',
           '各轮复用已看过的开发题，不能称作未见场景，也不能当成线上总体成功率。',
           'pass/style/fail 是助手依据逐条对话进行的审阅，不是独立人类盲测。','']
    manifest=[]
    total=0
    for directory in map(Path,folders):
        summary=read(directory/'summary.json')
        method=read(directory/'method.json')
        decision=read(directory/'decision.json') if (directory/'decision.json').exists() else {}
        reviews={row['id']:row for row in decision.get('cases',[])}
        lines.extend(['='*64,directory.name,
            '状态：'+decision.get('status','尚未完成逐案例评审'),
            '实际最终回复 {} / 计划 {}；实际 API 完成响应 {}；总耗时 {} 秒'.format(
                summary['actual_replies'],summary['expected_calls'],
                summary.get('actual_api_responses',summary['actual_replies']),summary['wall_seconds']),
            '提示词哈希：'+method['role_sha256'],
            '评审计数：'+json.dumps(decision.get('counts',{}),ensure_ascii=False),''])
        count=0
        for case in summary['cases']:
            review=reviews.get(case['id'],{})
            # New decisions separate the reason from structured evidence.
            grade=review.get('grade') or review.get('status') or '未评审'
            comment=review.get('reason') or review.get('evidence') or '无'
            if not isinstance(comment,str):
                comment=json.dumps(comment,ensure_ascii=False)
            lines.extend(['[{}] {} / {}'.format(case['id'],case.get('name',''),grade),
                          '评语：'+comment,''])
            for record in case['results']:
                count+=1
                lines.extend(['第 {} 轮 / {} 秒'.format(record['turn'],record['elapsed_seconds']),
                    '用户：'+record['user'],'小登：'+record['reply'],
                    'API：'+str(record.get('api_response_id')),''])
            for failed in case.get('failures',[]):
                lines.extend(['第 {} 轮失败：{}'.format(failed['turn'],failed['error']),
                    '用户：'+case['turns'][failed['turn']-1],
                    '没有最终回复，后续依赖这条回复的回合未执行。',''])
        if count!=summary['actual_replies']:
            raise ValueError('Transcript count differs from actual summary: '+str(directory))
        total+=count
        manifest.append(dict(directory=str(directory.resolve()),final_replies=count,
            summary_sha256=sha256((directory/'summary.json').read_bytes()).hexdigest(),
            method_sha256=sha256((directory/'method.json').read_bytes()).hexdigest(),
            decision_sha256=sha256((directory/'decision.json').read_bytes()).hexdigest() if decision else None))
    lines.extend(['='*64,'本报告共有 {} 条实际最终回复（含重复开发测试，不代表独立样本）。'.format(total)])
    destination=Path(destination)
    with destination.open('x',encoding='utf-8') as handle:
        handle.write(chr(10).join(lines)+chr(10))
    index=dict(final_replies=total,experiments=manifest,report_sha256=sha256(destination.read_bytes()).hexdigest())
    with destination.with_suffix('.index.json').open('x',encoding='utf-8') as handle:
        json.dump(index,handle,ensure_ascii=False,indent=2)
    return dict(path=str(destination.resolve()),experiments=len(manifest),final_replies=total,sha256=index['report_sha256'])


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',required=True)
    parser.add_argument('folders',nargs='+')
    arguments=parser.parse_args()
    print(json.dumps(export(arguments.folders,arguments.output),ensure_ascii=False))
