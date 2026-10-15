# Release checklist

Use this checklist for each release candidate. CI results apply to the exact
commit tested; rerun checks after any source change. CI does not create versions,
tags, or releases.

## Candidate

- [ ] Record the intended commit SHA and release tag.
- [ ] Review the complete diff and confirm it contains no private state or
  unrelated changes.
- [ ] Confirm the release notes describe user-visible changes and known limits.

## Automated checks

Record the run URL or local command, date, runtime/platform, and result for each
check. Do not report a skipped or unavailable check as passed.

| Check | Environment | Result and evidence |
| --- | --- | --- |
| Python discovered tests | Ubuntu, Python 3.10 and 3.13 | |
| Python discovered tests | Windows, Python 3.10 and 3.13 | |
| JavaScript discovered tests and syntax | Ubuntu, Node 24 | |
| Tracked-file and documentation-link check | Ubuntu | |
| Whitespace check for candidate change range | Candidate checkout | |

CI runs synthetic tests only. A successful run does not establish real Immich
compatibility, cross-browser layout/focus, permissions, proxy behavior, or
operational deployment behavior. Record manual verification separately,
including anything skipped and why.

## Pending and recovery

- [ ] Confirm queueing and editing work locally without calling an Immich write.
- [ ] Open Sync review, cancel it, and verify no upstream write was sent.
- [ ] Exercise retryable and failed operations with synthetic responses; confirm
  errors remain visible and retry does not silently repeat uncertain work.
- [ ] Change the queue after opening Sync review and confirm a stale review is
  rejected and requires another explicit confirmation.
- [ ] Restart with pending and partial work; verify saved queue/checkpoints
  recover and inflight/uncertain operations require human reconciliation rather
  than automatic replay.
- [ ] Confirm successful reconciliation/retry resumes only the safe remaining
  steps and that explicit Sync is still required.

## Authentication and browser

- [ ] Check password login and key login in an isolated synthetic setup.
- [ ] For API-key authentication, verify required permission scope, missing
  permission failure, revocation behavior, and replacement/rotation; never record
  or include a real key in evidence.
- [ ] Check local sign-in, logout/restart behavior, secure-cookie settings on the
  supported transport, and browser focus/layout in a real browser.
- [ ] Exercise the reverse-proxy origin and trusted-proxy policy on the intended
  deployment; record proxy headers/configuration without secrets.

## Docker deployment (when applicable)

- [ ] Build and start the image with a fresh synthetic state volume and verify
  health/readiness and expected non-root file ownership.
- [ ] Restart with the same volume and confirm local state persists; verify a
  different volume remains isolated.
- [ ] Start explicit Sync and send SIGTERM; confirm shutdown waits safely or
  reports an interrupted/uncertain result that requires reconciliation.
- [ ] Verify documented proxy and secure-cookie behavior through the actual
  reverse-proxy path, including rejection of untrusted forwarded headers.
- [ ] Confirm the published image corresponds to the exact source SHA whose
  reusable checks gate passed.

## Live compatibility and evidence limits

- [ ] Record separately whether the app was checked only with the synthetic mock
  or also against a real Immich server. Include the version and non-sensitive
  actions performed; do not include real names, photos, credentials, or paths.
- [ ] Identify the exact automated suites run and manual checks skipped. The mock
  exercises designed request/response cases; it does not establish behavior
  against other Immich versions or real permission enforcement.
- [ ] Record real browser version and tested viewport for layout/focus findings.
  Node DOM doubles do not prove real browser layout or focus behavior.
- [ ] Record deployment type and versions for proxy/container findings. A local
  test does not establish a particular NAS, proxy, or production environment.

## Evidence and decision

| Item | Evidence / result |
| --- | --- |
| Exact source SHA and tag | |
| CI run(s) | |
| Mock or live Immich version and actions | |
| Browser, deployment, and proxy environment | |
| Known gaps or skipped checks | |
| Maintainer release decision and date | |

Keep the release decision with the maintainer. Workflow success does not publish
anything or imply that release approval has been given.
