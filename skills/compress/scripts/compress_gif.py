#!/usr/bin/env python3
"""
Compress an animated GIF to under a target size (default 200 KB) using TWO
independent backends in parallel, so the user can A/B the results:

  • gifsicle  → fast, lossy LZW tuning, preserves the original palette
                strategy. Tends to win on screen-recording-style GIFs with
                flat colors and large unchanged regions.
  • gifski    → libimagequant-based re-encoder via pngquant. Tends to win on
                photographic / gradient-heavy GIFs where palette quality
                dominates perceived quality at small sizes.

For every run, both backends produce an output file (when available) and the
script prints a side-by-side report. The user picks the one they prefer.

Hard constraints honored by both pipelines:
  • No `--scale` / `--resize` / `--width` / `--height`  → dimensions preserved
  • No `--delay`, no frame slicing, no `--every-nth-frame` → FPS + frame
    count preserved, playback can't become stuttery
  • No format conversion — output is still a GIF

If a backend cannot reach the target without violating those constraints, it
writes its smallest safe variant and reports the gap rather than mangling
the GIF.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


DEFAULT_TARGET_BYTES = 200 * 1024  # 200 KB

# gifsicle ladders
LOSSY_LADDER = [20, 40, 60, 80, 120, 160, 200]
COLOR_LADDER = [224, 192, 160, 128, 96, 64]

# gifski ladder (quality 1-100; default 90)
QUALITY_LADDER = [100, 90, 80, 70, 60, 50, 40, 30, 20]


# ---------------------------------------------------------------------------
# Preflight: tool registry and self-check
# ---------------------------------------------------------------------------

# Each backend has a name, whether it's required, a runtime probe to detect
# both "missing" and "installed but broken" (e.g. dyld load failures), and
# per-platform install hints.
@dataclass
class Tool:
    name: str
    role: str               # "required" or "optional"
    probe: list[str]        # command + args used to verify the tool actually runs
    install_macos: str
    install_linux: str
    reason: str             # one-line reason why this tool is needed


TOOLS: list[Tool] = [
    Tool(
        name="gifsicle",
        role="required",
        probe=["gifsicle", "--version"],
        install_macos="brew install gifsicle",
        install_linux="sudo apt-get install -y gifsicle",
        reason="Primary compression backend (always runs).",
    ),
    Tool(
        name="gifski",
        role="optional",
        probe=["gifski", "--version"],
        install_macos="brew install gifski",
        install_linux="cargo install gifski   # or: see https://gif.ski",
        reason="High-quality alternative backend — produces the second output.",
    ),
    Tool(
        name="ffmpeg",
        role="optional",
        probe=["ffmpeg", "-version"],
        install_macos="brew install ffmpeg",
        install_linux="sudo apt-get install -y ffmpeg",
        reason="Required only by the gifski backend (frame extraction).",
    ),
]


@dataclass
class ToolStatus:
    tool: Tool
    state: str              # "ok" | "missing" | "broken"
    detail: str             # short error/version line


def check_tool(t: Tool) -> ToolStatus:
    """Return the runtime status of a tool.

    - "missing":  not on PATH
    - "broken":   on PATH, but the probe command fails (dyld error, wrong
                  arch, etc.). This is the case the user just hit with the
                  libx265 dylib mismatch.
    - "ok":       probe ran cleanly
    """
    if shutil.which(t.name) is None:
        return ToolStatus(t, "missing", f"{t.name} not on PATH")
    try:
        r = subprocess.run(t.probe, capture_output=True, text=True, timeout=8)
    except (subprocess.TimeoutExpired, OSError) as e:
        return ToolStatus(t, "broken", str(e))
    if r.returncode != 0:
        # Surface just the first non-empty line of stderr — dyld errors and
        # missing-library messages are usually on line 1.
        err = next((ln for ln in (r.stderr or "").splitlines() if ln.strip()), "")
        return ToolStatus(t, "broken", err or f"exit {r.returncode}")
    # First non-empty line of stdout/stderr is the version banner.
    out = (r.stdout or "") + (r.stderr or "")
    banner = next((ln for ln in out.splitlines() if ln.strip()), "")
    return ToolStatus(t, "ok", banner.strip())


def preflight() -> list[ToolStatus]:
    return [check_tool(t) for t in TOOLS]


def _platform_install_cmd(t: Tool) -> str:
    return t.install_macos if sys.platform == "darwin" else t.install_linux


def print_preflight(statuses: list[ToolStatus]) -> None:
    glyph = {"ok": "✓", "missing": "✗", "broken": "!"}
    print("Preflight check:")
    for s in statuses:
        tag = f"[{s.tool.role}]"
        print(f"  {glyph[s.state]} {s.tool.name:<10} {tag:<11} {s.detail}")
    # Per-issue install guidance.
    issues = [s for s in statuses if s.state != "ok"]
    if issues:
        print()
        print("To fix:")
        for s in issues:
            cmd = _platform_install_cmd(s.tool)
            if s.state == "broken":
                # Broken installs usually want a reinstall, not an install.
                cmd = cmd.replace("brew install", "brew reinstall")
                cmd = cmd.replace("apt-get install", "apt-get install --reinstall")
            print(f"  - {s.tool.name} ({s.state}, {s.tool.role}): {cmd}")
        print(f"    why {s.tool.name} matters: {s.tool.reason}" if len(issues) == 1
              else "    (see --check for per-tool rationale)")


def preflight_blocker(statuses: list[ToolStatus]) -> str | None:
    """Return a human message if any *required* tool is missing/broken,
    else None. Optional-backend failures are not blockers — they just
    disable that backend with a clear note in the report."""
    for s in statuses:
        if s.tool.role == "required" and s.state != "ok":
            return f"required tool '{s.tool.name}' is {s.state}: {s.detail}"
    return None


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


@dataclass
class Result:
    backend: str            # "gifsicle" or "gifski"
    output: Path | None     # path to the written file, or None if skipped
    size: int | None        # bytes, or None if skipped
    recipe: str             # human-readable description of the encoder settings
    hit_target: bool        # whether size <= target
    note: str = ""          # extra info (skip reason, best-effort message, etc.)


def human(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n / (1024 * 1024):.2f} MB"
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n} B"


def have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


# ---------------------------------------------------------------------------
# FPS probing (no ffprobe — parse gifsicle --info, which is always available
# when the gifsicle backend runs)
# ---------------------------------------------------------------------------


_DELAY_RE = re.compile(r"delay\s+([0-9.]+)s")


def probe_fps(input_path: Path) -> float:
    """Return the GIF's effective frame rate (most common per-frame delay)."""
    r = run(["gifsicle", "--info", str(input_path)])
    if r.returncode != 0:
        # If gifsicle can't read it, neither can we — caller should bail.
        raise RuntimeError(f"gifsicle --info failed: {r.stderr.strip()}")

    delays = [float(m.group(1)) for m in _DELAY_RE.finditer(r.stdout)]
    delays = [d for d in delays if d > 0]
    if not delays:
        # Some GIFs declare 0 delay (browsers clamp to ~10 cs). Use 10 FPS as
        # a sensible default rather than going to infinity.
        return 10.0

    # Use the mode (most common delay) — robust to one-off long frames.
    from collections import Counter
    most_common, _ = Counter(delays).most_common(1)[0]
    return round(1.0 / most_common, 3)


# ---------------------------------------------------------------------------
# gifsicle backend
# ---------------------------------------------------------------------------


def _gifsicle(args: list[str], src: Path, dst: Path) -> int:
    """Run gifsicle, return output size on success or raise on failure."""
    r = run(["gifsicle", *args, str(src), "-o", str(dst)])
    if r.returncode != 0:
        raise RuntimeError(f"gifsicle failed: {r.stderr.strip()}")
    return dst.stat().st_size


def compress_gifsicle(
    input_path: Path,
    output_path: Path,
    target: int,
    statuses: dict[str, ToolStatus],
) -> Result:
    s = statuses["gifsicle"]
    if s.state != "ok":
        return Result(
            "gifsicle", None, None, f"({s.state})", False,
            note=f"{s.detail} — fix with: {_platform_install_cmd(s.tool)}",
        )

    print(f"[gifsicle] target {human(target)}")

    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        best = (None, None)  # (size, path)
        best_recipe = "<input>"

        def consider(label: str, args: list[str]) -> tuple[int, Path]:
            scratch = tdp / f"{label}.gif"
            sz = _gifsicle(args, input_path, scratch)
            print(f"  {label:<32} → {human(sz)}")
            return sz, scratch

        # Stage 1: lossless
        sz, p = consider("-O3", ["-O3"])
        best, best_recipe = (sz, p), "-O3 (lossless)"
        if sz <= target:
            shutil.copyfile(p, output_path)
            return Result("gifsicle", output_path, sz, best_recipe, True)

        # Stage 2: lossy ladder
        for lossy in LOSSY_LADDER:
            sz, p = consider(f"-O3 --lossy={lossy}", ["-O3", f"--lossy={lossy}"])
            if sz < best[0]:
                best, best_recipe = (sz, p), f"-O3 --lossy={lossy}"
            if sz <= target:
                shutil.copyfile(p, output_path)
                return Result("gifsicle", output_path, sz, best_recipe, True)

        # Stage 3: palette reduction at lossy=200
        for colors in COLOR_LADDER:
            sz, p = consider(
                f"-O3 --lossy=200 --colors={colors}",
                ["-O3", "--lossy=200", f"--colors={colors}"],
            )
            if sz < best[0]:
                best, best_recipe = (sz, p), f"-O3 --lossy=200 --colors={colors}"
            if sz <= target:
                shutil.copyfile(p, output_path)
                return Result("gifsicle", output_path, sz, best_recipe, True)

        # Best-effort
        shutil.copyfile(best[1], output_path)
        return Result(
            "gifsicle", output_path, best[0], best_recipe, False,
            note="could not reach target without resizing or frame drops",
        )


# ---------------------------------------------------------------------------
# gifski backend
# ---------------------------------------------------------------------------


def _extract_frames(input_path: Path, frames_dir: Path) -> int:
    """Extract every frame of `input_path` as a numbered PNG. Returns count."""
    pattern = frames_dir / "frame_%05d.png"
    # -vsync 0 / -fps_mode passthrough preserves the original frame count.
    r = run([
        "ffmpeg",
        "-loglevel", "error",
        "-i", str(input_path),
        "-vsync", "0",
        str(pattern),
    ])
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg frame extraction failed: {r.stderr.strip()}")
    return sum(1 for _ in frames_dir.glob("frame_*.png"))


def compress_gifski(
    input_path: Path,
    output_path: Path,
    target: int,
    statuses: dict[str, ToolStatus],
) -> Result:
    for dep in ("gifski", "ffmpeg"):
        s = statuses[dep]
        if s.state != "ok":
            return Result(
                "gifski", None, None, f"({dep} {s.state})", False,
                note=f"{s.detail} — fix with: {_platform_install_cmd(s.tool)}",
            )

    try:
        fps = probe_fps(input_path)
    except RuntimeError as e:
        return Result("gifski", None, None, "(fps probe failed)", False, note=str(e))

    print(f"[gifski] target {human(target)}  (preserving {fps} FPS)")

    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        frames_dir = tdp / "frames"
        frames_dir.mkdir()
        try:
            frame_count = _extract_frames(input_path, frames_dir)
        except RuntimeError as e:
            return Result("gifski", None, None, "(extract failed)", False, note=str(e))

        if frame_count == 0:
            return Result("gifski", None, None, "(no frames extracted)", False)

        frame_glob = sorted(frames_dir.glob("frame_*.png"))
        best = (None, None)
        best_recipe = ""

        for q in QUALITY_LADDER:
            scratch = tdp / f"q{q}.gif"
            cmd = [
                "gifski",
                "--quiet",
                "--fps", str(fps),
                "--quality", str(q),
                "-o", str(scratch),
                *[str(f) for f in frame_glob],
            ]
            r = run(cmd)
            if r.returncode != 0:
                return Result(
                    "gifski", None, None, f"--quality {q} failed", False,
                    note=r.stderr.strip(),
                )
            sz = scratch.stat().st_size
            print(f"  --quality={q:<3}                    → {human(sz)}")

            if best[0] is None or sz < best[0]:
                best, best_recipe = (sz, scratch), f"--fps {fps} --quality {q}"
            if sz <= target:
                shutil.copyfile(scratch, output_path)
                return Result("gifski", output_path, sz, best_recipe, True)

        # Best-effort
        shutil.copyfile(best[1], output_path)
        return Result(
            "gifski", output_path, best[0], best_recipe, False,
            note="could not reach target without resizing or dropping FPS",
        )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def report(original: int, target: int, results: list[Result]) -> None:
    print()
    print("=" * 64)
    print(f"  original:       {human(original)}")
    print(f"  target:         under {human(target)}")
    print("=" * 64)
    for r in results:
        head = f"  {r.backend:<10}"
        if r.output is None:
            print(f"{head}  skipped       {r.recipe}")
            if r.note:
                print(f"              {r.note}")
            continue
        pct = 100 * (1 - r.size / original) if original else 0
        flag = "✓" if r.hit_target else "△"
        print(f"{head}  {flag} {human(r.size):<10}  ({pct:5.1f}% smaller)  {r.recipe}")
        print(f"              → {r.output}")
        if r.note:
            print(f"              note: {r.note}")
    print("=" * 64)
    # Recommend the smallest output that hit the target.
    hit = [r for r in results if r.hit_target and r.size is not None]
    if hit:
        winner = min(hit, key=lambda r: r.size)
        print(f"  recommendation: {winner.backend}  ({human(winner.size)})")
    else:
        attempted = [r for r in results if r.size is not None]
        if attempted:
            best = min(attempted, key=lambda r: r.size)
            print(f"  best-effort:    {best.backend}  ({human(best.size)})")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Compress a GIF to under a target size using BOTH "
                    "gifsicle and gifski; produce both outputs for comparison."
    )
    p.add_argument("input", type=Path, nargs="?", help="Path to the input .gif")
    p.add_argument(
        "-t", "--target-kb",
        type=int,
        default=200,
        help="Target size in KB (default: 200)",
    )
    p.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for output files (default: alongside input). "
             "Files are named <stem>.gifsicle.gif and <stem>.gifski.gif.",
    )
    p.add_argument(
        "--only",
        choices=["gifsicle", "gifski"],
        default=None,
        help="Run only one backend instead of both.",
    )
    p.add_argument(
        "--check",
        action="store_true",
        help="Only run the prerequisite check. Exits 0 if everything required "
             "is OK, 2 if a required tool is missing or broken. Optional "
             "backends report status but never block.",
    )
    p.add_argument(
        "--skip-preflight",
        action="store_true",
        help="Skip the preflight check (advanced; for testing only).",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()

    # ------------------------------------------------------------------
    # Preflight — always runs unless explicitly skipped. Required tools
    # being missing or broken aborts; optional tools being missing or
    # broken only disables that backend.
    # ------------------------------------------------------------------
    statuses_list = preflight()
    statuses = {s.tool.name: s for s in statuses_list}

    if args.check:
        print_preflight(statuses_list)
        block = preflight_blocker(statuses_list)
        return 0 if block is None else 2

    if not args.skip_preflight:
        block = preflight_blocker(statuses_list)
        if block:
            print_preflight(statuses_list)
            print()
            print(f"error: {block}", file=sys.stderr)
            print("       run with --check to see install commands.", file=sys.stderr)
            return 2
        # If only optional backends are off, print a short heads-up but
        # don't dump the full preflight — the per-backend Result already
        # surfaces it in the final report.
        degraded = [s for s in statuses_list if s.state != "ok" and s.tool.role == "optional"]
        if degraded:
            names = ", ".join(s.tool.name for s in degraded)
            print(f"note: optional backend dependency unavailable ({names}). "
                  f"Run --check for fix instructions.")
            print()

    # ------------------------------------------------------------------
    # Input validation
    # ------------------------------------------------------------------
    if args.input is None:
        print("error: missing input GIF path. Use --check to verify prereqs, "
              "or pass <input.gif>.", file=sys.stderr)
        return 2
    inp = args.input.expanduser().resolve()
    if not inp.exists():
        print(f"error: input not found: {inp}", file=sys.stderr)
        return 2

    target = args.target_kb * 1024
    original = inp.stat().st_size

    out_dir = (args.output_dir or inp.parent).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = inp.stem
    out_gifsicle = out_dir / f"{stem}.gifsicle.gif"
    out_gifski   = out_dir / f"{stem}.gifski.gif"

    print(f"input:  {inp}  ({human(original)})")
    print()

    results: list[Result] = []
    if args.only in (None, "gifsicle"):
        results.append(compress_gifsicle(inp, out_gifsicle, target, statuses))
        print()
    if args.only in (None, "gifski"):
        results.append(compress_gifski(inp, out_gifski, target, statuses))

    report(original, target, results)

    # Exit code reflects whether at least one backend hit the target.
    return 0 if any(r.hit_target for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
