"""Small file helpers shared by dataset and training code."""
import hashlib
import json
import os
from pathlib import Path
import tempfile


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''): digest.update(block)
    return digest.hexdigest()


def atomic_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent,
                                         suffix='.tmp', delete=False) as stream:
            temp = Path(stream.name)
            json.dump(value, stream, indent=2, allow_nan=False); stream.write('\n')
        os.replace(temp, path)
    finally:
        if temp is not None: temp.unlink(missing_ok=True)
