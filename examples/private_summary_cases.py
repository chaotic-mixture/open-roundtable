"""Run synthetic Chinese qualification cases through the local private store."""
from contextlib import closing
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from open_roundtable import data


CASES = {
    'ownership': '小周喜欢早班。小林只负责转达小周的偏好，小林自己的偏好未知。',
    'exception': '小陈通常不接晚班。仅在周五且有同事负责接孩子时，小陈才可以接晚班。',
    'conditional': '若试点获批，小赵可能在下月改为周三值班。目前试点尚未获批，排班保持原样。',
}


def run():
    with TemporaryDirectory() as folder:
        root = Path(folder) / 'synthetic'
        data.initialize(root)
        data.ingest(root, 'private', [dict(source=k, body=v) for k, v in CASES.items()])
        data.configure(root, 'local', 'qwen3.5:9b')
        result = data.maintain_pending(root, len(CASES))
        if result['completed'] != len(CASES):
            raise RuntimeError(f'Incomplete synthetic batch: {result}')
        with closing(data.connect(root, 'private')) as db:
            for source, body, summary in db.execute('SELECT source, body, summary FROM records ORDER BY source'):
                print(json.dumps(dict(case=source, original=body, summary=summary), ensure_ascii=False))


if __name__ == '__main__':
    run()
