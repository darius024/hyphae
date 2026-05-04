import json
import logging
import os
import subprocess
import tempfile

from .config import WHISPER_PATH

try:
    from cactus import cactus_init, cactus_transcribe
    CACTUS_VOICE_AVAILABLE = True
except Exception as _e:
    logging.getLogger(__name__).warning("Cactus voice bindings unavailable: %s", _e)
    cactus_init = None
    cactus_transcribe = None
    CACTUS_VOICE_AVAILABLE = False

log = logging.getLogger(__name__)

WHISPER_PROMPT = "<|startoftranscript|><|en|><|transcribe|><|notimestamps|>"
RECORD_SECONDS = 5
SAMPLE_RATE = 16000

_whisper_model = None


def _get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        if not CACTUS_VOICE_AVAILABLE:
            raise RuntimeError(
                "Cactus voice bindings are not available. "
                "Ensure the cactus SDK is built and the native library is accessible."
            )
        if not os.path.isdir(WHISPER_PATH):
            raise RuntimeError(
                f"Whisper model not found at {WHISPER_PATH}\n"
                "Run: cactus download openai/whisper-small"
            )
        _whisper_model = cactus_init(WHISPER_PATH)
    return _whisper_model


def record_audio(seconds=RECORD_SECONDS):
    """Record audio from the microphone and return the path to a WAV file."""
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()

    try:
        subprocess.run(
            [
                "sox", "-d", "-r", str(SAMPLE_RATE),
                "-c", "1", "-b", "16", tmp.name,
                "trim", "0", str(seconds),
            ],
            check=True,
            capture_output=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "avfoundation", "-i", ":0",
                "-t", str(seconds), "-ar", str(SAMPLE_RATE),
                "-ac", "1", "-sample_fmt", "s16", tmp.name,
            ],
            check=True,
            capture_output=True,
        )

    return tmp.name


def transcribe_file(audio_path):
    """Transcribe an audio file and return the text."""
    model = _get_whisper_model()
    response = cactus_transcribe(model, audio_path, prompt=WHISPER_PROMPT)
    try:
        result = json.loads(response)
        return result.get("response", "").strip()
    except (json.JSONDecodeError, TypeError):
        log.warning("Whisper returned non-JSON response, using raw text")
        return str(response).strip() if response else ""


def listen_and_transcribe(seconds=RECORD_SECONDS):
    """Record from mic and return transcribed text. Full on-device pipeline."""
    audio_path = record_audio(seconds)
    try:
        text = transcribe_file(audio_path)
        return text
    finally:
        try:
            os.unlink(audio_path)
        except OSError:
            pass
