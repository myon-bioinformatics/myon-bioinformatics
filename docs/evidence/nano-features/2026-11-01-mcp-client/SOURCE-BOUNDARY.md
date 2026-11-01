# MCP chat-client comparison boundary

Source: `myon-bioinformatics/mcp-toolcall-lab` at
`06cdc8e4e14d50de45c8b8122a5ccff6911c3404`. This is a partial, byte-verified
source slice, not a complete checkout or a replacement implementation.

## What is being separated

The unchanged public `chat_sim.send_direct` and `send_via_chat` use the real
MCP SDK's `ClientSession` and Streamable HTTP client. The latter constructs
generic chat tool-call messages; it is not an Open WebUI/LibreChat UI or model.
The default runtime export has 72 requirement rows; pruning FastMCP leaves
30 rows. These are marker-bearing export rows, **not installed package counts**.
MCP and Pydantic remain real pinned packages. The SDK closure still includes
Starlette, uvicorn, SSE, crypto/auth and other transitive dependencies; it is not
a stdlib-only client and not removal of every server-capable dependency.

The intact caller import slice is ten modules, totaling 64,801 source bytes:

- `src/mcp_toolcall_lab/__init__.py`: catalog eager; server exports lazy.
- `src/mcp_toolcall_lab/chat_sim.py`: real SDK calls and chat message construction.
- `src/mcp_toolcall_lab/record.py`: log/correlation schema.
- `src/mcp_toolcall_lab/mock/__init__.py` and `mock/common.py`: stdlib IDs/JSONL.
- `src/mcp_toolcall_lab/catalog.py`: seven tool declarations; three deterministic mocks.
- `src/mcp_toolcall_lab/wikipedia_tool.py` and `markdown_lib.py`: imported by catalog.
- `src/mcp_toolcall_lab/pixiv_dictionary_tool.py` and `pixiv_markup_normalize.py`: also imported by catalog.

Wikipedia/Pixiv imports themselves do not fetch; their live functions must not
be called. `markdown_lib` loads vendored Markdown lazily. Selected mock calls do
not use that converter, and no fake/empty vendor file is staged. Other features
requiring it are outside this slice. No `__init__` stub or SDK replacement is used.

## Necessary real fixture remains

The unchanged `src/mcp_toolcall_lab/server.py` is the fixture (10,968 additional
source bytes). The wrapper starts its actual `create_mcp().http_app('/mcp')` with
real uvicorn and FastMCP in a full default runtime environment. No protocol
server is reimplemented. Binding is dynamic-port IPv4 loopback only.

Report these separately:

1. full default runtime environment bytes/packages;
2. thin caller environment bytes/packages;
3. the full fixture server environment, reused rather than installed twice;
4. co-hosting total = full server environment + thin caller environment;
5. one shared full environment as the alternative.

The co-hosting total may exceed a shared full environment. Caller-only savings
are not whole-service or whole-host savings. Runtime source bytes and cache
bytes are also separate from installed distribution bytes.

## Bounded future workload

Eight synthetic cases, each through direct and chat paths: municipality match,
Unicode no-match, station match, unknown station empty success, transaction
match, missing argument error, type error, and one unknown-tool error. Only
`find_municipalities`, `find_stations`, `find_transaction_prices`, and the explicit
misspelled error name `find_transaction_price` are sent. No Wikipedia, Pixiv,
GitHub, arbitrary URL, provider/model, browser or Docker operation.

Independent expectations include exact fictional Exampleville/code90001,
station JR, price52,000,000/year2025/source mock, empty success and error flags.
Complete SDK Pydantic JSON and all chat message fields must match across
environments; no random metadata is silently normalized away. Full raw results
remain available. The selected SDK uses `result.is_error` (original trace and SDK
boundary tests), while wire JSON uses `isError` (original HTTP tests).

Three repetitions × worker counts1/2 × full/thin caller: 12 batches. Each batch
has exactly two fixed jobs against one shared real fixture process,16 calls/job,
384 total planned calls. Both sides execute identical implementations. The
server's startup is outside workload time; caller process/import/receipt costs
are included. Caller sum-RSS, server RSS and simultaneous combined sum-RSS are
recorded separately. These are sampled RSS, not PSS, guaranteed peak or quotas.

Worker startup environments omit inherited credentials, proxies, live flags and
model paths, and use one scratch HOME isolated from the host but shared by the fixture
server and clients, together with an inherited shared TMPDIR. Only process
receipts/logs are distinct; no per-worker filesystem-isolation claim is made. Audit hooks deny process spawning
and non-approved socket activity. Client connects only to its exact loopback
port; fixture makes no outgoing connections. This is defense in depth, not a
claim of OS sandboxing. Parent orchestration has bounded timeouts and finally
terminates/reaps all children on failures, including partial launch failures.

## Tests and current evidence

- 21 stdlib/fake preparation tests passed; no package install or local server.
- 10 **unchanged** `tests/test_catalog.py` unittest cases passed with socket and
  process operations denied. No MCP/FastMCP module imported in that test run.
- Existing trace/SDK transport integration tests remain untouched and staged
  for reference, but are **not run** by this selected-feature comparison.
  `test_trace` imports `test_streamable_http_protocol` → `test_schema` → real
  server at collection, so an external `MCP_SERVER_PYTHON` alone does not make
  it a thin-client test. `test_record` has a similar test-helper dependency.
  `test_sdk_transport_boundaries` intentionally requires FastMCP in process.
- `test_mock_common` imports `chat_sim`, so it is not currently a stdlib-only
  test despite its helper-module description. It also remains unchanged.
- No optional test closure is installed just to run incompatible integration
  tests in the thin environment. The full child test suite/CI is preserved;
  the focused proof does not claim it was run here.
- Install/output/time/memory observations are now recorded in evidence.json; unknown transfer bytes and effective resource quotas remain null.

Exact Git blob identities, byte sizes and SHA-256s are in `source-receipt.json`.
Raw test logs: `preparation-tests.log`, `catalog-stdlib-tests.log`.
