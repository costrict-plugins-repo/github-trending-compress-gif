---
description: Compress an animated GIF to under 200 KB (or a custom target) using BOTH gifsicle and gifski in parallel and present both outputs for comparison. Preserves FPS, dimensions, frame count, and color fidelity. Use when the user asks to "compress this GIF", "shrink this gif", "reduce gif file size", "gif too big", "optimize a .gif", or mentions a specific size target like "under 200 KB" for a GIF.
argument-hint: <path-to-input.gif> [target-kb]
allowed-tools: Bash, Read
---

# Compress GIF (dual-method, with preflight)

Compress the GIF at the path in `$ARGUMENTS` to under a target size
(default 200 KB) using **two independent encoders in parallel** so the user
can pick the better-looking result:

- **gifsicle** — fast, lossy LZW tuning. Wins on flat-color content (UI
  demos, screen recordings, code walkthroughs).
- **gifski** — pngquant-based high-quality re-encoder. Wins on gradients,
  photographic content, and dithered material.

Both encoders are bound by the same hard constraints: no resizing, no FPS
change, no frame dropping, no format conversion. Always use the bundled
script — do not call `gifsicle`, `gifski`, or `ffmpeg` directly.

## The script handles its own prereqs

The script at
`${CLAUDE_PLUGIN_ROOT}/skills/compress/scripts/compress_gif.py` ships with
a built-in preflight (`--check`) that detects both **missing** *and*
**broken** tools (e.g. dyld load failures from ABI mismatches). It tells
the user exactly what to install and exits before doing any work, so the
user never sees a raw codec error.

## What to do

### Step 1 — Preflight

Run the script's self-check first:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/compress/scripts/compress_gif.py" --check
```

Read the output. It lists every tool as one of:

- `✓ tool [required|optional]` — fine, nothing to do
- `✗ tool [required|optional]` — not installed
- `! tool [required|optional]` — installed but broken (probe failed)

The script prints the exact `brew install …` or `apt-get install …`
command for each issue under the `To fix:` section.

### Step 2 — Install missing/broken tools (with user consent)

If preflight reports anything other than all `✓`:

1. **Tell the user what the preflight found**, verbatim — list the
   tool, its state, and the suggested install command.
2. **Explain the impact:** if `gifsicle` is the problem, neither backend
   can run. If `gifski` or `ffmpeg` is the problem, only the second
   backend is unavailable — gifsicle will still produce one output.
3. **Ask permission** before running any install commands. Wording like
   *"want me to run `brew install gifski ffmpeg`? (y/n)"* is ideal.
4. On consent, run the install commands via Bash. Re-run `--check`
   afterward to confirm green.
5. If the user declines an *optional* backend's install, proceed with
   just gifsicle — the script will still run and report one output.
6. If the user declines a *required* tool's install, stop. There is
   nothing useful to do without gifsicle.

### Step 3 — Parse arguments

- First whitespace-separated token of `$ARGUMENTS` is the input path.
- Optional second token is the target size in KB (defaults to 200).
- If `$ARGUMENTS` is empty, ask the user which GIF to compress.
- Expand `~` and resolve relative paths against the user's CWD.

### Step 4 — Compress

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/compress/scripts/compress_gif.py" \
    "<input-path>" --target-kb <target>
```

Pass `--output-dir <dir>` if the user asked for a specific location. Pass
`--only gifsicle` or `--only gifski` only if the user explicitly asks for
one backend — otherwise always run both.

### Step 5 — Report

The script prints a side-by-side table ending with a `recommendation:`
line. Relay to the user:
- both file paths,
- both final sizes + % reduction,
- the recipe each backend settled on,
- the recommendation (smallest output that hit the target).

Add: *"open both and pick whichever looks better — the encoders trade off
differently on different content."* The recommendation line is just
"smallest that hit target," not a quality judgment.

### Best-effort handling

If neither backend hit the target, the script exits 1 and still writes
its smallest safe variants. Surface the gap and explain that hitting the
target would require resizing or dropping frames, which the constraints
forbid.

## Examples

| User says | You run |
|---|---|
| `/compress-gif:compress ~/demos/walkthrough.gif` | `--check`, install if needed, then default 200 KB target |
| `/compress-gif:compress ./big.gif 150` | `--check`, install if needed, then `--target-kb 150` |
| `compress demo.gif under 100kb with gifski only` | `--check`, ensure gifski + ffmpeg present, then `--target-kb 100 --only gifski` |

## Things not to do

- Do not call `ffmpeg`, `gifski`, ImageMagick, or any other encoder
  directly. The script orchestrates them safely.
- Do not rewrite the compression ladders in Bash.
- Do not run `brew install …` silently. Always tell the user what
  you're installing and why, then ask.
- Do not delete or overwrite the original input. The script writes
  new files; the original is untouched.
- Do not stop after one backend finishes — always present both outputs
  unless the user explicitly asked for one.
