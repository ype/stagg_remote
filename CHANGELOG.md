# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed
- `PUT /api/plan` returned 500 under docker compose. Root cause: Docker auto-created the `./data`
  bind mount as root, so the non-root app user (uid 1000) couldn't write `state.json`. Compose now
  uses a named volume, which inherits the image's `/data` ownership, and the app checks at startup
  that its state directory is writable.

## [0.1.0] - 2026-10-26

### Added
- Reverse-engineered protocol notes for firmware 1.2.26 (`docs/protocol.md`), including live
  captures of a schedule being armed and fired.
- `protocol` module that encodes and decodes the kettle's 17-byte settings record.
- `KettleClient`, an async HTTP client. It checks that firmware is 1.2.26 before writing, requires
  the kettle to be idle on its base before arming, and confirms every write by reading it back.
  Writes that can't be confirmed are never retried automatically.
- `WakeService` with a persisted wake plan (once or daily), a background reconcile loop that
  re-arms daily plans after each firing, and a 3-minute guard against firing twice.
- FastAPI app with status, plan, cancel and clock-sync endpoints, plus a mobile-first single-page UI.
- Dockerfile (multi-stage, non-root, tzdata) and docker-compose.
- ADR 0001: use the kettle's native schedule instead of BLE power or a firmware downgrade.
