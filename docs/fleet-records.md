# Fleet records: identifiers, relations, basis and time (minimal spec)

`fleet_records.py` links the records the repositories already keep, so two questions get a direct answer:

- **What changed → where must it be updated, retested or rechecked** (変更の影響先 / 再テストが必要な対象 / 再検証が必要な記録)
- **What is a judgement based on** (判断の根拠)

It is owned by the parent (AGENTS.md: fleet tooling lives here only) and runs inside the existing entry point,
`vendor_fleet_plan.py` (`impact`, `report`, `graph --format jsonld`, `query why|reverify`). It reads records and
never rewrites them; it adds no database, no RDF/OWL store, no network beyond what `--fetch` already does, and no
LLM. Python 3.11+ standard library only (`tomllib`, `ast`, `json`, `hashlib`).

## 1. Identifiers

| Class | Node string | Identity |
| --- | --- | --- |
| Repository | `Repo:` | GitHub `repository_id` from `child-heads.json`; a name is a label. Previous names (`optional.previous_names`) resolve to the current name everywhere, records included. |
| Directory | `Repo:prefix/` | a hub's subtree prefix, or a directory a record names as its scope |
| File / Workflow / Record | `Repo:path` | path at one revision (commit SHA, or the parent's working tree) |
| Entry | `Repo:path#key` | one entry of a record: `propositions.toml#P1`, `ledger.jsonl#P1@<recorded_at>` (one judgement), `ledger.jsonl#P1` (the verdict in that ledger) |
| Run | `Repo:actions/runs/N[/attempts/M]` | a GitHub Actions run, or one attempt |
| PullRequest | `Repo:#N` | repository-qualified, so `app#5` and `relay#5` never meet |

Versions are commit SHAs, Git blob ids, sha256 digests or a TOML entry's content at a commit. Times are the
record's own (`recorded_at`, `recorded`, `date`, a snapshot's `generated_at`); the time of the run that read
them is `checkedAt` and is never presented as an observation time.

### From handle to key (鍵の昇格)

A relational table fixes its primary key first. Here identity is a claim that is settled last and can be
withdrawn: a thing gets a handle at once and a key only when the observations support one.

| Step | What happens | Here |
| --- | --- | --- |
| 1 🏷️ handle | give a name that carries no claim | `R1`, a row number, a path, a repository name |
| 2 👁️ observe | add attributes and relations, each with its record, time and basis | section 2 |
| 3 🧩 group | put what cannot be told apart into one class; "indistinguishable" is not "the same" | `rule_graph` classes (PythDRagoras), `sha256-index` |
| 4 ✂️ split | one new observation may split a class; nothing is merged for good | a new row splits a rule class |
| 5 🔑 promote | an attribute set that is complete and unique, and survives change, becomes the key; old handles stay as aliases | `repository_id` over the name, `previous_names` |
| 6 🚨 demote | a key that collides is reported, never overwritten | `duplicate-id`, identity conflict of a repository |

`key_report(items, candidates)` is step 5 and 6 for any list of entries: per candidate it says `key`, `repeat`
(the same entry twice), `collides` or `incomplete`, for those entries only. On the Err-o-RoadRunner corpus
(458 entries): `id` collides for 7 ids; the colliding entries differ in `source_file` and `sha256`;
`id+source_file` is unique where present but 101 entries lack it, so it is `incomplete` and not promoted.

`collides` takes precedence over `incomplete`, then `repeat`, then `key`. Missing entries and collision
groups are counted independently, so a partial observation never hides a known collision. Empty observations
are `incomplete`. `distinct`, `repeats` and `collisions` count complete value groups (a group with differing
entries is a collision, not a repeat); `missing` counts entries. Missing means absent, null or empty string;
zero and false are values. All dictionary entries in a corpus participate, including entries without `id`.
Values and complete entries are compared as canonical JSON: number `1`, string `"1"`, boolean `true` and
number `1.0` are distinct; object member order is irrelevant. Non-finite numbers are rejected. Candidate
field sets must be nonempty, with distinct nonempty names without `+` (reserved for report labels).
Composite values are grouped as tuples, never by joining display strings. Existing output fields and the
five-example cap are preserved; examples are display labels only. Input iterators are read once for all
candidates. Reports are snapshots, not a persistent identity registry: rerun on new observations to demote
a previous key; previously returned reports are unchanged. A `repeat` is not a promoted `key`.

A surrogate key plus a unique constraint added later is the same idea in a table (steps 1 and 5). What a
fixed schema has no place for is steps 3 and 4 — "not yet known to be the same" and "may split later".

### Who owns which logic

One owner per kind of logic; the others call it or read its output, and nothing is copied.

| Logic | Owner | Others |
| --- | --- | --- |
| identifiers, relation vocabulary, basis / compare / state, key promotion, impact and basis walks | parent: `fleet_records.py`, `vendor_fleet_plan.py` | read `fleet.jsonld`; mcp-toolcall-lab `fleet_query` passes through |
| proofs between rules (exclusive, implies), rule classes | PythDRagoras `bitwise_surrogate.py` | Aoi takes it by subtree |
| ledgers that shorten text, round trip, cost with the codebook | TalkCompress | the parent's compressed facts and `rule_graph`'s listing reuse the idea, not the code |
| GitHub reads (runs, attempts, heads) | gh_identity | `ci_state` takes its rows |
| a record's own format | the repository that writes it | the parent adds a reader in section 3, never a writer |

New graph logic goes to the owner of its row; a new row is added here before a second copy appears anywhere.

## 2. Relations

Every relation is an edge **from what is depended on (`source`) to what must be rechecked when it changes
(`target`)**. One breadth-first walk is the impact; the reverse walk is the basis. The dependency edges of the
fleet graph (`vendor`, `launcher`, `license`, `workflow-pin`, `subtree`, `shared-tools`, `heads`, `catalog`)
keep their meaning; the evidence relations are:

| Relation | Code | Reads as | Found by (method) | Applies to |
| --- | --- | --- | --- | --- |
| `tested_by` | `tb` | source is exercised by the test or workflow target | `ast-import` (observed), `workflow` (observed), `text-mention` (derived), `locked_ci` (registry) | vendored copies, generated files and their generators |
| `produced_by` | `pb` | target is generated by source (`prov:wasGeneratedBy`) | `generator-marker`: "Generated by X" in the first 5 lines; `*.generated.json` `source`/`generator` | Ironmate slides, TalkCompress tense matrix, flutter `.g.dart`, mcp-toolcall-lab mocks and catalog |
| `derived_from` | `df` | target was computed or recorded from source (`prov:wasDerivedFrom`) | `sha256`, `git-blob`, `sha256-index` (derived), `ledger`, `receipt`, `receipt-url` (derived), `junit-corpus` | ledgers, input bundles, provenance and source receipts, browser receipts, corpus goldens |
| `observed_in` | `oi` | target was captured from the run source | `junit-corpus` | Err-o-RoadRunner JUnit failure corpus |
| `verified_by` | `vb` | source is checked by the record target | `receipt` | browser evidence receipts (`btk-evidence-gif/*`) |
| `supports` / `contradicts` | `sp` / `cd` | source (a judgement) supports / contradicts target (the verdict) | `ledger` | proposition ledgers (Supported / Refuted; Inconclusive is `pending`, neither) |
| `depends_on` | `pd` | target waits for source to merge | `audit-snapshot` (`ci_status` stacked PRs) | `--audit data.json` |

Each relation carries `lock` (the record asserting it), `basis`, `method`, and when they apply `compare`,
`state`, `pin` (recorded commit), `sha256`, `observed_at`, `value` (verdict, classification, counts, history).
Dependency edges additionally carry `registered`: whether `vendor-consumers.json` registers that copy.

### Basis, comparison, state

- `basis`: **observed** (read from a record or the tree) · **derived** (a fixed rule over records, e.g. a
  file-name mention or a hash found in a directory) · **hypothesis** (a candidate, e.g. a receipt naming an
  encoder module that resolves to no file). Hypotheses are listed as 候補 and never walked by `impact`.
- `compare`: **match** · **different** · **unknown**, and for commits **same** · **different** · **unknown**.
  A different commit or blob is never called old, incompatible or "must update". Plan rows say it separately:
  `content` (bytes), `commit` (pin vs source), `compatibility` (`not_assessed` until a test run says otherwise).
- `state`: **not_collected** 未取得 · **running** 実行中 · **success** 成功 · **failure** 失敗 ·
  **not_applicable** 対象外 · **unverifiable** 検証不能 · **pending** 未判定. A missing receipt, report or
  result is `not_collected`, never success.

### History is kept, not overwritten

- Observations at different commits or times are separate statements (dedup key includes `pin` and
  `observed_at`); one identifier with two contents is a `duplicate-id` finding, the first is kept.
- A ledger's newest judgement per proposition is the current one; its `value.history` keeps the earlier rows
  (last 8 inline, all counted) and `rows_with_counterexamples`, so a newer Supported never hides an older Refuted.
- CI per head (`ci_state`): only rows for the current head count; every attempt is kept; a retry that passed
  sets `retried_after_failure`; separate jobs (environments) stay separate.
- Evidence graphs need not be DAGs: `relation_cycles` reports cycles and the walk continues for the rest;
  `graphlib` orders only the update plan.

## 3. Record adapters (existing formats, recognised by content)

Candidates by format name conventions (`RECORD_PATTERNS`), then by content. Nothing is rewritten.

| Record | Relations | Checked | Stays unknown |
| --- | --- | --- | --- |
| `vendor.lock.json`, `shared-tools.lock.json`, `html-import-vendor/1` | vendor/launcher/license (+ `registered`) | content by blob, commit by pin | compatibility (tests) |
| Generator markers, `*.generated.json` | `produced_by` | generator path exists (`generator-missing` otherwise) | whether the output is current (regenerate and diff) |
| `docs/evidence/*.json` `btk-evidence-gif/*` | `verified_by` image→receipt, `derived_from` page→receipt, encoder pin | five separate layers: exists · digest · format (GIF/PNG header) · operation (shots, summary) · commit | visual expectation (`not_collected`: no review record exists) |
| `*/junit_corpus/classification.json` | `observed_in` run→case, `derived_from` case→golden | case/golden present, duplicate ids, FOUND / NOT FOUND / HINT counts (observed / absent / hypothesis) | run attempts (the corpus does not record them) |
| `cycles/*/outputs/ledger.jsonl` | `derived_from` data→judgement (by sha256 in the cycle), definition→judgement (TOML entry at `code_version` vs now), `supports` / `contradicts` | data hash, definition entry | definitions not present at `code_version` (moved histories) |
| Input bundles, provenance and source receipts (`path`/`sha256`/`git_blob`/`repository`/`revision`) | `derived_from` file→record, in the repository the item names | sha256 or blob against that repository's current tree; logical names by hash | paths the record does not place in any repository (installed packages, exports) |
| `vendor-consumers.json` `locked_ci` | `tested_by` copy→workflow | workflow sha256 | — |
| `ci_status` `parent-audit/1` (`--audit`) | `depends_on` PR→PR, CI state per head | current head only | runs not observed for that head |

Relations naming a repository outside the public, non-archived inventory are dropped and only counted
(`outside_inventory`, per record). Only identifiers, hashes, commits, counts and verdicts are copied; no log
text, environment description or secret from the records reaches the report, the page or `fleet.jsonld`.
Limits: 4000 relations per run (`dropped` counts the rest), 600 entries per record, sorted output.

## 4. Answers

```sh
python -S vendor_fleet_plan.py impact py_launch.cjs --checkouts ..                       # all answers, CLI
python -S vendor_fleet_plan.py impact Aoi:cycles/c001-chunichi/outputs/season.jsonl --checkouts .. --format json
python -S vendor_fleet_plan.py report --checkouts .. --audit site/dist/data.json          # + PR dependencies, CI per head
python -S vendor_fleet_plan.py query why "Aoi:cycles/c001-chunichi/outputs/ledger.jsonl#P1"   # published graph only
python -S vendor_fleet_plan.py query reverify PySaiyanWeBoo:web_ui.py
```

`impact` returns `rows` (unchanged fields: `node`, `depth`, `kind`, `via`; added: `group`, `basis`) and
`view`: **変更の影響先** (`affected`: repository, direct or indirect, relation groups), **判断の根拠**
(`basis`: record, method, basis, pin / blob / sha256, compare, state, registered), **再テストが必要な対象**
(`retest`: test or workflow, for which file, method), **再検証が必要な記録** (`recheck`: judgements, receipts,
goldens, records), **再テスト対応が未登録** (`unregistered`: reached files no test, workflow or `locked_ci`
names), **候補** (hypotheses, not walked). With checkouts, `impact` also reads the reached repositories' tests for
files no record links yet. The Pages dashboard shows the same per repository ("判断の根拠・再テスト") and links
`fleet.jsonld`; the MCP tool `fleet_query` (mcp-toolcall-lab) answers `why` and `reverify` from it.

Real data, 2026-11-09 (all 24 public checkouts at main, parent at this branch):

| Input | Answer |
| --- | --- |
| `report` | 2,325 relations: tested_by 346 · derived_from 1,415 · observed_in 451 · supports 55 · contradicts 44 · produced_by 8 · verified_by 6; 69 plan rows `content: match`, `commit: different` (a moved commit, same bytes); 7 duplicate corpus ids in Err-o-RoadRunner |
| `impact py_launch.cjs` | 14 repositories affected (11 direct), each with its lock, pin, blob and registration; 84 tests and workflows; 5 browser receipts as candidates only |
| `impact Aoi:…/outputs/season.jsonl` | 155 records to recheck (the c001 judgements whose `data_sha256` is this file), definitions `match` for all 114 current judgements |
| `impact PySaiyanWeBoo:web_ui.py` | 5 repositories, 71 tests/workflows, `Aoi:vendor/web_ui.py` unregistered |
| `impact Err-o-RoadRunner:triage.py` | no record names it; 14 tests and workflows found by reading the tree |

## 5. Where this applies (per repository)

| Repository | Existing records used | Result |
| --- | --- | --- |
| myon-bioinformatics | registry, child-heads, catalog, locks, workflow pins, audit snapshot, `docs/evidence` receipts | dependency basis + `registered`; nano-feature source receipts linked to the named repositories |
| Err-o-RoadRunner | JUnit corpus, goldens | runs→cases→goldens, FOUND/NOT FOUND/HINT kept apart, duplicate ids found |
| BrowserTestKit, Ironmate, PySaiyanWeBoo, TalkCompress, ascii_artist, Aoi | `docs/evidence/browser.json` | receipt layers; consumers of the kit without a receipt are `not_collected` |
| xprobe, yourself, gh_identity | locks; xprobe `docs/experiments/*.json` producer commit | tests per vendored copy; commit same/different. GitHub fetching stays in gh_identity: `ci_state` takes its `run_history` rows (run, attempt, head) or the audit snapshot's check rows |
| Aoi (QueRyu, BlueProbe, SakAnalytics, PythDRagoras, NagoyAction, DRAgoWing as subtrees) | ledgers, propositions, input bundles, evidence JSON | judgements ← data and definitions; verdict history; bundle pins by hash. The six children keep their own records; no extra format was needed |
| TalkCompress | browser receipt, `docs/*-replay.json`, results provenance, `tense_matrix.md` marker | commits recorded per result; generator link. Meaning/compression quality is not inferred from links |
| mcp-toolcall-lab | `catalog.generated.json`, generated mocks, provenance JSON | `produced_by` to the server/export modules; `fleet_query` gains `why` / `reverify` |
| flutter_navigation_basic, myon-bioinformatics.github.io | `.g.dart` markers, vendored web_ui | generator links, retest registration gaps listed |
| PySaiyanWeBoo, markdown, cli_args, nvd_nist_known_vulns, search_seq_including_spaces, ascii_artist | locks | tests per vendored copy; no domain records to add (no change in those repositories) |

A CVE lookup, a coordinate system or a rendering setting is not modelled: those repositories keep no record
of that kind yet, and an empty lookup must not read as "no vulnerability". Add an adapter here when a record
appears, not before.
