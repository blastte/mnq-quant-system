"""End-to-end glue: YouTube URL → compiled.json → optional backtest run.

Usage:
  python -m youtube_extractor.to_backtest "<url>"
      → writes yt_out/<id>/{strategy.json, compiled.json} and prints next steps

  python -m youtube_extractor.to_backtest "<url>" --run \
      --ltf mnq-bot/data/mnq_5m.csv
      → also invokes the existing runners/run_backtest.py with --strategy youtube
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from .utils import ensure_utf8_stdout

ensure_utf8_stdout()

from .compiler import compile_rules, write_compiled
from .pipeline import run_pipeline


def main() -> None:
    p = argparse.ArgumentParser(prog="youtube_extractor.to_backtest")
    p.add_argument("url", help="YouTube URL or 11-char video ID")
    p.add_argument("--out", type=Path, default=Path("./yt_out"))
    p.add_argument("--no-video", action="store_true",
                   help="Transcript-only (faster, no frame extraction)")
    p.add_argument("--backend", choices=["ollama", "claude"], default="ollama")
    p.add_argument("--ollama-model", default="llama3.1")
    p.add_argument("--claude-model", default="claude-opus-4-7")
    p.add_argument("--send-images", action="store_true")

    p.add_argument("--run", action="store_true",
                   help="After compiling, invoke mnq-bot/runners/run_backtest.py")
    p.add_argument("--ltf", default="mnq-bot/data/mnq_5m.csv",
                   help="LTF CSV path passed to runners/run_backtest.py")
    p.add_argument("--mnq-bot-dir", default="mnq-bot",
                   help="Path to the mnq-bot project (runners/run_backtest.py lives here)")
    args = p.parse_args()

    # 1. Extract → strategy.json
    result = run_pipeline(
        args.url,
        out_root=args.out,
        extract_video=not args.no_video,
        backend=args.backend,
        ollama_model=args.ollama_model,
        claude_model=args.claude_model,
        send_images=args.send_images,
    )

    # 2. Compile free-text rules → DSL
    strategy_json = json.loads(Path(result.strategy_path).read_text(encoding="utf-8"))
    print("\n[to_backtest] compiling rules into backtestable DSL…")
    compiled = compile_rules(
        strategy_json,
        model=args.ollama_model,
        host="http://localhost:11434",
    )

    compiled_path = Path(result.output_dir) / "compiled.json"
    write_compiled(compiled, compiled_path)
    print(f"[to_backtest] wrote {compiled_path} "
          f"({len(compiled.rules)} rules, {len(compiled.rejected)} rejected)")

    if not compiled.rules:
        print("\nNo backtestable rules survived compilation.")
        print("Common reasons: video relied on chart-pattern concepts the engine "
              "cannot evaluate (FVG, order block, liquidity sweep), or the LLM "
              "left stops/targets unspecified.")
        sys.exit(0)

    print("\nNext step:")
    print(f"  cd {args.mnq_bot_dir}")
    print(f"  python runners/run_backtest.py --strategy youtube "
          f"--rules ../{compiled_path.as_posix()} "
          f"--file {Path(args.ltf).name} --debug --ai")

    # 3. Optional: run the backtest immediately
    if args.run:
        rules_abs = compiled_path.resolve()
        ltf_path = Path(args.ltf)
        # If --ltf is given relative to the repo root, make it relative to mnq-bot/
        if not ltf_path.is_absolute() and ltf_path.parts[:1] == ("mnq-bot",):
            ltf_for_cmd = Path(*ltf_path.parts[1:]).as_posix()
        else:
            ltf_for_cmd = ltf_path.as_posix()

        cmd = [
            sys.executable, "runners/run_backtest.py",
            "--strategy", "youtube",
            "--rules", str(rules_abs),
            "--file", ltf_for_cmd,
            "--debug",
        ]
        print(f"\n[to_backtest] running: {' '.join(cmd)} (cwd={args.mnq_bot_dir})")
        subprocess.run(cmd, cwd=args.mnq_bot_dir, check=False)


if __name__ == "__main__":
    main()
