# Security Policy

## Reporting a vulnerability

Please use **GitHub's private vulnerability reporting** (Security → Report a vulnerability
on this repository) rather than opening a public issue. Include a description, reproduction
steps, and any relevant logs with secrets redacted.

## Scope and notes

- CRS-01 is designed to run locally or on a trusted network. The API has **no
  authentication by design** — do not expose the backend or frontend ports to untrusted
  networks.
- Provider keys (OpenAI, Tavily, Perplexity) are optional and read from `.env`, which is
  gitignored and must never be committed. If a key has appeared anywhere public, rotate it.
- The research pipeline fetches and stores content from the public web. Treat raw evidence
  and source URLs as untrusted input.
- The SQLite KB is a local file; anything you merge into it stays on your machine unless
  you distribute the file yourself.

## Supported versions

The `main` branch only.
