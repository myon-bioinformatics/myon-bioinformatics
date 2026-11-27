"""Inspect the uploaded GitHub Pages tar archive without extracting it (stdlib only)."""
import argparse
from dataclasses import dataclass, field
import html.parser
import json
import os
import posixpath
import tarfile
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit


HTML_SUFFIXES = ('.html', '.htm')


def srcset_urls(value):
    # A comma inside the URL can be part of a data URL or a filename.
    while value := value.lstrip(' \t\n\r\f,'):
        parts = value.split(maxsplit=1)
        url = parts[0]
        value = parts[1] if len(parts) > 1 else ''
        if not url.endswith(','):
            _, _, value = value.partition(',')
        yield url.rstrip(',')


class References(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ids = set()
        self.named_anchors = set()
        self.links = []
        self.back_nav = 0
        self.anchor_hrefs = []
        self.base_href = None

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if attrs.get('id'):
            self.ids.add(attrs['id'])
        if tag == 'a' and attrs.get('href'):
            self.anchor_hrefs.append(attrs['href'])
        if tag == 'a' and attrs.get('name'):
            self.named_anchors.add(attrs['name'])
        if tag == 'base' and self.base_href is None and attrs.get('href') is not None:
            self.base_href = attrs['href']
        if 'data-ui-back' in attrs:
            self.back_nav += 1
        for key in ('src', 'href'):
            if attrs.get(key) and (tag, key) != ('base', 'href'):
                self.links.append((key, attrs[key]))
        if attrs.get('srcset'):
            self.links.extend(('srcset', url) for url in srcset_urls(attrs['srcset']))

    handle_startendtag = handle_starttag


@dataclass
class ArtifactIndex:
    files: set = field(default_factory=set)
    documents: dict = field(default_factory=dict)
    gifs: dict = field(default_factory=dict)
    inventory_errors: list = field(default_factory=list)
    html_errors: list = field(default_factory=list)
    total_bytes: int = 0


def index_archive(archive):
    """Read the uploaded file inventory and parse HTML without extracting the tar."""
    result = ArtifactIndex()
    try:
        with tarfile.open(archive, 'r:*') as bundle:
            members = bundle.getmembers()
            if not members:
                return ArtifactIndex(inventory_errors=['empty Pages artifact'])
            for member in members:
                name = member.name.removeprefix('./')
                if not name or name == '.':
                    continue
                path = PurePosixPath(name)
                if name.startswith('/') or '..' in path.parts or '\\' in name or name != path.as_posix() or name in result.files:
                    result.inventory_errors.append(f'unsafe or duplicate archive path: {name}')
                    continue
                if member.isdir():
                    continue
                if not member.isfile():
                    result.inventory_errors.append(f'unsupported archive member: {name}')
                    continue
                result.files.add(name)
                result.total_bytes += member.size
                if name.lower().endswith(HTML_SUFFIXES):
                    with bundle.extractfile(member) as stream:
                        data = stream.read(16 * 1024 * 1024 + 1)
                    if len(data) > 16 * 1024 * 1024:
                        result.html_errors.append(f'{name}: HTML exceeds 16 MiB inspection limit')
                        continue
                    try:
                        parser = References()
                        parser.feed(data.decode('utf-8'))
                        parser.close()
                        result.documents[name] = parser
                    except (UnicodeError, ValueError) as exc:
                        result.html_errors.append(f'{name}: invalid HTML: {exc}')
                elif name.lower().endswith('.gif'):
                    with bundle.extractfile(member) as stream:
                        result.gifs[name] = stream.read(13)
    except (OSError, tarfile.TarError, EOFError) as exc:
        return ArtifactIndex(inventory_errors=[f'invalid Pages artifact: {exc}'])
    return result


def resolve_file(files, path, *, directory=False):
    index = posixpath.normpath(posixpath.join(path, 'index.html'))
    if directory:
        return index if index in files else None
    if path in files:
        return path
    if '.' not in posixpath.basename(path) and path + '.html' in files:
        return path + '.html'
    return index if index in files else None


def repository_target(url):
    """Canonical GitHub repository root only; retain README, query and subpage links."""
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in ('https', 'http') or parsed.netloc.lower() != 'github.com':
            return None
        if parsed.query or parsed.fragment:
            return None
        parts = parsed.path.strip('/').split('/')
        if len(parts) == 2 and all(parts):
            return '/'.join(parts).lower()
    except ValueError:
        pass
    return None


def verify(archive, report=None, *, repository=None, strict_navigation=False):
    """Check files and local URL targets in the precise uploaded tarball."""
    index = index_archive(archive)
    files, documents, gifs = index.files, index.documents, index.gifs
    errors = index.inventory_errors + index.html_errors
    if not files and errors and errors[0].startswith(('empty Pages artifact', 'invalid Pages artifact:')):
        return errors
    if not files:
        errors.append('no files in Pages artifact')
    if not any(name.lower().endswith(HTML_SUFFIXES) for name in files):
        errors.append('no HTML documents in Pages artifact')
    navigation_duplicates = []
    for name, parser in sorted(documents.items()):
        if parser.back_nav and repository:
            count = sum(repository_target(url) == repository.lower() for url in parser.anchor_hrefs)
            if count > 1:
                finding = {'file': name, 'target': f'https://github.com/{repository}', 'count': count}
                navigation_duplicates.append(finding)
                if strict_navigation:
                    errors.append(f'{name}: duplicate repository navigation target ({count})')
        if parser.back_nav > 1:
            errors.append(f'{name}: duplicated common back navigation ({parser.back_nav})')
        for kind, url in parser.links:
            if url == '#' or url.startswith('//'):
                continue
            parsed = urlsplit(url)
            if parsed.scheme or parsed.netloc or url.startswith('/'):
                # External URLs / site-root URLs require a separate cross-site check.
                continue
            if not parsed.path and not parsed.fragment:
                continue
            relative = unquote(parsed.path)
            if relative.startswith('\\') or '\\' in relative:
                errors.append(f'{name}: invalid local URL: {url}')
                continue
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(name), relative)) if relative else name
            if resolved == '.':
                resolved = ''  # ../ from a folder (or ./ at the top) is the artifact's own front page
            if resolved == '..' or resolved.startswith('../'):
                errors.append(f'{name}: path escapes artifact: {url}')
                continue
            target = resolve_file(files, resolved, directory=relative.endswith('/'))
            if target is None:
                errors.append(f'{name}: missing local {kind}: {url}')
            elif parsed.fragment and target in documents:
                fragment = unquote(parsed.fragment)
                if fragment not in documents[target].ids:
                    errors.append(f'{name}: missing fragment #{fragment} in {target}')
    for name, data in gifs.items():
        if len(data) < 13 or data[:6] not in (b'GIF87a', b'GIF89a') or not int.from_bytes(data[6:8], 'little') or not int.from_bytes(data[8:10], 'little'):
            errors.append(f'{name}: invalid GIF header')
    print(f'Pages artifact inspection: {len(files)} files, {len(documents)} HTML, {len(gifs)} GIF; {len(errors)} error(s)')
    if report is not None:
        report['navigation_duplicates'] = navigation_duplicates
        report['navigation_duplicate_count'] = len(navigation_duplicates)
        report.update(files=len(files), bytes=index.total_bytes, html=len(documents), gifs=len(gifs),
                      css=sum(n.endswith('.css') for n in files), js=sum(n.endswith(('.js', '.mjs')) for n in files))
    return errors


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', help='path to the uploaded Pages tar archive')
    parser.add_argument('--report', type=Path)
    parser.add_argument('--repository', default=os.environ.get('GITHUB_REPOSITORY'))
    parser.add_argument('--strict-navigation', action='store_true')
    args = parser.parse_args()
    report = {}
    failures = verify(args.archive, report, repository=args.repository, strict_navigation=args.strict_navigation)
    for finding in report.get('navigation_duplicates', []):
        print(f"::warning::{finding['file']}: duplicate repository navigation target ({finding['count']})")
    if args.report:
        report.update(schema='pages-artifact-inspection/1', errors=failures[:100], error_count=len(failures),
                      status='failure' if failures else 'success', head_sha=os.environ.get('PAGES_HEAD_SHA'),
                      run_id=os.environ.get('GITHUB_RUN_ID'), artifact_name=os.environ.get('PAGES_ARTIFACT_NAME'),
                      browser_status='未検証')
        args.report.write_text(json.dumps(report, ensure_ascii=False), encoding='utf-8')
    for failure in failures[:100]:
        print(f'::error::{failure}')
    raise SystemExit(bool(failures))
