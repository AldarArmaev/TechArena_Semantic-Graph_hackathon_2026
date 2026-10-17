#!/usr/bin/env python3
"""Package the current solver output for the TechArena ZIP upload."""
import argparse
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--answer', type=Path, default=root/'solution/output/answer.json')
    parser.add_argument('--output', type=Path, default=root/'solution/output/techarena-submission.zip')
    args = parser.parse_args()
    answer = args.answer.read_bytes()
    payload = json.loads(answer)
    expected = {f'S{i}' for i in range(8)}
    if payload.get('project') != 'trainticket' or set(payload.get('scenarios', {})) != expected:
        parser.error('answer must contain project=trainticket and exactly scenarios S0-S7')
    sources = [root/'solution/solve.py', root/'solution/package_submission.py',
               root/'solution/requirements.txt', root/'solution/README.md', root/'PROGRESS.md']
    sources.extend(sorted((root/'solution/ttsolver').glob('*.py')))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(args.output, 'w', ZIP_DEFLATED, compresslevel=9) as archive:
        archive.writestr('submission.json', answer)
        for source in sources:
            archive.write(source, source.relative_to(root).as_posix())
    with ZipFile(args.output) as archive:
        if archive.testzip() is not None:
            raise RuntimeError('submission ZIP failed CRC check')
    print(args.output.resolve())


if __name__ == '__main__':
    main()
