# compress-gif

A Claude Code plugin that compresses animated GIFs to a target size
(default 200 KB) **without** changing frame rate, dimensions, frame count,
or visibly degrading the color profile.

Every run executes two independent encoders in parallel —
[`gifsicle`](https://www.lcdf.org/gifsicle/) and
[`gifski`](https://github.com/ImageOptim/gifski) — and writes both outputs
side by side so you can pick whichever looks better. They trade off
differently on different content: gifsicle wins on flat-color material
(screen recordings, code demos), gifski wins on gradients and photographic
content.

If the target can't be reached without violating the size-preservation
constraints, the plugin emits a best-effort output and explains the gap
rather than silently dropping frames or resizing.

---

## Quick start

Inside a running Claude Code session, type these three slash commands:

```
/plugin marketplace add ankshvayt/compress-gif
/plugin install compress-gif@ankshvayt-plugins
/reload-plugins
```

The first command registers this GitHub repo as a marketplace. The second
installs the plugin from it. The third makes the new skill available
without needing to restart Claude Code.

Then use the skill:

```
/compress-gif:compress ~/demo.gif           # default 200 KB target
/compress-gif:compress ~/demo.gif 150       # custom target in KB
```

The first time you run it, the plugin checks your environment and walks
you through installing any missing dependencies (`gifsicle`, optionally
`gifski` and `ffmpeg`) with your consent — no surprise `brew install`s.

---

## Why this plugin

Most "compress a GIF" recipes hit small file sizes by doing things that
quietly degrade the result:

- **Dropping frames** → playback becomes stuttery.
- **Resizing** → loses the original dimensions you cared about.
- **Transcoding to a different palette space** → wrecks the color profile.

This plugin refuses all three. It only tunes knobs that are visually
safe — LZW encoding, perceptual lossy passes, and (as a last resort)
gentle palette reduction down to a floor of 64 colors. If even the
safest aggressive pass can't reach the target, the plugin says so
instead of resorting to the destructive shortcuts above.

It also runs **two** encoders every time and shows both outputs, because
gifsicle and gifski produce subtly different artifacts and the better
choice depends on the content. Running both costs a few extra seconds
and saves you from guessing.

---

## How it works

The script applies passes in order, stopping the moment the target is
met. Whichever pass settles the result is reported as the "recipe."

**gifsicle backend:**

1. Lossless `-O3` (structural optimization, no quality loss).
2. `--lossy=N` ladder: 20 → 40 → 60 → 80 → 120 → 160 → 200. Perceptual
   tweak to the LZW step — not a resolution or frame change.
3. If still over budget at `--lossy=200`, gentle palette reduction:
   224 → 192 → 160 → 128 → 96 → 64 colors. Stops at 64 to keep the
   color profile recognizable.

**gifski backend:**

1. Read the GIF's per-frame delays via `gifsicle --info`, take the
   mode, derive an FPS.
2. Extract every frame as PNG via `ffmpeg` (one-to-one, no rate
   conversion).
3. `gifski --quality` ladder: 100 → 90 → 80 → 70 → 60 → 50 → 40 → 30
   → 20. Stops as soon as the target is met. pngquant chooses the
   palette internally.

Neither backend ever passes `--scale`, `--resize`, `--width`, `--height`,
`--delay`, frame slicing, or `--every-nth-frame`. Frame count and
dimensions in the output are byte-identical to the input; per-frame
delays are preserved exactly by gifsicle and reconstructed at the
detected FPS by gifski.

---

## What's preserved vs. what's tuned

|                                       | gifsicle | gifski |
|---                                    |---       |---     |
| Lossless structural optimization      | `-O3`              | (built in)        |
| Perceptual quality dial               | `--lossy` 20 → 200 | `--quality` 100 → 20 |
| Gentle palette reduction (last resort)| `--colors` 224 → 64 | (pngquant)       |
| Dimensions (`--scale`/`--resize`)     | **never**          | **never**         |
| Frame count (`--every-nth-frame`)     | **never**          | **never**         |
| Per-frame delay / FPS                 | unchanged          | reconstructed at source FPS |
| Format change (→ MP4, WebP, etc.)     | **never**          | **never**         |

---

## Requirements

| Tool       | Role     | macOS                       | Linux (apt)                              | Notes                                  |
|---         |---       |---                          |---                                       |---                                     |
| Claude Code| required | v2.1.0+                     | v2.1.0+                                  | Needed for the `skills/` plugin layout |
| Python     | required | 3.8+ (system Python OK)     | 3.8+ (system Python OK)                  | Standard library only                  |
| `gifsicle` | required | `brew install gifsicle`     | `sudo apt install gifsicle`              | Always runs                            |
| `gifski`   | optional | `brew install gifski`       | `cargo install gifski` (no apt package)  | Enables the second output              |
| `ffmpeg`   | optional | `brew install ffmpeg`       | `sudo apt install ffmpeg`                | Required only by the gifski backend    |

**Windows:** untested. The Python script itself is platform-neutral, but
the `gifsicle`/`gifski`/`ffmpeg` install paths in this README target
macOS and Debian-family Linux. If you're on Windows, WSL2 with the
Linux instructions is the safest route.

You don't need to install these by hand. On the first run, the plugin
detects what's missing or broken — including the case where a tool is
on `PATH` but fails to load due to a dylib ABI mismatch — and surfaces
the exact install command. It **never** runs `brew install` or
`apt install` silently; Claude proposes the command and you approve it.

If you decline an *optional* install, gifsicle still runs and you get
one output instead of two.

---

## Installation

### From this GitHub repo (recommended)

In Claude Code, run the three slash commands shown in [Quick start](#quick-start):

```
/plugin marketplace add ankshvayt/compress-gif
/plugin install compress-gif@ankshvayt-plugins
/reload-plugins
```

To update later (after a new release is pushed here):

```
/plugin marketplace update ankshvayt-plugins
/reload-plugins
```

To uninstall:

```
/plugin uninstall compress-gif@ankshvayt-plugins
```

### For local development

Clone and load the repo directly with `--plugin-dir`. Useful if you want
to hack on the plugin source itself — changes take effect after
`/reload-plugins`. This loads the plugin for the current session only.

```bash
git clone https://github.com/ankshvayt/compress-gif.git
cd compress-gif
claude --plugin-dir .
```

---

## Usage

### Inside Claude Code

```
/compress-gif:compress <path-to-gif> [target-kb]
```

Examples:

```
/compress-gif:compress ~/Desktop/demo.gif
/compress-gif:compress ./screencast.gif 150
compress demo.gif under 100kb with gifski only
```

The skill is model-invocable, so natural-language phrasing also
triggers it.

### As a standalone CLI

The compression script works without Claude Code:

```bash
# Run the preflight without compressing anything:
python3 skills/compress/scripts/compress_gif.py --check

# Default: both backends, 200 KB target, output next to input:
python3 skills/compress/scripts/compress_gif.py input.gif

# Custom options:
python3 skills/compress/scripts/compress_gif.py input.gif --target-kb 150
python3 skills/compress/scripts/compress_gif.py input.gif --only gifsicle
python3 skills/compress/scripts/compress_gif.py input.gif --output-dir ./out
```

Exit codes:

| Code | Meaning                                                         |
|---   |---                                                              |
| 0    | At least one backend produced output at or under the target.    |
| 1    | Both backends produced output, but neither reached the target.  |
| 2    | Preflight failed (required tool missing/broken) or bad input.   |

---

## Output

Both files are written next to the input (or to `--output-dir` if set):

- `<stem>.gifsicle.gif`
- `<stem>.gifski.gif`

The original input is never modified or deleted.

End-of-run report:

```
================================================================
  original:       2.4 MB
  target:         under 200.0 KB
================================================================
  gifsicle    ✓ 187.3 KB   ( 92.4% smaller)  -O3 --lossy=120
              → /path/to/demo.gifsicle.gif
  gifski      ✓ 162.8 KB   ( 93.4% smaller)  --fps 10.0 --quality 70
              → /path/to/demo.gifski.gif
================================================================
  recommendation: gifski  (162.8 KB)
```

The `recommendation` line is just "smallest output that hit the target."
It is **not** a visual-quality judgment — open both files and pick what
looks best for your content.

A `△` flag instead of `✓` means the backend couldn't reach the target
without violating constraints. The file is still written (best-effort)
and the report explains the gap.

---

## Known limitations

- **Variable per-frame delays** — gifski reconstructs frames at a single
  FPS (the mode of the input's per-frame delays). If your GIF has
  intentionally non-uniform timing, gifski may shift the perceived
  cadence slightly. gifsicle preserves per-frame delays exactly.
- **Single-frame "GIFs"** — there's nothing for gifski to gain on a
  static image; gifsicle's lossless pass will usually still shrink it.
- **Inputs with high motion + photographic content + tight targets**
  may best-effort out. That's the design — the alternative is to
  silently degrade in ways the user told us not to.

---

## Troubleshooting

**`! gifski [optional]  dyld[…]: Library not loaded: …libx265.…dylib`**
gifski's Homebrew bottle is linked through ffmpeg's `libavcodec`, which
in turn links against `libx265`. When x265 bumps its ABI version
(common in fast-moving builds), the old filename disappears and gifski
can't load. Fix:

```bash
brew reinstall ffmpeg
# or, if you use a custom ffmpeg tap:
brew reinstall homebrew-ffmpeg/ffmpeg/ffmpeg
```

Then re-run `python3 skills/compress/scripts/compress_gif.py --check`.

**`✗ gifsicle [required]`** — required dependency. Install per the
table above and re-run `--check`.

**Both backends best-effort (`△`)** — your input is fundamentally too
large for the target at its native dimensions and frame rate. Either
raise the target with `--target-kb N` or accept the best-effort
output.

---

## Acknowledgments

This plugin is a thin orchestrator over two excellent open-source
encoders. All real work is theirs:

- [`gifsicle`](https://www.lcdf.org/gifsicle/) — Eddie Kohler
- [`gifski`](https://github.com/ImageOptim/gifski) — Kornel Lesiński
- [`pngquant`](https://pngquant.org/) / `libimagequant` — the palette
  quantization that powers gifski's quality, also by Kornel Lesiński

---

## License

[MIT](./LICENSE)
