"""CLI: python -m youtube_extractor <url> [options]"""

from __future__ import annotations

import argparse
from pathlib import Path

from .pipeline import run_pipeline


def main() -> None:
    p = argparse.ArgumentParser(
        prog="youtube_extractor",
        description="Extract transcript + frames from a YouTube video and "
                    "convert into backtestable strategy rules via an LLM.",
    )
    p.add_argument("url", help="YouTube URL or 11-char video ID")
    p.add_argument("--out", type=Path, default=Path("./yt_out"),
                   help="Root output directory (default: ./yt_out)")
    p.add_argument("--interval", type=float, default=30.0,
                   help="Fixed-interval frame sampling, seconds (default: 30)")
    p.add_argument("--scene-threshold", type=float, default=0.55,
                   help="Scene-change sensitivity 0-1 (default: 0.55)")
    p.add_argument("--bucket", type=float, default=30.0,
                   help="Timeline bucket size, seconds (default: 30)")
    p.add_argument("--max-tokens", type=int, default=6000,
                   help="Max tokens per LLM chunk (default: 6000)")
    p.add_argument("--no-fillers-removal", action="store_true",
                   help="Keep filler words in transcript")
    p.add_argument("--no-video", action="store_true",
                   help="Skip video download / frame extraction (transcript-only)")
    p.add_argument("--backend", choices=["ollama", "claude"], default="ollama")
    p.add_argument("--ollama-model", default="llama3.1")
    p.add_argument("--claude-model", default="claude-opus-4-7")
    p.add_argument("--send-images", action="store_true",
                   help="Send frame images to the LLM (Claude only)")
    p.add_argument("--whisper-model", default="base",
                   help="Whisper model used as final transcript fallback")
    p.add_argument("--force-whisper", action="store_true",
                   help="Skip YouTube captions and transcribe audio with Whisper "
                        "directly. Use for jargon-heavy videos (trading, medical) "
                        "where auto-captions garble specialised terms.")
    args = p.parse_args()

    result = run_pipeline(
        args.url,
        out_root=args.out,
        interval_sec=args.interval,
        scene_threshold=args.scene_threshold,
        bucket_sec=args.bucket,
        max_tokens_per_chunk=args.max_tokens,
        drop_fillers=not args.no_fillers_removal,
        extract_video=not args.no_video,
        backend=args.backend,
        ollama_model=args.ollama_model,
        claude_model=args.claude_model,
        send_images=args.send_images,
        whisper_model=args.whisper_model,
        force_whisper=args.force_whisper,
    )

    print()
    print("─── Summary ───────────────────────────────────────────")
    print(f"  Video ID         : {result.video_id}")
    print(f"  Output dir       : {result.output_dir}")
    print(f"  Transcript segs  : {result.n_transcript_segments}")
    print(f"  Frames           : {result.n_frames}")
    print(f"  LLM chunks       : {result.n_chunks}")
    print(f"  Rules extracted  : {len(result.strategy.rules)}")
    print(f"  Unclear notes    : {len(result.strategy.unclear)}")
    print(f"  Strategy file    : {result.strategy_path}")


if __name__ == "__main__":
    main()
