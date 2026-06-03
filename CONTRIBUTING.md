# Contributing

Thanks for your interest in LiraAi. This is a **hobby project** — there is no paid support and no guaranteed response time.

## How to help

- **Bug reports:** open a GitHub issue with steps to reproduce, OS, Python version, GPU (if any), and relevant log excerpts. Do not paste secrets (`config.json`, `.env`, tokens).
- **Pull requests:** welcome for docs, install scripts, and clear bug fixes. Keep changes focused; match existing code style (`ruff` in `pyproject.toml`).
- **Questions:** issues are fine; there is no official chat or SLA.

## What we usually won't merge

- Bundled model weights or personal configs/personas
- Large refactors without a prior issue/discussion
- Changes that require the maintainer's private weights or hardware to verify

## Development setup

See [docs/getting-started.md](docs/getting-started.md) (Russian). Quick path:

```bash
./scripts/install-deps.sh
./scripts/setup.sh
./scripts/smoke_imports.sh
```

Model weights are **not** in the repository — use your own GGUF paths in `config.json`.

## License

By contributing, you agree that your contributions are licensed under the [MIT License](LICENSE).
