"""Bounded package-bearing workload; stdlib ZIP/XML oracle, no visual renderer."""
import ast
import base64
import hashlib
import json
from pathlib import Path, PurePosixPath
import posixpath
import subprocess
import sys
from xml.etree import ElementTree as ET
from zipfile import ZipFile

source, output = map(Path, sys.argv[1:3])
output.mkdir(parents=True, exist_ok=False)
sys.path.insert(0, str(source))
from pptx_render import render_bytes, read_package_spec
from pptx_mapping import canonical_spec
from pptx_spec import sample_deck_spec, pattern_objects

UNICODE = "日本語・naïve・e\u0301・🧬 <tag> & \"quotes\""
spec = sample_deck_spec()
spec['slides'][0]['objects'][0]['paragraphs'][0]['runs'][0]['text'] = UNICODE
spec['slides'][1]['objects'].extend(pattern_objects({
    'pattern': 'table', 'slide': 2, 'x': .5, 'y': .9, 'width': 9, 'height': .6,
    'rows': [['項目', '値 & <'], ['α', '42']],
}, index=1, slide_count=2, slide_width=9144000, slide_height=6858000))


def tree(node):
    return [node.tag, sorted(node.attrib.items()), node.text or '', node.tail or '',
            [tree(child) for child in node]]


def inspect(path):
    with ZipFile(path) as archive:
        if not (archive.testzip() is None):
            raise AssertionError('run_pptx_fixture:36: validation failed')
        names = archive.namelist()
        if not (len(names) == len(set(names))):
            raise AssertionError('duplicate ZIP member')
        if not (all(not n.startswith('/') and '..' not in PurePosixPath(n).parts for n in names)):
            raise AssertionError('run_pptx_fixture:39: validation failed')
        files = {n for n in names if not n.endswith('/')}
        if not ('[Content_Types].xml' in files and 'ppt/presentation.xml' in files):
            raise AssertionError('run_pptx_fixture:41: validation failed')
        types = ET.fromstring(archive.read('[Content_Types].xml'))
        defaults = {x.attrib['Extension'] for x in types if x.tag.endswith('Default')}
        overrides = {x.attrib['PartName'].lstrip('/') for x in types if x.tag.endswith('Override')}
        if not (all(n == '[Content_Types].xml' or n in overrides or n.rsplit('.', 1)[-1] in defaults for n in files)):
            raise AssertionError('run_pptx_fixture:45: validation failed')
        semantic = {}
        texts = []
        for name in sorted(files):
            payload = archive.read(name)
            if name.endswith(('.xml', '.rels')):
                root = ET.fromstring(payload)
                if name == 'docProps/core.xml':
                    for child in root:
                        if child.tag in ('{http://purl.org/dc/terms/}created', '{http://purl.org/dc/terms/}modified'):
                            child.text = '<TIMESTAMP>'
                if name.endswith('.rels'):
                    ids = [child.attrib['Id'] for child in root]
                    if not (len(ids) == len(set(ids))):
                        raise AssertionError('run_pptx_fixture:58: validation failed')
                    parent = '' if name == '_rels/.rels' else str(PurePosixPath(name).parent.parent)
                    for rel in root:
                        if not (rel.attrib.get('TargetMode') != 'External'):
                            raise AssertionError('run_pptx_fixture:61: validation failed')
                        target = rel.attrib['Target']
                        resolved = target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join(parent, target))
                        if not (resolved in files):
                            raise AssertionError((name, resolved))
                if name.startswith('ppt/slides/slide') and name.endswith('.xml'):
                    texts.extend(node.text for node in root.iter() if node.tag == '{http://schemas.openxmlformats.org/drawingml/2006/main}t')
                semantic[name] = tree(root)
            else:
                semantic[name] = {'sha256': hashlib.sha256(payload).hexdigest(), 'bytes': len(payload)}
        raw = json.dumps(semantic, ensure_ascii=False, separators=(',', ':'), sort_keys=True).encode()
        return {'semantic_sha256': hashlib.sha256(raw).hexdigest(), 'file_count': len(files),
                'package_bytes': path.stat().st_size, 'texts': texts, 'members': sorted(files)}


results = {}
for name, display in (('rich', None), ('reordered', [1, 0])):
    path = output / (name + '.pptx')
    path.write_bytes(render_bytes(spec, display=display))
    actual = canonical_spec(read_package_spec(path))
    expected = canonical_spec(spec)
    if display:
        expected['slides'] = [expected['slides'][i] for i in display]
    if not (actual == expected):
        raise AssertionError('public spec parity (secondary same-implementation check)')
    results[name] = inspect(path)
    if not (UNICODE in results[name]['texts']):
        raise AssertionError('run_pptx_fixture:85: validation failed')
    if not ('項目' in results[name]['texts'] and '値 & <' in results[name]['texts']):
        raise AssertionError('run_pptx_fixture:86: validation failed')

bad = {'width': 10, 'height': 7.5, 'slides': [{'objects': [{'op': 'line', 'options': {
    'x': 0, 'y': 0, 'w': 1, 'h': 0,
    'line': {'color': '000000', 'width': 1, 'endArrowType': 'triangle'},
}}]}]}
try:
    render_bytes(bad)
except ValueError as exc:
    if not ('endArrowType' in str(exc)):
        raise AssertionError('run_pptx_fixture:95: validation failed')
    results['invalid_arrow_rejected'] = True
else:
    raise AssertionError('unsupported arrow accepted')

# Use the existing image-size regression's operation with explicit stable alt
# text: its default description embeds the worker-specific absolute PNG path.
# The original test is run separately, unchanged; no XML description is ignored.
test_tree = ast.parse((source / 'tests/test_pptxgenjs_image.py').read_text())
script = next(ast.literal_eval(n.value) for n in test_tree.body if isinstance(n, ast.Assign)
              and any(isinstance(t, ast.Name) and t.id == '_NODE_SCRIPT' for t in n.targets))
if not (script.count('slide.addImage({ path:') == 1):
    raise AssertionError('run_pptx_fixture:106: validation failed')
script = script.replace('slide.addImage({ path:', "slide.addImage({ altText: 'nano-fixture-png', path:")
png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAD0lEQVQImWNgYGBgAAAABQABDavlc2QAAAABJRU5ErkJggg==')
image_path, deck = output / 'dot.png', output / 'image.pptx'
image_path.write_bytes(png)
run = subprocess.run(['node', '-e', script, str(image_path), str(deck)], cwd=source,
                     capture_output=True, text=True, timeout=60)
if not (run.returncode == 0):
    raise AssertionError(run.stderr)
with ZipFile(deck) as archive:
    media = [n for n in archive.namelist() if n.startswith('ppt/media/') and not n.endswith('/')]
    if not (len(media) == 1 and archive.read(media[0]) == png):
        raise AssertionError('run_pptx_fixture:116: validation failed')
results['image'] = inspect(deck)
results['normalization'] = ['ZIP archive metadata omitted', 'docProps/core.xml dcterms:created and dcterms:modified text only']
results['visual_or_office_validation'] = False
(output / 'result.json').write_text(json.dumps(results, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({k:v.get('semantic_sha256') if isinstance(v,dict) else v for k,v in results.items()}, ensure_ascii=False))
