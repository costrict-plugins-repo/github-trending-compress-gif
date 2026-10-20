# Changelog

All notable changes to this plugin will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-05-14

Initial release.

### Added
- `compress` skill (`/compress-gif:compress <path> [target-kb]`) that
  compresses an animated GIF to under a target size (default 200 KB).
- Dual-backend pipeline: both `gifsicle` and `gifski` run on every
  invocation and produce side-by-side outputs (`<stem>.gifsicle.gif` and
  `<stem>.gifski.gif`).
- Self-check preflight (`--check`) that detects both **missing** and
  **broken** (e.g. dyld load failure) tools and prints platform-specific
  install commands.
- Guided install flow: the skill asks for user consent before running
  any `brew install` / `apt-get install` commands.

### Guarantees
- No resize, no FPS change, no frame drops, no format conversion.
- Best-effort behavior when the target cannot be hit without violating
  the constraints: the smallest safe variant is written and the gap is
  reported, rather than silently mangling the GIF.
