#!/usr/bin/env python3
"""Emit an intentionally easy synthetic adapter smoke fixture, not a benchmark."""
import json


def examples():
    for split, count in [('train', 40), ('calibration', 80), ('development', 40), ('heldout', 40)]:
        for index in range(count):
            label = bool(index % 2)
            yield dict(root_case_id=f'{split}-{index}', split=split, project='synthetic', label=label,
                       text=('command failed invalid argument' if label else
                             'command completed accepted output') + f' task {index}')


if __name__ == '__main__':
    for row in examples():
        print(json.dumps(row))
