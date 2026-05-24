"""End-to-end orchestrator: URL → strategy JSON."""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path

from . import frames as frames_mod
from . import structurize, transcript
from .ai_strategy import StrategyOutput, extract_strategy
from .chunker import chunk_timeline
from .utils import extract_video_id


@dataclass
class PipelineResult:
    video_id: str
    output_dir: str
    timeline_path: str
    strategy_path: str
    n_transcript_segments: int
    n_frames: int
    n_chunks: int
    strategy: StrategyOutput

    def to_dict(self) -> dict:
        d = asdict(self)
        d["strategy"] = asdict(self.strategy)
        return d


def run_pipeline(
    url: str,
    out_root: Path = Path("./yt_out"),
    *,
    interval_sec: float = 30.0,
    scene_threshold: float = 0.55,
    bucket_sec: float = 30.0,
    max_tokens_per_chunk: int = 6000,
    drop_fillers: bool = True,
    extract_video: bool = True,
    backend: str = "ollama",
    ollama_model: str = "llama3.1",
    claude_model: str = "claude-opus-4-7",
    send_images: bool = False,
    whisper_model: str = "base",
    force_whisper: bool = False,
) -> PipelineResult:
    video_id = extract_video_id(url)
    work = out_root / video_id
    frames_dir = work / "frames"
    work.mkdir(parents=True, exist_ok=True)

    # 1) Video + audio (optional — only needed for frame extraction or Whisper)
    video_path: Path | None = None
    audio_path: Path | None = None
    # If force_whisper is on, we MUST download audio even when extract_video=False
    need_audio = extract_video or force_whisper
    if need_audio:
        print(f"[pipeline] downloading video for {video_id}…")
        video_path, audio_path = frames_mod.download_video(url, work)

    # 2) Transcript
    print("[pipeline] fetching transcript…")
    segments = transcript.get_transcript(
        url,
        audio_path=audio_path,
        drop_fillers=drop_fillers,
        whisper_model=whisper_model,
        force_whisper=force_whisper,
    )
    print(f"[pipeline] {len(segments)} transcript segments")

    # 3) Frames
    frames: list[frames_mod.Frame] = []
    if extract_video and video_path is not None:
        print("[pipeline] extracting frames…")
        frames = frames_mod.extract_frames(
            video_path, frames_dir,
            interval_sec=interval_sec,
            scene_threshold=scene_threshold,
        )

    # 4) Structure
    timeline = structurize.build_timeline(
        segments, frames, bucket_sec=bucket_sec, image_root=work,
    )
    timeline_path = work / "timeline.json"
    structurize.write_timeline(timeline, timeline_path)
    print(f"[pipeline] wrote {timeline_path} ({len(timeline)} entries)")

    # 5) Chunk + LLM
    chunks = chunk_timeline(timeline, max_tokens_per_chunk=max_tokens_per_chunk)
    strategy = extract_strategy(
        chunks,
        backend=backend,
        ollama_model=ollama_model,
        claude_model=claude_model,
        send_images=send_images,
    )

    # 6) Output
    strategy_path = work / "strategy.json"
    strategy_path.write_text(
        json.dumps(asdict(strategy), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"[pipeline] wrote {strategy_path} "
          f"({len(strategy.rules)} rules, {len(strategy.unclear)} unclear)")

    return PipelineResult(
        video_id=video_id,
        output_dir=str(work),
        timeline_path=str(timeline_path),
        strategy_path=str(strategy_path),
        n_transcript_segments=len(segments),
        n_frames=len(frames),
        n_chunks=len(chunks),
        strategy=strategy,
    )
