# Claude Citation Proof

Deterministic proof for web citations, plus a small Claude Code hook layer that
holds an agent accountable for sources it actually opened.

The core proof builder is model-agnostic: any model, agent, script, or CI job can
give it a URL and a verbatim quote. The automatic source detection and Stop-time
enforcement are Claude Code-specific because they use Claude Code's hook events
and transcript payload.

## What it does

For a URL and an expected verbatim quote, `scripts/cite_proof.py` builds a local
evidence record with:

- a W3C text-fragment deep link;
- a deterministic `present`, `absent`, or `unreadable` verdict;
- served HTML or PDF text checking;
- rendered-DOM checking through a fresh Playwright browser context;
- a viewport screenshot when the rendered page contains the quote;
- a SingleFile HTML archive when the optional CLI is available;
- an optional Wayback Machine snapshot;
- a JSON manifest keyed by the canonical URL and quote.

The Claude Code integration adds two hooks:

- `proof_source_logger.py` records HTTP(S) URLs returned by supported web tools,
  scoped to the current session.
- `citation_proof_gate.py` checks `Src:` / `Quote:` blocks against the manifest
  and detects fetched-but-uncited URLs before Claude stops.

This is a narrow citation-accountability mechanism. It is not a general factual
correctness oracle, and a `present` verdict proves only that the quoted text was
found at the source—not that the source itself is correct.

## Citation format

Claude's response uses one block per relied-on source:

```text
Src: https://example.org/report
Quote: "The exact wording copied from the source."
```

In warning mode, missing proof or an uncited fetched source is reported but the
turn completes. To enforce blocking in one project:

```bash
mkdir -p .proof
touch .proof/BLOCK
```

On PowerShell:

```powershell
New-Item -ItemType Directory -Force .proof | Out-Null
New-Item -ItemType File -Force .proof/BLOCK | Out-Null
```

You can also set `CITATION_PROOF_MODE=warn` or `CITATION_PROOF_MODE=block`.
The environment variable takes precedence over `.proof/BLOCK`.

## Install

Python 3.10 or newer is required. The deterministic HTTP/HTML lane uses the
standard library. Optional features need extra tools:

```bash
python -m pip install -e ".[test,browser,pdf]"
python -m playwright install chromium
npm install -g single-file-cli
```

`playwright` enables rendered-DOM verification and screenshots. `PyMuPDF`
enables served-text extraction for PDFs. `single-file-cli` enables HTML
archives. Missing optional tools produce honest null fields rather than fake
evidence.

Run the proof builder directly:

```bash
python scripts/cite_proof.py "https://example.org/report" \
  "The exact wording copied from the source."
```

Results are written under `.proof/` and are excluded by this repository's
`.gitignore`.

## Configure Claude Code

Copy the relevant entries from `examples/settings.json` into either:

- `.claude/settings.json` for a shareable project configuration; or
- `~/.claude/settings.json` for all local projects.

Replace `/absolute/path/to/claude-citation-proof` with the clone's real absolute
path. Claude Code passes hook JSON on stdin; the logger runs after matching web
tools, and the gate runs once when Claude stops.

The source logger needs a literal `url` field in the tool input. A plain
`WebSearch` request generally has only a query, so discovery queries alone are
not logged; opening a result with `WebFetch` is. MCP tools are logged only when
their input also contains an HTTP(S) `url` field.

## Privacy and repository safety

`.proof/` can contain raw URLs, query strings, quotes, page text, screenshots,
HTML archives, and local session identifiers. Never commit it. The supplied
`.gitignore` excludes `.proof/` at every depth, but still inspect the complete
outgoing Git history before publishing.

Current behavior preserves the original mechanism: screenshot capture and
`noimg` HTML archive attempts are enabled by default. Rendered-DOM verification
is a proof channel; the JPEG is only a human-review artifact and never decides
the verdict. A future hardening change can make persistence opt-in without
removing rendered-DOM verification.

Do not pass authenticated, credential-bearing, localhost, or private-network
URLs unless you have separately reviewed the retention risk. URL query strings
are currently preserved because they may identify the cited resource.

See `SECURITY.md` for the publication checklist and disclosure policy.

## Tests

The test suite is offline; network, browser, and archive calls are mocked where
the proof workflow is exercised.

```bash
python -m pytest
```

## Design boundary

The portable portion is:

```text
URL + verbatim quote -> proof manifest -> deterministic verdict
```

The Claude Code-specific portion is:

```text
PostToolUse URL ledger -> Stop hook -> warn or block
```

Other agents can use the proof builder today. To get automatic accountability
with another model host, adapt that host's tool-call and final-response lifecycle
to write the same ledger and call the same gate logic.

## License

MIT
