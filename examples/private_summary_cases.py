"""Check whether local private summaries preserve qualifications in English or Chinese."""
import argparse
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


ENGLISH_CASES = {
    'ownership': "Jo prefers early shifts. Lee is only passing on Jo's preference. Lee's own preference is unknown.",
    'exception': 'Chen usually avoids late shifts. Chen can take one only on Fridays when a colleague picks up the children.',
    'conditional': "If the pilot is approved, Zhao might switch to Wednesday shifts next month. Approval is pending and the current roster still applies.",
}


def run(language='zh', model='qwen3.5:9b'):
    cases = {'en': ENGLISH_CASES, 'zh': CASES}[language]
    with TemporaryDirectory() as folder:
        root = Path(folder) / 'synthetic'
        data.initialize(root)
        data.ingest(root, 'private', [dict(source=k, body=v) for k, v in cases.items()])
        data.configure(root, 'local', model)
        result = data.maintain_pending(root, len(cases))
        if result['completed'] != len(cases):
            raise RuntimeError(f'Incomplete synthetic batch: {result}')
        with closing(data.connect(root, 'private')) as db:
            rows = db.execute("""SELECT source, body, summary, mode, o.model, p.model FROM records
                                 LEFT JOIN summary_origin o USING(source)
                                 LEFT JOIN summary_reported_model p USING(source) ORDER BY source""")
            for source, body, summary, mode, recorded_model, reported_model in rows:
                if (mode, recorded_model) != ('local', model):
                    raise ValueError('stored summary origin differs from the requested local model')
                print(json.dumps(dict(case=source, language=language, mode=mode, model=recorded_model,
                                      reported_model=reported_model, original=body, summary=summary), ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--language', choices=('en', 'zh'), default='zh', help='language of the synthetic records')
    parser.add_argument('--model', default='qwen3.5:9b', help='installed local Ollama model')
    args = parser.parse_args()
    run(args.language, args.model)
