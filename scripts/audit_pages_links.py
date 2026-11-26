"""Compare links across locally downloaded GitHub Pages deployment artifacts."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import posixpath
from pathlib import Path
import sys
from urllib.parse import quote, unquote, urljoin, urlsplit

if __package__:
    from .verify_pages_artifact import HTML_SUFFIXES, index_archive, resolve_file
else:
    from verify_pages_artifact import HTML_SUFFIXES, index_archive, resolve_file


def load_sites(manifest):
    """Read the explicit site-to-artifact map, including sites missing an artifact."""
    document = json.loads(manifest.read_text(encoding='utf-8'))
    sites = document.get('sites') if isinstance(document, dict) else None
    if not isinstance(sites, list) or not sites:
        raise ValueError('manifest requires a non-empty sites list')
    by_mount = {}
    for site in sites:
        if not isinstance(site, dict) or not isinstance(site.get('base_url'), str):
            raise ValueError('each site requires a base_url')
        base = urlsplit(site['base_url'])
        path = unquote(base.path)
        mount = path.rstrip('/') + '/'
        if (base.scheme != 'https' or not base.hostname or base.netloc.lower() != base.hostname
                or base.query or base.fragment or '\\' in path
                or not mount.startswith('/') or '//' in path
                or any(part in ('.', '..') for part in path.split('/'))):
            raise ValueError(f"invalid HTTPS Pages base URL: {site['base_url']}")
        key = base.hostname, mount
        if key in by_mount:
            raise ValueError(f"duplicate Pages base URL: {site['base_url']}")
        archive = site.get('archive')
        if archive is not None and (not isinstance(archive, str) or not archive):
            raise ValueError('archive must be a local tar path or null')
        row = {'base_url': f'https://{base.hostname}{quote(mount)}', 'status': 'unavailable'}
        for field in ('repository', 'head_sha', 'artifact_url'):
            if field in site:
                if not isinstance(site[field], str) or not site[field]:
                    raise ValueError(f'{field} must be a non-empty string')
                row[field] = site[field]
        index = index_archive(manifest.parent / archive) if archive is not None else None
        if index is None:
            row['reason'] = 'archive_not_supplied'
        elif index.inventory_errors or not index.files:
            row['reason'] = 'archive_unreadable_or_invalid'
        else:
            row.update(status='partial' if index.html_errors or not index.documents else 'available',
                       file_count=len(index.files), html_count=len(index.documents),
                       html_error_count=len(index.html_errors))
        by_mount[key] = row, index
    return by_mount


def check_reference(target_url, source_key, explicit, mounts_by_host, sites):
    if target_url.scheme not in ('http', 'https') or not target_url.hostname or target_url.username is not None:
        return None
    try:
        if target_url.port not in (None, 443 if target_url.scheme == 'https' else 80):
            return None
    except ValueError:
        return None
    decoded = unquote(target_url.path or '/')
    path = posixpath.normpath('/' + decoded.lstrip('/'))
    if path != '/' and decoded.endswith(('/', '/.', '/..')):
        path += '/'
    host = target_url.hostname
    for mount in mounts_by_host.get(host, ()):
        if path == mount.rstrip('/') or path.startswith(mount):
            break
    else:
        return None
    if (host, mount) == source_key and not explicit:
        return None  # The single-artifact gate handles ordinary same-site relative links.
    site, index = sites[host, mount]
    if site['status'] == 'unavailable':
        return site, 'target_unavailable', None, None
    target = resolve_file(index.files, path[len(mount):], directory=path.endswith('/'))
    if target is None:
        return site, 'missing_file', None, None
    fragment = unquote(target_url.fragment.split(':~:', 1)[0])
    if fragment and target.lower().endswith(HTML_SUFFIXES) and fragment.lower() != 'top':
        document = index.documents.get(target)
        if document is None:
            return site, 'anchor_unverified', target, fragment
        if fragment not in document.ids and fragment not in document.named_anchors:
            return site, 'missing_anchor', target, fragment
    return site, 'resolved', target, None


def audit(manifest):
    sites = load_sites(Path(manifest))
    mounts_by_host = {}
    for host, mount in sorted(sites, key=lambda key: (-len(key[1]), key)):
        mounts_by_host.setdefault(host, []).append(mount)
    findings = []
    counts = Counter()
    for source_key, (site, index) in sorted(sites.items()):
        if site['status'] == 'unavailable':
            continue
        for file, document in sorted(index.documents.items()):
            document_url = site['base_url'] + quote(file.removesuffix('index.html')
                                                   if posixpath.basename(file) == 'index.html' else file)
            try:
                base = urljoin(document_url, document.base_href)
                if urlsplit(base).scheme not in ('http', 'https'):
                    base = document_url
            except ValueError:
                base = document_url
            for kind, reference in dict.fromkeys(document.links):
                try:
                    value = reference.strip()
                    parsed = urlsplit(value)
                    target_url = urlsplit(urljoin(base, value))
                except ValueError:
                    continue
                explicit = bool(parsed.scheme or parsed.netloc or value.startswith('/') or base != document_url)
                result = check_reference(target_url, source_key, explicit, mounts_by_host, sites)
                if result is None:
                    continue
                target, status, target_file, fragment = result
                counts[status] += 1
                if status != 'resolved':
                    findings.append({'status': status, 'source_site': site['base_url'],
                                     'source_file': file, 'source_url': document_url, 'attribute': kind,
                                     'reference': reference, 'target_url': target_url.geturl(),
                                     'target_site': target['base_url'], 'target_file': target_file,
                                     'fragment': fragment})
    rows = [site for _, (site, _) in sorted(sites.items())]
    return {'schema': 'pages-links-audit/1',
            'generated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'sites': rows, 'findings': findings,
            'summary': {'available_sites': sum(row['status'] == 'available' for row in rows),
                        'partial_sites': sum(row['status'] == 'partial' for row in rows),
                        'unavailable_sites': sum(row['status'] == 'unavailable' for row in rows),
                        'in_scope_references': counts.total(),
                        **{key: counts[key] for key in ('resolved', 'missing_file', 'missing_anchor',
                                                       'target_unavailable', 'anchor_unverified')}}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--report', type=Path, help='write the JSON report to this path (default: stdout)')
    args = parser.parse_args(argv)
    try:
        report = audit(args.manifest)
        content = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
        if args.report is None:
            sys.stdout.write(content)
        else:
            args.report.write_text(content, encoding='utf-8')
    except (OSError, ValueError) as exc:
        parser.exit(2, f'{exc}\n')
    summary = report['summary']
    if summary['missing_file'] or summary['missing_anchor']:
        return 1
    return 3 if summary['unavailable_sites'] or summary['partial_sites'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
