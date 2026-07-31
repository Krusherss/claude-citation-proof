# Security policy

## What this project stores

Runtime evidence is written to a project's `.proof/` directory. Depending on
the command options and available tools, that directory can include URLs,
verbatim quotes, fetched page text, screenshots, HTML archives, and hook logs.
The hooks best-effort add `.proof/` to each target repository's private
`$GIT_COMMON_DIR/info/exclude`, and this repository's `.gitignore` excludes it
at every depth. These protections apply to untracked files only: they do not
remove already-tracked evidence or prevent `git add --force`. Treat the
directory as local evidence, not publishable source code.

Do not use citation URLs containing credentials or private tokens. URL query
strings are part of proof identity and may be recorded in manifests and logs.
Use only sources you are authorized to fetch and retain.

The Playwright browser is launched with a fresh context; do not modify the
scripts to load a personal browser profile or cookies into proof runs.

## Before publishing a fork

- Inspect the complete outgoing commit range, not only the working tree.
- Run a secret scanner locally and keep GitHub push protection enabled.
- Confirm `.proof/`, browser profiles, transcripts, caches, and local settings
  are absent from Git history.
- If a real credential is ever committed, rotate or revoke it before rewriting
  history. Deleting the visible file alone is not sufficient.

## Reporting a vulnerability

Please open a GitHub security advisory rather than a public issue when the
report contains sensitive reproduction details.
