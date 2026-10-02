# Execution ledger — Issue #20

Plan: 2026-10-02-government-affairs.md (approved issue/customer requirements).
Base: 77d27042bf92ba8c6fee4fc8d67e15e2099eb217. Branch: codex/government-affairs.
Worktree: native managed government-affairs; original checkout and its pre-existing changes untouched.

## Implementation

- A complete: versioned topic package; read-only candidate preview; additive explicit installation with current-version, candidate hash, evaluated actual-model profile, business review and backup gates; conflicts fail closed.
- B complete: dedicated strict scoring, 0 vs empty vs failed/null, bounded retries (SDK internal retries off), failure-only recovery, scoped null-to-zero database update and fail-closed missing mapping.
- C complete: ordered per-term search diagnostics with partial data retained, true upstream failure propagation, compatible resume fingerprint, final summary failure signal, explicit cold-start baseline log.
- D complete: lazy full-summary/source endpoint and escaped plain-text display, null-score label; existing homepage pagination and response budget preserved.
- E complete: 55 frozen synthetic cases, isolated explicit paid-run evaluator, default no-call preview, actual model/config/prompt/data/implementation fingerprints; complete recomputed report validation and human review gate.
- F complete: README/configuration/operations updated; docs/government-affairs-acceptance.md includes first-release activation, ordinary deploy preservation, recovery and outstanding release checklist.

## Rulings

- Lettered A-F ledger retained because approved issue uses those task names.
- A produces candidate builder, E supplies installer evaluation validation. Nullable B scores propagate through DB and presentation.
- Scoped approved writes used for native worktree outside initial writable roots.
- JSON score_status / score_error / score_attempts are explicitly namespaced metadata; report uses status / error_code / attempts.
- No per-topic summary prompt fork: existing production summary prompt stays authoritative.
- Collection cache identity excludes mutable content fields and mirrors existing tv.cctv.com filtering / people.com.cn protocol normalization.
- An incomplete collection requires reviewed recovery; repeated main invocation cannot silently turn cached partial data into success or trigger paid recollection.

## Verification

Baseline: 245 tests and 12 subtests passed; 19 standalone log checks passed.
RED/GREEN: additive config, strict scoring and recovery, collection/summary, UI, evaluation, SDK retry/logging boundary, real fetcher failures and content-stage resume.
Final: python run_config_tests.py -> 320 passed, 12 subtests passed (19.56 s), then 19/19 log checks; network blocked by runner.
git diff --check: clean.
Browser: isolated in-memory SQLite fixture; page 2/2 of 51 rows, complete summary expansion, first search term, literal script text without dialog, empty-topic view verified. No production database used.

Independent fresh reviewer found two real defects (upstream errors looked empty; post-content hash mismatch). Fixed and added regression tests; second review caught missing main_keyword forwarding and people.com.cn normalization, both fixed with RED/GREEN tests. Final reviewer: no unresolved substantive findings.

## Release work pending

No production connection/change, live collection, paid model run or business approval was performed.
True scoring-model acceptance, individual business review, production backup/additive activation and first-run monitoring remain unchecked release acceptance work. Do not close Issue #20 solely because offline tests pass.
