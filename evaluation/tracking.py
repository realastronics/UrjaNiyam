"""Record experiment provenance and refuse incompatible or incomplete resumes."""
import csv
import hashlib
import json
import os
import platform
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def manifest(args, fields, inputs, root=ROOT):
    settings = {k: v for k, v in vars(args).items() if k not in ('out', 'resume')}
    sources = sorted(p for directory in ('src', 'evaluation')
                     for p in (root / directory).rglob('*.py'))
    packages = {}
    for name in ('numpy', 'ortools', 'PyYAML'):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = None
    return {
        'schema': 1, 'fields': list(fields), 'settings': settings,
        'inputs': {name: digest(Path(path)) for name, path in inputs.items()},
        'code': {str(p.relative_to(root)).replace('\\', '/'): digest(p) for p in sources},
        'environment': {'python': platform.python_version(), 'system': platform.system(),
                        'machine': platform.machine(), 'packages': packages,
                        'threads': {k: os.environ.get(k) for k in
                                    ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS')}},
    }

def prepare_run(args, fields, inputs, root=ROOT):
    """Return output path. Call before reading old rows or opening output for writing.

    A run owns one CSV and its adjacent .meta.json. Use one writer per output.
    Existing untracked CSVs are never adopted or overwritten automatically.
    """
    out = Path(args.out)
    meta = out.with_suffix(out.suffix + '.meta.json')
    expected = manifest(args, fields, inputs, root)
    if out.exists() or meta.exists():
        if not getattr(args, 'resume', False):
            raise ValueError(f'{out}: output already exists; use --resume or a new --out path')
        if not out.is_file() or not meta.is_file():
            raise ValueError(f'{out}: resume needs both CSV and metadata; start a new output')
        actual = json.loads(meta.read_text(encoding='utf-8'))
        changed = [key for key in expected if actual.get(key) != expected[key]]
        if changed:
            raise ValueError(f'{out}: incompatible resume ({", ".join(changed)}); use a new --out path')
        with out.open(newline='', encoding='utf-8') as fh:
            reader = csv.reader(fh, strict=True)
            if next(reader, None) != list(fields):
                raise ValueError(f'{out}: CSV header does not match this experiment')
            for line, row in enumerate(reader, 2):
                if len(row) != len(fields):
                    raise ValueError(f'{out}: incomplete CSV record at line {line}; repair before resuming')
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation prevents accidental replacement of another run's metadata.
        with meta.open('x', encoding='utf-8') as fh:
            json.dump(expected, fh, indent=2, sort_keys=True)
            fh.write('\n')
    return out
