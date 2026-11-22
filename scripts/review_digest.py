"""Offline, source-linked attribution from gh_ops issue-comments --save or REST rows."""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gh_ops import _preview, gh_identity

HEAD = re.compile(r"^(?:[-*] +)?head(?:_sha)?[:：] *`?([a-f0-9]{7,40})`?(?=$|[ (（])", re.I)
HEADER = re.compile(r"^(from|to):\s*(.+)$", re.I)
RESOLUTION = re.compile(r"^(?:Resolved|解消):\s*issuecomment-(\d+)\[(\d+)\]\s+(.+)$", re.I)
IDENTIFIER = re.compile(r"(?<![\w/])(?:[\w.-]+/[\w.-]+)?#\d+\b|\b[0-9a-f]{7,40}\b|"
                        r"(?<![\w/])(?:[\w.-]+/)+[\w.-]+\.[\w.-]+", re.I)


def visible_lines(body):
    """Exclude Markdown examples before interpreting attribution headers or resolutions."""
    fence = None
    quoted = False
    body = re.sub(r"<!--.*?(?:-->|\Z)", "", body, flags=re.S)
    body = re.sub(r"<(pre|code|blockquote)\b[^>]*>.*?(?:</\1\s*>|\Z)", "", body, flags=re.I | re.S)
    for line in body.splitlines():
        if fence:
            if re.fullmatch(r" {0,3}" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}\s*", line):
                fence = None
            continue
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if marker:
            fence = marker[1]
            yield ""
            continue
        if not line.strip():
            quoted = False
            yield ""
            continue
        if line.lstrip().startswith(">"):
            quoted = True
        if quoted or line.startswith(("    ", "\t")):
            continue
        yield line.rstrip()


def prose(body):
    # Consume matching backtick runs in order. A one-line span can contain a
    # head SHA; multiline code examples must never contribute a resolution.
    inline = re.compile(r"(?<!`)(`+)(?!`)(.*?)(?<!`)\1(?!`)", re.S)
    text = "\n".join(visible_lines(body))
    def redact(match):
        return "\n" * match[0].count("\n") if "\n" in match[0] else match[0]
    return "\n\n".join(inline.sub(redact, part) for part in text.split("\n\n"))


def digest(comments, head, keep=2):
    if not re.fullmatch(r"[a-f0-9]{40}", head) or keep < 0:
        raise ValueError("expected a full lowercase target head and nonnegative keep")
    if not isinstance(comments, list):
        raise ValueError("expected full REST comment rows")
    rows, seen, findings = [], set(), {}
    for comment in comments:
        cid = comment.get("id") if isinstance(comment, dict) else None
        if type(cid) is not int or cid < 1 or cid in seen or not isinstance(comment.get("body"), str):
            raise ValueError("each full comment needs a body and a unique positive ID")
        body = prose(comment["body"])
        lines = [line.strip() for line in body.splitlines()]
        url = comment.get("html_url") or comment.get("url")
        heads = {match[1].lower() for line in lines if (match := HEAD.match(line))}
        claimed_head = next(iter(heads)) if len(heads) == 1 else None
        row = {"covers": [cid], "url": url, "head": claimed_head, "from": None, "to": None,
               "identifiers": list(dict.fromkeys(IDENTIFIER.findall(body))),
               "state_mentions": list(dict.fromkeys(re.findall(r"未確認|未解消|解消|マージ済み", body))),
               "checks": [line.split(":", 1)[1].strip() for line in lines if line.lower().startswith("check:")],
               "findings": []}
        for line in lines:
            for part in re.split(r"\s+/\s+(?=to:)", line, flags=re.I):
                if match := HEADER.fullmatch(part):
                    row[match[1].lower()] = match[2]
        # Historical reviews often name multiple repositories' main/head SHAs.
        # Keep that context without using a prose mention to authorize a resolution.
        row["revision_mentions"] = [
            {"context": _preview(line, 160), "shas": re.findall(r"[0-9a-f]{40}", line, re.I)}
            for line in lines if re.search(r"\b(?:main|head)\b", line, re.I)
            and re.search(r"[0-9a-f]{40}", line, re.I)]
        labels = gh_identity._explicit_severity(body)
        row["severity_counts"] = dict(Counter(label["label"] for label in labels))
        for number, label in enumerate(labels, 1):
            finding = {"number": number, "severity": label["label"], "text": _preview(label["text"], 240),
                       "reported_resolution": None}
            row["findings"].append(finding)
            findings[cid, number] = finding
        if claimed_head == head:
            for line in lines:
                match = RESOLUTION.fullmatch(line)
                if not match:
                    continue
                target = int(match[1])
                finding = findings.get((target, int(match[2])))
                if target in seen and finding:
                    finding["reported_resolution"] = {"covers": [cid], "url": url, "head": head,
                                                      "evidence": match[3]}
        rows.append(row)
        seen.add(cid)
    if keep:
        for row, original in zip(rows[-keep:], comments[-keep:]):
            row["body"] = original["body"]
    return {"target_head": head, "comments": rows}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", help="saved conversation JSON, or '-' for stdin")
    parser.add_argument("--head", required=True, help="current full checkout or PR head SHA")
    parser.add_argument("--keep", type=int, default=2, help="number of recent original bodies")
    args = parser.parse_args(argv)
    try:
        data = json.loads(sys.stdin.read() if args.file == "-" else Path(args.file).read_text())
        comments = data.get("comments") if isinstance(data, dict) else data
        print(json.dumps(digest(comments, args.head, args.keep), ensure_ascii=False))
    except (OSError, ValueError) as error:
        parser.exit(2, f"review-digest: {error}\n")


if __name__ == "__main__":
    main()
