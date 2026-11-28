"""Artifact gate regression tests using synthetic tar archives."""
import importlib.util
import io
import json
import subprocess
import sys
import tarfile
from html import escape
from pathlib import Path

import pytest

from scripts import audit_pages_links


def load_vendored(name, relpath):
    """Load a vendored file by path under a repo-unique name (cli_args README: Loading a vendored copy)."""
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / relpath)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


run_cli = load_vendored("myon_bioinformatics_cli_args", "vendor/cli_args.py").run_cli


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('verify_pages_artifact', ROOT / 'scripts/verify_pages_artifact.py')
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


def archive(tmp_path, files):
    path = tmp_path / 'artifact.tar'
    with tarfile.open(path, 'w:gz') as tar:
        for name, body in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
    return path


def test_pages_artifact_accepts_local_links_and_fragments(tmp_path):
    path = archive(tmp_path, {
        './index.html': b'<a data-ui-back href="guide/">Guide</a><a href="#weekly">Weekly</a><section id="weekly"></section><link href="style.css">',
        './guide/index.html': b'<img src="../movie.gif"><script src="../script.js"></script>',
        './style.css': b'body{}', './script.js': b'//code',
        './movie.gif': b'GIF89a\x01\x00\x01\x00\x00\x00\x00',
    })
    assert validator.verify(path) == []


def test_pages_artifact_resolves_the_parent_folder_to_the_front_page(tmp_path):
    """A guide one folder down (guide_dir) links back to the Pages' own front page as ../ (or .., ./ at the top)."""
    page = b'<a href="../">UI</a><a href="..">UI</a><a href="../#top">Top</a><a href="../index.html">UI</a>'
    for prefix in ('./', ''):
        files = {f'{prefix}index.html': b'<p id="top">front</p><a href="./">Me</a>', f'{prefix}guide/index.html': page}
        assert validator.verify(archive(tmp_path, files)) == []
    errors = validator.verify(archive(tmp_path, {'guide/index.html': page}))          # no front page: still reported
    assert sum('missing local href: ..' in error for error in errors) == 4
    errors = validator.verify(archive(tmp_path, {'index.html': b'<p>front</p>', 'guide/index.html': b'<a href="../#nowhere">x</a>'}))
    assert errors == ['guide/index.html: missing fragment #nowhere in index.html']
    errors = validator.verify(archive(tmp_path, {'index.html': b'<a href="../">out</a>'}))   # above the top: still escapes
    assert errors == ['index.html: path escapes artifact: ../']


def test_pages_artifact_rejects_missing_local_link(tmp_path):
    errors = validator.verify(archive(tmp_path, {'index.html': b'<a href="tools/ironmate/">Old</a>'}))
    assert any('missing local href' in error for error in errors)


def test_pages_artifact_rejects_missing_hash_target(tmp_path):
    errors = validator.verify(archive(tmp_path, {'index.html': b'<a href="#absent">Missing</a>'}))
    assert any('missing fragment' in error for error in errors)


def test_pages_artifact_rejects_duplicate_back_nav_and_invalid_gif(tmp_path):
    errors = validator.verify(archive(tmp_path, {
        'index.html': b'<a data-ui-back href="#">Back</a><a data-ui-back href="#">Back again</a>',
        'broken.gif': b'not a GIF',
    }))
    assert any('duplicated common back navigation' in error for error in errors)
    assert any('invalid GIF header' in error for error in errors)


def test_pages_artifact_rejects_path_escape_and_missing_html(tmp_path):
    errors = validator.verify(archive(tmp_path, {'../index.html': b'hi', 'image.png': b'png'}))
    assert any('unsafe or duplicate archive path' in error for error in errors)
    assert any('no HTML documents' in error for error in errors)


def test_pages_artifact_does_not_claim_external_links_are_checked(tmp_path):
    path = archive(tmp_path, {'index.html': b'<a href="https://example.org/foo">External</a><img src="/tools/image-studio/logo.png">'})
    assert validator.verify(path) == []


def test_pages_artifact_rejects_bad_archive(tmp_path):
    path = tmp_path / 'artifact.tar'
    path.write_bytes(b'not a tar')
    assert any('invalid Pages artifact' in error for error in validator.verify(path))


def site_manifest(tmp_path, *sites):
    manifest = tmp_path / 'sites.json'
    manifest.write_text(json.dumps({'sites': sites}), encoding='utf-8')
    return manifest


def test_pages_links_catch_routes_removed_from_another_artifact(tmp_path):
    root_url = 'https://demo.github.io/'
    source = archive(tmp_path, {'index.html': b'''
        <a href="https://demo.github.io/tools/ironmate/">Old</a>
        <a href="/tools/ironmate/slides/phone/">Old phone</a>
        <a href="//demo.github.io/tools/slide-studio/phone?download=0#usage">New phone</a>
        <a href="../tools/slide-studio/phone/#absent">Missing anchor</a>
        <a href="/tools/slide-studio/phone/#usage:~:text=help">Text plus anchor</a>
        <a href="/tools/slide-studio/phone/#TOP">Top</a>
        <a href="/tools/slide-studio/phone/#%E4%BD%BF%E3%81%84%E6%96%B9">Unicode anchor</a>
        <a href="/tools/slide-studio/phone/#legacy">Named anchor</a>
        <a href="/tools/slide-studio/phone/#:~:text=help">Text only</a>
        <img srcset="data:image/gif;base64,R0lGODk= 1x, /tools/slide-studio/phone/missing.png 2x, /tools/slide-studio/phone/icon.png 3x">
        <a href="https://elsewhere.example/missing">External</a>
        <a href="https://demo.github.io.evil.example/tools/ironmate/">Lookalike host</a>
        <a href="https://demo.github.io:443/tools/slide-studio/phone/">Default port</a>
        <a href="https://demo.github.io:444/tools/ironmate/">Other origin</a>
        <a href="guide/missing.html">Relative within the project</a>'''}).rename(tmp_path / 'project.tar')
    target = archive(tmp_path, {'tools/slide-studio/phone/index.html':
                                '<article id="usage"></article><a name="legacy"></a><p id="使い方"></p>'.encode(),
                                'tools/slide-studio/phone/icon.png': b'png'})
    report = audit_pages_links.audit(site_manifest(tmp_path,
        {'base_url': root_url + 'Project/', 'archive': source.name},
        {'base_url': root_url, 'archive': target.name},
    ))
    assert report['summary']['resolved'] == 8
    assert [(f['status'], f['attribute'], f['reference']) for f in report['findings']] == [
        ('missing_file', 'href', root_url + 'tools/ironmate/'),
        ('missing_file', 'href', '/tools/ironmate/slides/phone/'),
        ('missing_anchor', 'href', '../tools/slide-studio/phone/#absent'),
        ('missing_file', 'srcset', '/tools/slide-studio/phone/missing.png'),
    ]
    assert all(f['source_site'] == root_url + 'Project/' and f['target_site'] == root_url for f in report['findings'])
    assert report['findings'][2]['target_file'] == 'tools/slide-studio/phone/index.html'


def test_pages_links_follow_declared_hash_routes_and_check_srcdoc_assets(tmp_path):
    root = 'https://demo.github.io/'
    frame = '''<base href="./mcp/execution/">
        <img src="assets/logo.png"><script src="/tools/mcp/execution/missing.mjs"></script>
        <a href="#frame-only">Local section</a>
        <a href="/tools/#wiki?title=A%26B">Wiki</a>'''
    target = archive(tmp_path, {
        'tools/index.html': f'''<section id="mcp" data-tools-panel data-routes="wiki pixiv"></section>
            <section id="mcp-execution" data-tools-panel>
              <template><iframe srcdoc="{escape(frame, quote=True)}"></iframe></template>
            </section>'''.encode(),
        'tools/mcp/execution/assets/logo.png': b'png',
    }).rename(tmp_path / 'portfolio.tar')
    guide = archive(tmp_path, {'index.html': b'''
        <a href="/tools/#mcp-execution">Execution Lab</a>
        <a href="/tools/#wiki?title=Example%3F">Wiki</a>
        <a href="/tools/#removed?title=Example">Removed route</a>
        <a href="/tools/mcp/execution/">Removed page</a>'''})
    report = audit_pages_links.audit(site_manifest(tmp_path,
        {'base_url': root, 'archive': target.name},
        {'base_url': root + 'Guide/', 'archive': guide.name},
    ))
    assert report['summary']['resolved'] == 4
    assert [(f['status'], f['target_url']) for f in report['findings']] == [
        ('missing_file', root + 'tools/mcp/execution/missing.mjs'),
        ('missing_anchor', root + 'tools/#removed?title=Example'),
        ('missing_file', root + 'tools/mcp/execution/'),
    ]


def test_pages_links_uses_longest_mount_and_keeps_unavailable_sites_separate(tmp_path):
    root_url = 'https://demo.github.io/'
    target = archive(tmp_path, {'index.html': b'<p id="verify"></p>'}).rename(tmp_path / 'nested.tar')
    source = archive(tmp_path, {'index.html': b'''
        <a href="/Project">Project root</a><a href="/Project/not-there/#missing">No validated artifact</a>
        <a href="/Project-tools/">A distinct root directory</a><a href="/Other/">Malformed tar</a>
        <a href="/Absent/">Absent tar</a><a href="/Project/doc#verify">More specific site</a>''',
        'Project/index.html': b'fallback must not be used', 'Project-tools/index.html': b'present'})
    (tmp_path / 'broken.tar').write_bytes(b'invalid')
    manifest = site_manifest(tmp_path,
        {'base_url': root_url, 'archive': source.name},
        {'base_url': root_url + 'Project/'},
        {'base_url': root_url + 'Project/doc/', 'archive': target.name},
        {'base_url': root_url + 'Other/', 'archive': 'broken.tar'},
        {'base_url': root_url + 'Absent/', 'archive': 'absent.tar'},
    )
    report = audit_pages_links.audit(manifest)
    assert report['summary'] == {'available_sites': 2, 'partial_sites': 0, 'unavailable_sites': 3,
                                 'in_scope_references': 6, 'resolved': 2, 'missing_file': 0,
                                 'missing_anchor': 0, 'target_unavailable': 4, 'anchor_unverified': 0}
    assert all(f['status'] == 'target_unavailable' for f in report['findings'])
    assert str(tmp_path) not in json.dumps(report)
    output = tmp_path / 'report.json'
    result = run_cli(ROOT / 'scripts/audit_pages_links.py', str(manifest),
                     '--report', str(output), python_options=('-S',))
    assert result.returncode == 3, result.stderr
    assert json.loads(output.read_text(encoding='utf-8'))['summary'] == report['summary']


def test_pages_links_distinguishes_unreadable_anchor_from_missing_file(tmp_path):
    root_url = 'https://demo.github.io/'
    source = archive(tmp_path, {'nested/index.html': b'''
        <base href="/Project/"><a href="doc.html#id">Unreadable anchor</a>
        <a href="doc.html">Existing file</a><a href="unknown.html">Missing file</a>'''}).rename(tmp_path / 'project.tar')
    target = archive(tmp_path, {'doc.html': b'\xff', 'index.html': b'<i id="known"></i>'})
    manifest = site_manifest(tmp_path,
        {'base_url': root_url, 'archive': source.name},
        {'base_url': root_url + 'Project/', 'archive': target.name},
    )
    report = audit_pages_links.audit(manifest)
    assert report['summary']['partial_sites'] == 1
    assert report['summary']['resolved'] == 1
    assert [(f['status'], f['target_file']) for f in report['findings']] == [
        ('anchor_unverified', 'doc.html'), ('missing_file', None),
    ]
    assert audit_pages_links.main([str(manifest)]) == 1


def test_pages_links_rejects_ambiguous_mount(tmp_path):
    manifest = site_manifest(tmp_path, {'base_url': 'https://demo.github.io/Project'},
                             {'base_url': 'https://DEMO.github.io/Project/'})
    with pytest.raises(ValueError, match='duplicate Pages base URL'):
        audit_pages_links.audit(manifest)


def test_repository_navigation_duplicates_report_and_optional_gate(tmp_path):
    path = archive(tmp_path, {'index.html': b'''
        <nav data-ui-back><a href="https://github.com/Owner/Repo">GitHub</a></nav>
        <a href="https://github.com/owner/repo/">Repository</a>
        <a href="https://github.com/Owner/Repo#readme">README</a>
        <a href="https://github.com/Owner/Repo/issues">Issues</a>
        <a href="https://github.com/Owner/Child">Child</a>
        <a href="https://github.com/Owner/Repo?tab=readme">Query</a>
    '''})
    report = {}
    assert validator.verify(path, report, repository='Owner/Repo') == []
    assert report['navigation_duplicates'] == [
        {'file': 'index.html', 'target': 'https://github.com/Owner/Repo', 'count': 2}
    ]
    assert report['navigation_duplicate_count'] == 1
    assert any('duplicate repository navigation target' in e for e in
               validator.verify(path, repository='Owner/Repo', strict_navigation=True))


def test_repository_navigation_ignores_distinct_links_and_pages_without_nav(tmp_path):
    path = archive(tmp_path, {'index.html': b'''
        <nav data-ui-back><a href="https://github.com/Owner/Repo">GitHub</a></nav>
        <a href="https://github.com/Owner/Repo#readme">README</a>
        <a href="https://github.com/Owner/Child">Child</a>
        <a href="https://github.com.evil.example/Owner/Repo">Other host</a>
        ''', 'content.html': b'<a href="https://github.com/Owner/Repo">1</a><a href="https://github.com/Owner/Repo">2</a>'})
    report = {}
    assert validator.verify(path, report, repository='Owner/Repo', strict_navigation=True) == []
    assert report['navigation_duplicates'] == []
