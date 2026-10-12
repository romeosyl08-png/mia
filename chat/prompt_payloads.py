"""Serialize structured model context without changing its data."""
import json


def compact_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))
