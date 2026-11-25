"""Inspect the uploaded GitHub Pages tar archive without extracting it (stdlib only)."""
import argparse
import html.parser
import posixpath
import tarfile
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit


class References(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ids = set()
        self.links = []
        self.back_nav = 0

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if attrs.get('id'):
            self.ids.add(attrs['id'])
        if 'data-ui-back' in attrs:
            self.back_nav += 1
        for key in ('src', 'href'):
            if key in attrs and attrs[key]:
                self.links.append((key, attrs[key]))
        if 'srcset' in attrs and 'data:' not in attrs['srcset']:
            for candidate in attrs['srcset'].split(','):
                value = candidate.strip().split(' ', 1)[0]
                if value:
                    self.links.append(('srcset', value))

    handle_startendtag = handle_starttag


def verify(archive):
    """Check files and local URL targets in the precise uploaded tarball."""
    errors = []
    files = set()
    htmls = {}
    gifs = {}
    try:
        with tarfile.open(archive, 'r:*') as bundle:
            members = bundle.getmembers()
            if not members:
                return ['empty Pages artifact']
            for member in members:
                name = member.name.removeprefix('./')
                if not name or name == '.':
                    continue
                path = PurePosixPath(name)
                if name.startswith('/') or '..' in path.parts or '\\' in name or name in files:
                    errors.append(f'unsafe or duplicate archive path: {name}')
                    continue
                if member.isdir():
                    continue
                if not member.isfile():
                    errors.append(f'unsupported archive member: {name}')
                    continue
                stream = bundle.extractfile(member)
                if stream is None:
                    errors.append(f'unreadable file: {name}')
                    continue
                files.add(name)
                if name.lower().endswith(('.html', '.htm')):
                    data = stream.read(16 * 1024 * 1024 + 1)
                    if len(data) > 16 * 1024 * 1024:
                        errors.append(f'{name}: HTML exceeds 16 MiB inspection limit')
                    else:
                        htmls[name] = data
                elif name.lower().endswith('.gif'):
                    gifs[name] = stream.read(13)
    except (OSError, tarfile.TarError, EOFError) as exc:
        return [f'invalid Pages artifact: {exc}']
    if not files:
        errors.append('no files in Pages artifact')
    if not htmls:
        errors.append('no HTML documents in Pages artifact')
    documents = {}
    for name, data in sorted(htmls.items()):
        try:
            parser = References()
            parser.feed(data.decode('utf-8'))
            parser.close()
            documents[name] = parser
            if parser.back_nav > 1:
                errors.append(f'{name}: duplicated common back navigation ({parser.back_nav})')
        except (UnicodeError, ValueError) as exc:
            errors.append(f'{name}: invalid HTML: {exc}')
    for name, parser in documents.items():
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
            if resolved == '..' or resolved.startswith('../'):
                errors.append(f'{name}: path escapes artifact: {url}')
                continue
            candidates = [resolved]
            if relative.endswith('/'):
                candidates = [posixpath.join(resolved, 'index.html')]
            elif '.' not in posixpath.basename(resolved):
                candidates.extend([resolved + '.html', posixpath.join(resolved, 'index.html')])
            target = next((candidate for candidate in candidates if candidate in files), None)
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
    return errors


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', help='path to the uploaded Pages tar archive')
    failures = verify(parser.parse_args().archive)
    for failure in failures[:100]:
        print(f'::error::{failure}')
    raise SystemExit(bool(failures))
