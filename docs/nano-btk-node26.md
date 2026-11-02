# Browser-test-kit Node 26 metadata profile

`browser-test-kit-document-mirrors-node26` is an explicit dependency-only profile
for browser-test-kit commit `8de4a8c145a1361c477c67e87f0e74a1c45faa50`.
It accepts only the recorded manifest and npm lock blobs and the unchanged
30-record document-mirror dependency closure. The root Node engine is `>=26`.
Marked remains 18.1.1 and linkedom remains 0.18.13; this does not adopt the
separate dependency PRs. ExcelJS remains unchanged in the full source manifest,
and is excluded from this document-mirror projection just as before.

The historical `browser-test-kit-document-mirrors` profile and its recorded
measurements remain unchanged. Existing Bun recipes and their hashes are not
rewritten. Consumers must deliberately select `BTK_NODE26_FEATURE` and its
`BTK_NODE26_SOURCE_BLOBS`, `BTK_NODE26_SELECTED`, and `BTK_NODE26_EXPECTED_PATHS`,
after pinning a reviewed parent revision and verifying the helper SHA-256.

Preparation uses offline npm lock-only projection, not installation. Its receipt
records the source package snapshot, not the live application/test checkout;
consumers run the live checkout's tests and must verify those separately.
