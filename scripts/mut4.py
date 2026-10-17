import subprocess,sys
src=open('gh_ops.py').read()
Q='"'
M=[
('write guard flipped', '    if not write:\n        out["status"] = "planned"', '    if write:\n        out["status"] = "planned"'),
('write guard removed', '    if not write:\n        out["status"] = "planned"', '    if False:\n        out["status"] = "planned"'),
('allow_unmerged_commits ignored', '    if unmerged_risky and not allow_unmerged_commits:', '    if unmerged_risky and False:'),
('allow_unmerged_commits inverted', '    if unmerged_risky and not allow_unmerged_commits:', '    if unmerged_risky and allow_unmerged_commits:'),
('unmerged_risky always False', '    unmerged_risky = ahead_by > 0\n', '    unmerged_risky = False\n'),
('ahead_by >1', '    unmerged_risky = ahead_by > 0\n', '    unmerged_risky = ahead_by > 1\n'),
('verify removed', '    verified = after["base_ref"] == base', '    verified = True'),
('no re-read after PATCH', '    after = pull_snapshot()\n', '    after = fresh\n'),
('PATCH body old', '{"base": base})', '{"base": old})'),
('number check removed', '    if int(number) < 1:', '    if False:'),
('number <=1', '    if int(number) < 1:', '    if int(number) <= 1:'),
('non-str base check removed', '    if not isinstance(base, str):\n        raise GhOpsError("base is required")', '    if False:\n        raise GhOpsError("base is required")'),
('no strip', '    branch = name.strip()', '    branch = name'),
('empty base check removed', '    if not branch:\n        raise', '    if False:\n        raise'),
('state check removed', '    if head["state"] != "open":', '    if False:'),
('already_satisfied removed', '    if old == base:', '    if False:'),
('compare reversed', 'f"/repos/{repo}/compare/{urllib.parse.quote(base, safe=\'\')}"\n                    f"...{urllib.parse.quote(old or \'\', safe=\'\')}")', 'f"/repos/{repo}/compare/{urllib.parse.quote(old or \'\', safe=\'\')}"\n                    f"...{urllib.parse.quote(base, safe=\'\')}")'),
('compare quote safe /', "urllib.parse.quote(old or '', safe='')", "urllib.parse.quote(old or '', safe='/')"),
('subjects all commits', 'for item in commits[:5]]', 'for item in commits]'),
('subject old IndexError', '            _commit_message_subject((item.get("commit") or {}).get("message")) for item', '            ((item.get("commit") or {}).get("message") or \'\').splitlines()[0] for item'),
('subject helper strip removed', '        stripped = line.strip()', '        stripped = line'),
('subject limit removed', '            return stripped[:limit]', '            return stripped'),
('subject None branch', '    if message is None:\n        return "(empty commit message)"', '    if message is None:\n        return ""'),
('except catches nothing (compare)', '    except GhOpsError as error:\n        compare_failed = True', '    except ZeroDivisionError as error:\n        compare_failed = True'),
('ok True on mismatch', '    out["ok"] = verified', '    out["ok"] = True'),
('reason missing on mismatch', '        out["reason"] = f"base is {after[\'base_ref\']!r} after PATCH, expected {base!r}"', '        pass'),
('dry_run True after write', '    out["dry_run"] = False\n    if not verified', '    out["dry_run"] = True\n    if not verified'),
('refused_unmerged ok True', '        out["status"] = "refused_unmerged_base"\n        out["ok"] = False', '        out["status"] = "refused_unmerged_base"\n        out["ok"] = True'),
('not_open ok True', '        out["status"] = "not_open"\n        out["ok"] = False', '        out["status"] = "not_open"\n        out["ok"] = True'),
('cli write not passed', 'return pr_retarget(args.repo, args.number, args.base, write=args.write,', 'return pr_retarget(args.repo, args.number, args.base, write=False,'),
('cli default base', 'p.add_argument("--base", default="main", help="new base branch name (default: main)")', 'p.add_argument("--base", default="develop", help="new base branch name (default: main)")'),
('warning text unmerged', 'f"{old_label} has {ahead_by} commit(s) not in {base}; they would join the diff"', 'f"could not compare"'),
('warning text compare', '        out["warning"] = f"could not compare {old_label} with {base}"', '        out["warning"] = "x"'),
('unknown label removed', '    return ref if ref else "(unknown)"', '    return str(ref)'),
('observed_at removed', '"observed_at": _utc_now_iso()}', '}'),
('merged reason', '"merged" if head["merged"] else', '"closed" if head["merged"] else'),
('stale: state skipped', '    if after.get("state") != "open":\n        return "merged"', '    if False:\n        return "merged"'),
('stale: base_ref skipped', '    if after.get("base_ref") != before.get("base_ref"):', '    if False:'),
('stale: head_sha skipped', '    if after.get("head_sha") != before.get("head_sha"):', '    if False:'),
('stale: merged label', '        return "merged" if after.get("merged") else f"PR is {after.get(\'state\')}"', '        return f"PR is {after.get(\'state\')}"'),
('stale result ignored', '    if stale:\n        out["status"] = "refused_stale_pr"', '    if False:\n        out["status"] = "refused_stale_pr"'),
('stale ok True', '        out["status"] = "refused_stale_pr"\n        out["ok"] = False', '        out["status"] = "refused_stale_pr"\n        out["ok"] = True'),
('expect-head-sha ignored (func)', '    if expect_head_sha is not None:\n        prefix', '    if False:\n        prefix'),
('expect-head-sha always refuses', '        if not head.startswith(prefix):', '        if True:'),
('expect-head-sha not passed (CLI)', 'expect_head_sha=args.expect_head_sha, client=client)', 'expect_head_sha=None, client=client)'),
('expect-head-sha not validated up front', '    if expect_head_sha is not None:\n        _sha(expect_head_sha)\n', '    pass\n'),
('tree-match flag removed', '        out["unmerged_commit_tree_match"] = True\n', '        pass\n'),
('validate refs/ removed', '    if branch.startswith("refs/"):', '    if False:'),
('validate .. removed', '    if ".." in branch:', '    if False:'),
('validate .lock removed', '    if segment.endswith(".lock"):', '    if False:'),
('validate not called', '    base = _validate_branch_name(base)', '    base = base.strip()'),
('allow flags swapped (CLI)', '                           allow_unmerged_commits=args.allow_unmerged_commits,\n                           allow_compare_failure=args.allow_compare_failure,', '                           allow_unmerged_commits=args.allow_compare_failure,\n                           allow_compare_failure=args.allow_unmerged_commits,'),
('allow_compare_failure ignored', '    if compare_failed and not allow_compare_failure:', '    if compare_failed and False:'),
('allow_compare_failure inverted', '    if compare_failed and not allow_compare_failure:', '    if compare_failed and allow_compare_failure:'),
('compare guard uses unmerged flag', '    if compare_failed and not allow_compare_failure:', '    if compare_failed and not allow_unmerged_commits:'),
('unmerged guard uses compare flag', '    if unmerged_risky and not allow_unmerged_commits:', '    if unmerged_risky and not allow_compare_failure:'),
('refused_compare ok True', '        out["status"] = "refused_compare_failure"\n        out["ok"] = False', '        out["status"] = "refused_compare_failure"\n        out["ok"] = True'),
('compare_failed never set', '        compare_failed = True\n', '        compare_failed = False\n'),
('404 check != 404', '"not_found" if error.http_status == 404 else str(error)', '"not_found" if error.http_status != 404 else str(error)'),
('404 check -> 403', '"not_found" if error.http_status == 404 else str(error)', '"not_found" if error.http_status == 403 else str(error)'),
('404 check always', '"not_found" if error.http_status == 404 else str(error)', '"not_found"'),
('http_status not set in Client', 'raise GhOpsError(scrub(text, (self._token,)), http_status=response.status)', 'raise GhOpsError(scrub(text, (self._token,)))'),
('http_status attr dropped', '        self.http_status = http_status', '        self.http_status = None'),
('ahead_by->behind_by', '            ahead_by = int(cmp["ahead_by"])', '            ahead_by = int(cmp.get("behind_by", 0))'),
('pre-PATCH re-read removed', '    fresh = pull_snapshot()\n    old_tip_fresh = _git_ref_tip_sha_optional(client, repo, old) if old else None\n', '    fresh = head\n    old_tip_fresh = old_tip_sha\n'),
('pre-PATCH tip re-read removed only', '    old_tip_fresh = _git_ref_tip_sha_optional(client, repo, old) if old else None\n', '    old_tip_fresh = old_tip_sha\n'),
('expect-head-sha not passed (internal)', 'stale = _pr_retarget_stale_reason(head, fresh, expect_head_sha=expect_head_sha)', 'stale = _pr_retarget_stale_reason(head, fresh, expect_head_sha=None)'),
('tree-equality inverted', '        if tree_info["matched"]:', '        if not tree_info["matched"]:'),
('tree-equality always True', '        matched = bool(base_tree) and bool(old_tree) and base_tree == old_tree', '        matched = True'),
('tree-equality empty base tree ok', '        matched = bool(base_tree) and bool(old_tree) and base_tree == old_tree', '        matched = bool(old_tree) and base_tree == old_tree'),
('tree-equality empty both ok', '        matched = bool(base_tree) and bool(old_tree) and base_tree == old_tree', '        matched = base_tree == old_tree'),
('tree-equality compares same branch', '        old_tree = _git_commit_tree_sha(client, repo, old_tip)', '        old_tree = base_tree'),
('tree-equality except removed', '                "new_base_tree_sha": base_tree or None, "old_base_tree_sha": old_tree or None}\n    except GhOpsError:', '                "new_base_tree_sha": base_tree or None, "old_base_tree_sha": old_tree or None}\n    except ZeroDivisionError:'),
('tree_compare out removed', '        out["tree_compare"] = tree_info\n', '        pass\n'),
('tree compare skipped when old missing check removed', '    if unmerged_risky and old:\n        tree_info', '    if unmerged_risky:\n        tree_info'),
('optional tip except removed', '        return _git_ref_tip_sha(client, repo, branch)\n    except GhOpsError:', '        return _git_ref_tip_sha(client, repo, branch)\n    except ZeroDivisionError:'),
('no-SHA raise removed', '    if not sha:\n        raise GhOpsError(f"ref heads/{branch} returned no SHA")', '    if False:\n        raise GhOpsError(f"ref heads/{branch} returned no SHA")'),
('tip stale: equality shortcut removed', '    if old_before == old_after:\n        return ""\n', '    if old_before == old_after and old_before is not None:\n        return ""\n'),
('tip stale: allow_both_missing ignored', '        if allow_both_missing:\n            return ""\n', '        if False:\n            return ""\n'),
('tip stale: allow_both_missing always', '        return "old base tip could not be verified before PATCH"\n', '        return ""\n'),
('tip stale: one-sided accepted', '    if old_before is None or old_after is None:\n        return "old base tip', '    if old_before is None and old_after is None:\n        return "old base tip'),
('tip stale: changed SHA accepted', '    return (f"old base tip SHA changed from {old_before[:12]} to {old_after[:12]} "\n            "before PATCH")', '    return ""'),
('tip stale result ignored', '    if tip_stale:\n', '    if False:\n'),
('tip stale ok True', '        out["reason"] = tip_stale', '        out["reason"] = tip_stale; out["ok"] = True'),
('allow_missing_tips always False', '    allow_missing_tips = compare_failed and allow_compare_failure', '    allow_missing_tips = False'),
('allow_missing_tips ignores flag', '    allow_missing_tips = compare_failed and allow_compare_failure', '    allow_missing_tips = compare_failed'),
('allow_missing_tips ignores compare_failed', '    allow_missing_tips = compare_failed and allow_compare_failure', '    allow_missing_tips = allow_compare_failure'),
('missing ahead_by -> 0', '        if "ahead_by" not in cmp:', '        if False:'),
('missing ahead_by not compare_failed', '            compare_failed = True\n            out["old_base_unmerged_commits"] = None\n            out["old_base_commit_subjects"] = []\n            out["compare_error"] = "missing ahead_by in compare response"', '            out["old_base_unmerged_commits"] = None\n            out["old_base_commit_subjects"] = []\n            out["compare_error"] = "missing ahead_by in compare response"'),
('missing ahead_by compare_error text', '"missing ahead_by in compare response"', '"x"'),
('old_base_tip_sha out removed', '        out["old_base_tip_sha"] = old_tip_sha\n', '        pass\n'),
('rule / start-end removed', '    if branch.startswith("/") or branch.endswith("/"):', '    if False:'),
('rule / end only removed', '    if branch.startswith("/") or branch.endswith("/"):', '    if branch.startswith("/"):'),
('rule / start only removed', '    if branch.startswith("/") or branch.endswith("/"):', '    if branch.endswith("/"):'),
('rule . start-end removed', '    if segment.startswith(".") or segment.endswith("."):', '    if False:'),
('rule . end only removed', '    if segment.startswith(".") or segment.endswith("."):', '    if segment.startswith("."):'),
('rule . start only removed', '    if segment.startswith(".") or segment.endswith("."):', '    if segment.endswith("."):'),
('rule - start removed', '    if branch.startswith("-"):', '    if False:'),
('rule regex removed', '    if _BRANCH_NAME_INVALID.search(segment):', '    if False:'),
('regex: control chars removed', 'r"[\\000-\\037\\177 ~^:?*[\\]\\\\]|@{|//"', 'r"[\\177 ~^:?*[\\]\\\\]|@{|//"'),
('regex: DEL removed', 'r"[\\000-\\037\\177 ~^:?*[\\]\\\\]|@{|//"', 'r"[\\000-\\037 ~^:?*[\\]\\\\]|@{|//"'),
('regex: space removed', 'r"[\\000-\\037\\177 ~^:?*[\\]\\\\]|@{|//"', 'r"[\\000-\\037\\177~^:?*[\\]\\\\]|@{|//"'),
('regex: ~ removed', 'r"[\\000-\\037\\177 ~^:?*[\\]\\\\]|@{|//"', 'r"[\\000-\\037\\177 ^:?*[\\]\\\\]|@{|//"'),
('regex: ^ removed', 'r"[\\000-\\037\\177 ~^:?*[\\]\\\\]|@{|//"', 'r"[\\000-\\037\\177 ~:?*[\\]\\\\]|@{|//"'),
('regex: : removed', 'r"[\\000-\\037\\177 ~^:?*[\\]\\\\]|@{|//"', 'r"[\\000-\\037\\177 ~^?*[\\]\\\\]|@{|//"'),
('regex: ? removed', 'r"[\\000-\\037\\177 ~^:?*[\\]\\\\]|@{|//"', 'r"[\\000-\\037\\177 ~^:*[\\]\\\\]|@{|//"'),
('regex: * removed', 'r"[\\000-\\037\\177 ~^:?*[\\]\\\\]|@{|//"', 'r"[\\000-\\037\\177 ~^:?[\\]\\\\]|@{|//"'),
('regex: [ removed', 'r"[\\000-\\037\\177 ~^:?*[\\]\\\\]|@{|//"', 'r"[\\000-\\037\\177 ~^:?*\\]\\\\]|@{|//"'),
('regex: backslash removed', 'r"[\\000-\\037\\177 ~^:?*[\\]\\\\]|@{|//"', 'r"[\\000-\\037\\177 ~^:?*[\\]]|@{|//"'),
('regex: @{ removed', 'r"[\\000-\\037\\177 ~^:?*[\\]\\\\]|@{|//"', 'r"[\\000-\\037\\177 ~^:?*[\\]\\\\]|//"'),
('regex: // removed', 'r"[\\000-\\037\\177 ~^:?*[\\]\\\\]|@{|//"', 'r"[\\000-\\037\\177 ~^:?*[\\]\\\\]|@{"')
]
res=[]
try:
  for name,a,b in M:
    n=src.count(a)
    if n!=1: res.append((name,f"PATTERN x{n}"));continue
    open('gh_ops.py','w').write(src.replace(a,b))
    r=subprocess.run([sys.executable,'-m','pytest','-q','-x','-p','no:cacheprovider',
                      'tests/test_gh_ops_pr_retarget.py'],capture_output=True)
    res.append((name,'killed' if r.returncode else 'SURVIVED'))
    print(name,res[-1][1],flush=True)
finally:
  open('gh_ops.py','w').write(src)

EXTRA = [
    ('stale ok True (pr stale)', '        out["reason"] = stale\n', '        out["reason"] = stale; out["ok"] = True\n'),
    ('compare_failed never set (missing ahead_by)', '        if "ahead_by" not in cmp:\n            compare_failed = True',
     '        if "ahead_by" not in cmp:\n            compare_failed = False'),
    ('compare_failed never set (except)', '    except GhOpsError as error:\n        compare_failed = True',
     '    except GhOpsError as error:\n        compare_failed = False'),
]
for name, a, b in EXTRA:
    n = src.count(a)
    if n != 1:
        res.append((name, f"PATTERN x{n}"))
        print(name, res[-1][1], flush=True)
        continue
    open('gh_ops.py', 'w').write(src.replace(a, b))
    r = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-x', '-p', 'no:cacheprovider',
                        'tests/test_gh_ops_pr_retarget.py'], capture_output=True)
    res.append((name, 'killed' if r.returncode else 'SURVIVED'))
    print(name, res[-1][1], flush=True)
open('gh_ops.py', 'w').write(src)
print("TOTAL", len(res), "killed", sum(r[1] == 'killed' for r in res), "survived",
      [r[0] for r in res if r[1] == 'SURVIVED'], "bad", [r for r in res if r[1].startswith('PATTERN')])

