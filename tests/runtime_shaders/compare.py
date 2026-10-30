"""Compare the on-device shader compiler's output (Check.cs `all`) with the libraries the Python pipeline prepared."""
import json
from pathlib import Path
import sys

shaders, scratch = Path(sys.argv[1]), Path(sys.argv[2])
chain = next(p for p in sorted(shaders.glob('b-*')) if p.is_dir())
expected = {p.name[:-len('.metallib')] for p in chain.glob('*_b.metallib')}
produced = {p.name[:-len('.metallib')] for p in (scratch / 'b-check').glob('*_b.metallib')}
spv_mismatch = [p.name for p in (scratch / 'spv').glob('*.spv')
                if (shaders / 'spv' / p.name).exists() and (shaders / 'spv' / p.name).read_bytes() != p.read_bytes()]
source, metadata = [], []
for name in sorted(expected & produced):
    if (chain / (name + '.metallib')).read_text() != (scratch / 'b-check' / (name + '.metallib')).read_text():
        source.append(name)
    old = json.loads((chain / (name + '.json')).read_text())
    new = json.loads((scratch / 'b-check' / (name + '.json')).read_text())
    if old != {**new, 'chain': old['chain']}:
        metadata.append(name)
python_failures = {f['key']: f for f in json.loads((shaders / 'build-failures.json').read_text())}
port_failures = {f['key']: f for f in json.loads((scratch / 'build-failures.json').read_text())}
print('libraries: python', len(expected), 'port', len(produced), 'both', len(expected & produced))
print('only python', sorted(expected - produced)[:10], len(expected - produced))
print('only port', sorted(produced - expected)[:10], len(produced - expected))
print('SPIR-V differences', len(spv_mismatch), spv_mismatch[:5])
print('MSL differences', len(source), source[:5])
print('metadata differences', len(metadata), metadata[:5])
print('failures: python', len(python_failures), 'port', len(port_failures),
      'same keys', set(python_failures) == set(port_failures),
      'same records', all(python_failures.get(k) == f for k, f in port_failures.items()))
sys.exit(1 if expected != produced or spv_mismatch or source or metadata or python_failures != port_failures else 0)
