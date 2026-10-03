# mimic

Intercept an app you are authorized to inspect, learn its API shape, then call it from Python like a library. Client generation now uses OpenAI rather than a local Claude CLI, and `mimic agent` can perform software-engineering work in an isolated OpenAI-hosted sandbox.

```python
from hinge_client import Hinge

acc = Hinge()                 # reuses your captured session
recs = acc.get_recommendations()
acc.like(subject_id, comment="hi lol")
```

## Architecture

```text
capture traffic -> extract session -> redact samples -> OpenAI Responses API -> generated client

local source -> filtered archive -> OpenAI-hosted sandbox -> report + patch + workspace
                                    (network disabled)
```

Generated clients remain ordinary Python built on `mimic.App`.

## Install

Requires Python 3.10+.

```bash
sh install.sh
export OPENAI_API_KEY="..."
mimic doctor
```

`OPENAI_API_KEY` is used by both `mimic gen` and `mimic agent`. Override the defaults with `MIMIC_OPENAI_MODEL` and `MIMIC_AGENT_MODEL`.

## Capture and generate

```bash
mimic record
mimic hosts
mimic learn prod-api.example.com
mimic gen prod-api.example.com
```

For a HAR file:

```bash
mimic hosts --har traffic.har
mimic learn api.example.com --har traffic.har
mimic gen api.example.com --har traffic.har
```

Audit the redacted prompt without making a model request:

```bash
mimic gen api.example.com --har traffic.har --prompt-only
```

`mimic gen` uses the OpenAI Responses API with `store=False`. Common credential-shaped fields in query strings and request/response samples are redacted before generation. Redaction is defense in depth; use `--prompt-only` when you need to inspect exactly what will be sent.

## mimic-agent sandbox

Use `mimic agent` for deconstruction, reconstruction, adaptation, refactoring, debugging, and test-driven code changes:

```bash
mimic agent "audit client generation, reproduce failures, fix them, and add regression tests"
```

By default it:

- filters `.git`, environments, dependency/build caches, `.env*`, common key/certificate files, common credential files, symlinks, and oversized files;
- uploads the filtered project to an OpenAI-hosted Linux sandbox;
- disables sandbox network access;
- creates a clean Git baseline inside the sandbox;
- asks the agent to inspect, edit, and test the software;
- downloads a report, Git patch, and reconstructed workspace into `.mimic-agent/`;
- does **not** modify your local project.

Artifacts:

```text
.mimic-agent/mimic-agent-report.md
.mimic-agent/mimic-agent.patch
.mimic-agent/mimic-agent-workspace.zip
```

To apply the returned patch, the local path must be a Git working tree and the patch must pass `git apply --check`:

```bash
mimic agent "fix the failing tests" --apply
```

Other options:

```bash
mimic agent "task" --path ./some-project
mimic agent "task" --container-size small
mimic agent "task" --container-size large
mimic agent "task" --model gpt-6-astra
```

The initial sandbox archive is capped at 5 MiB to stay within the hosted inline-file limit. For a large repository, point `--path` at the smallest relevant project/subtree.

## The library

```python
from mimic import Session

Session.from_mitm("prod-api.example.com")
Session.from_curl(open("copied.txt").read())
Session.from_har("traffic.har", "api.example.com")
Session(base_url="https://api.example.com", headers={...})
```

`.get(path)`, `.post(path, json=...)`, and the other common HTTP helpers return parsed JSON and raise `requests.HTTPError` for failed responses. A `401` on an idempotent request can refresh captured credentials once from mitmweb.

## Capture limitations

- **Certificate pinning:** see `docs/pinning.md` and the existing `mimic unpin` flow. Use only on software/accounts you are authorized to inspect.
- **DPoP / sender-constrained tokens:** captured requests may not be replayable; see `docs/dpop.md`.

## Security and operating rules

- Keep `OPENAI_API_KEY` in your shell or secret manager; mimic does not place it inside the hosted sandbox.
- The engineering sandbox runs with network access disabled by default.
- `mimic agent` never applies returned changes unless you pass `--apply`.
- Review the report and patch before committing changes.
- Use mimic only with accounts, applications, and data you are authorized to inspect, and follow applicable terms and law.

## License

MIT, see [LICENSE](LICENSE). Provided as-is, no warranty.
