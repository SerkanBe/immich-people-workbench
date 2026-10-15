# Reporting a bug safely

Use the repository's bug report form. Include enough detail to reproduce the
problem, while keeping your Immich library and credentials private.

Please provide the Workbench release or commit, Immich version, install method
and host OS, and browser/version for a user-interface problem. Unknown versions
are okay. Describe the steps, expected result, actual result, and whether Pending
or explicit Sync was involved. Say whether it repeats after a harmless reload;
skip that step if it could repeat an uncertain change.

Never attach photos, real names, private paths, API keys or passwords,
`state.sqlite3`, reports containing names, or authentication headers. Redact
error text and screenshots. Do not repeat an uncertain merge or Sync just to
capture a report. Use the synthetic demo when a screenshot helps explain a UI
problem.

Optional useful context includes the shortcut used, viewport or zoom, approximate
library size, whether the result was local-only or followed explicit Sync, and
sanitized error text. Do not upload a database or a full diagnostics bundle.

Automated checks use synthetic data and do not collect telemetry. They cannot
confirm behavior with every Immich version, browser layout, or hosting setup.
