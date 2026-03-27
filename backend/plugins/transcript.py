# Take yt-dlp to download the transcript of a YouTube video, and return the transcript as a string.
import subprocess, whisper
from .base import BasePlugin

class TranscriptPlugin(BasePlugin):
    name = "transcript" 
    async def run(self, inputs: dict) -> dict:
        youtube_url = inputs["youtube_url"]
        audio_path = "storage/audio/hearing.mp3"

        # Download audio only 
        subprocess.run([
            "yt-dlp",
            "-x",
            "--audio-format", "mp3",
            "-o", audio_path,
            youtube_url
        ], check=True)

        #Transcribe with Whisper 
        model = whisper.load_model("medium")
        result = model.transcribe(audio_path)
        transcript_path = "storage/transcripts/hearing.txt"
        with open(transcript_path, "w") as f:
            f.write(result["text"])

        return {"transcript_path": transcript_path}
