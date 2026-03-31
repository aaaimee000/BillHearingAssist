"""
Transcript Generator Plugin
============================
Downloads audio from a YouTube hearing link and transcribes it using Whisper.
"""

import subprocess
import os
from pathlib import Path
from .base import BasePlugin


class TranscriptPlugin(BasePlugin):
    name = "transcript"
    description = "Transcribes a YouTube hearing video into text"

    def input_schema(self):
        return {
            "youtube_url": "string — full YouTube URL of the hearing",
            "bill_id": "string — used to name the output file",
        }

    async def run(self, inputs: dict) -> dict:
        youtube_url = inputs.get("youtube_url", "").strip()
        bill_id = inputs.get("bill_id", "unknown").strip()

        if not youtube_url:
            return {"error": "youtube_url is required", "transcript": "", "transcript_path": ""}

        if "youtube.com" not in youtube_url and "youtu.be" not in youtube_url:
            return {"error": "URL does not look like a YouTube link", "transcript": "", "transcript_path": ""}

        output_dir = Path(f"storage/audio/{bill_id}")
        output_dir.mkdir(parents=True, exist_ok=True)
        audio_path = str(output_dir / "hearing.mp3")
        transcript_dir = Path(f"storage/transcripts/{bill_id}")
        transcript_dir.mkdir(parents=True, exist_ok=True)
        transcript_path = str(transcript_dir / "hearing.txt")

        # Step 1: Download audio only using yt-dlp
        try:
            result = subprocess.run(
                ["yt-dlp", "-x", "--audio-format", "mp3", "-o", audio_path, youtube_url],
                capture_output=True, text=True, timeout=300,
            )
            if result.returncode != 0:
                return {
                    "error": f"yt-dlp failed: {result.stderr}",
                    "transcript": "",
                    "transcript_path": "",
                }
        except FileNotFoundError:
            return {
                "error": "yt-dlp not installed. Run: pip install yt-dlp",
                "transcript": "",
                "transcript_path": "",
            }
        except subprocess.TimeoutExpired:
            return {"error": "Audio download timed out (>5 min)", "transcript": "", "transcript_path": ""}

        # Step 2: Transcribe with Whisper
        try:
            import whisper
            # "base" is fast, "medium" is more accurate for legislative content
            model_size = os.getenv("WHISPER_MODEL", "base")
            model = whisper.load_model(model_size)
            result = model.transcribe(audio_path)
            transcript_text = result["text"]
            Path(transcript_path).write_text(transcript_text, encoding="utf-8")
            return {
                "transcript": transcript_text,
                "transcript_path": transcript_path,
                "word_count": len(transcript_text.split()),
            }
        except ImportError:
            return {
                "error": "Whisper not installed. Run: pip install openai-whisper",
                "transcript": "",
                "transcript_path": "",
            }
        except Exception as e:
            return {"error": f"Transcription failed: {str(e)}", "transcript": "", "transcript_path": ""}