# Bounded audit

Base commit: `2959cc8`.

## Implemented

1. Page rendering and query lifecycle. React's `jsx(type, props, key)` treats the third argument as a key, not children. Classification, jurisdiction and journal buttons had no visible label. Their labels now live in `props.children`. The page waits for both initial queries before initializing setup fields and shows failed status reads with Retry instead of an empty form. The installed SDK re-exports TanStack Query hooks directly; the existing object-form `useQuery` calls were correct.
2. Source switching. Saving CSV mode now explicitly clears the saved Securo URL while retaining credentials and date settings. Previously the omitted URL remained saved and the next scan still used Securo.
3. Package snapshots. Each build uses a new directory and zip. Receipt names include a charge ordinal so identical date/vendor/amount combinations cannot overwrite each other. Rebuilds cannot include leftover receipts or overwrite earlier archives. `output_dir` is now a naming prefix: requesting `/exports/pack` returns `/exports/pack-<unique suffix>` and its `.zip`; callers must use the returned paths.
4. Saved Securo scan identity. Packaging refuses an instance URL change until a new scan succeeds. Attachment caches are scoped by URL, transaction and attachment hashes; remote IDs cannot traverse local directories. Old unscoped caches remain untouched and are not reused. A first build after upgrade downloads those receipts again.

## Verification

`node scripts/smoke-plugin.mjs` remains a quick stub test, not proof of the host renderer.

For the stronger test, use an isolated npm prefix and the installed desktop source:

```sh
export RECEIPT_TEST_DEPS="$TMPDIR/receipt-ledger-test-deps"
export HERMES_DESKTOP_SOURCE=/path/to/hermes-agent/apps/desktop/src
npm install --prefix "$RECEIPT_TEST_DEPS" react@19 react-dom@19 @tanstack/react-query@5 esbuild@0.25 jsdom@26 class-variance-authority radix-ui clsx tailwind-merge @tabler/icons-react nanostores
node scripts/test-render.mjs
python -m pytest tests -q
```

The DOM test uses real React hooks, React Query, installed SDK component source and the installed route classifier. Only the registry storage, pane-reveal side effects, notifications and synthetic REST responses are substituted. It checks route discovery, charge rendering, visible action labels, async setup hydration, switching source, status failure and Retry. This is a jsdom test, not an Electron or authenticated browser session. React development-mode key warnings remain visible.

Before the fixes the DOM test failed with `missing visible button: Business`. New route tests failed because identical charges shared one receipt filename, packaging accepted a changed instance, and the cache had no source namespace. After the fixes, the DOM test and all 195 Python tests passed locally. No product dependencies were added.

## Deferred

- The reported live sidebar click leaving the welcome page was not reproduced or verified in the user's desktop. This work proves the installed route classifier discovers the contribution and mounts its React page in isolation. It does not prove the running desktop uses the same SDK revision or that its workspace pane is frontmost.
- Review buttons create vendor-substring standing rules, potentially affecting other vendors. Add explicit scope selection or confirmation before changing those semantics.
- Package CSV fields need spreadsheet-formula escaping for untrusted vendor/account/category text.
- Failed or disappeared receipt downloads appear in API errors but not the package's missing-paper CSV. Package completeness reporting should distinguish saved coverage from exported documents.
- Cache identity does not yet distinguish different users/workspaces at the same Securo URL. URL equality also cannot detect a service replaced behind the same URL.
- Saved-ledger shape validation, atomic scan/settings writes and settings validation for malformed request bodies need a separate pass.
- Initial query failures now offer Retry. Background refresh errors while cached data is present still need an explicit stale-data banner.
- JSX list-key warnings and theme/contrast, narrow-screen layout and real Electron navigation remain unverified or deferred. The new render test is opt-in because it requires installed host source; existing CI still runs the stub smoke test.
