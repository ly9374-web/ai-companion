import base64
import struct
import subprocess
import tempfile
import os
import numpy as np
from loguru import logger
from ..agent.output_types import Actions
from ..agent.output_types import DisplayText


def _convert_to_wav_bytes(audio_path: str) -> bytes:
    """Convert any audio file to WAV bytes.

    If the file is already a valid WAV, reads it directly.
    Otherwise falls back to ffmpeg for conversion.
    """
    # Check if the file is already a valid WAV — skip ffmpeg if so
    try:
        with open(audio_path, "rb") as f:
            header = f.read(12)
        if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WAVE":
            # Already a WAV file, read and return directly
            with open(audio_path, "rb") as f:
                return f.read()
    except OSError:
        pass  # Fall through to ffmpeg

    # Fallback: use ffmpeg for non-WAV formats
    cmd = [
        "ffmpeg",
        "-y",               # overwrite output
        "-i", audio_path,   # input file
        "-f", "wav",        # output format
        "-acodec", "pcm_s16le",  # 16-bit PCM
        "-ar", "44100",     # sample rate
        "-ac", "1",         # mono
        "pipe:1",           # output to stdout
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, check=True)
        return result.stdout
    except subprocess.CalledProcessError as e:
        raise ValueError(
            f"Error converting audio file to WAV '{audio_path}': {e.stderr.decode() if e.stderr else str(e)}"
        )
    except FileNotFoundError:
        raise ValueError(
            "ffmpeg not found in PATH. Please install ffmpeg to enable audio playback."
        )


def _get_volume_by_chunks(wav_bytes: bytes, chunk_length_ms: int) -> list:
    """Calculate normalized volume (RMS) for each chunk of WAV audio.

    Uses pure Python + numpy instead of pydub to avoid the ffprobe dependency.
    """
    # WAV header is 44 bytes for standard PCM WAV
    if len(wav_bytes) < 44:
        raise ValueError("WAV data too short")

    # Parse WAV header to get sample rate and bits per sample
    # bytes 24-27: sample rate, bytes 34-35: bits per sample
    sample_rate = struct.unpack_from("<I", wav_bytes, 24)[0]
    bits_per_sample = struct.unpack_from("<H", wav_bytes, 34)[0]

    # Read PCM data (skip 44-byte header)
    pcm_data = wav_bytes[44:]
    samples = np.frombuffer(pcm_data, dtype=np.int16)

    # Calculate samples per chunk
    samples_per_chunk = int(sample_rate * chunk_length_ms / 1000)
    if samples_per_chunk == 0:
        raise ValueError("chunk_length_ms too small for sample rate")

    # Calculate RMS for each chunk
    num_chunks = len(samples) // samples_per_chunk
    if num_chunks == 0:
        # Short audio: treat as single chunk
        rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
        max_volume = rms if rms > 0 else 1.0
        return [rms / max_volume]

    chunks = samples[: num_chunks * samples_per_chunk].reshape(
        num_chunks, samples_per_chunk
    )
    volumes = np.sqrt(np.mean(chunks.astype(np.float64) ** 2, axis=1))

    max_volume = float(np.max(volumes))
    if max_volume == 0:
        raise ValueError("Audio is empty or all zero.")

    normalized = (volumes / max_volume).tolist()
    return [float(v) for v in normalized]


def prepare_audio_payload(
    audio_path: str | None,
    chunk_length_ms: int = 20,
    display_text: DisplayText = None,
    actions: Actions = None,
    emotion: str | None = None,
) -> dict[str, any]:
    """Prepares the audio payload for sending to the frontend.

    If audio_path is None, returns a payload with audio=None for silent display.
    Uses ffmpeg subprocess for conversion instead of pydub to avoid ffprobe dependency.

    Parameters:
        audio_path (str | None): The path to the audio file, or None for silent display
        chunk_length_ms (int): The length of each audio chunk in milliseconds
        display_text (DisplayText, optional): Text to be displayed with the audio
        actions (Actions, optional): Actions associated with the audio

    Returns:
        dict: The audio payload to be sent
    """
    if isinstance(display_text, DisplayText):
        display_text = display_text.to_dict()

    if not audio_path:
        # Return payload for silent display
        return {
            "type": "audio",
            "audio": None,
            "volumes": [],
            "slice_length": chunk_length_ms,
            "display_text": display_text,
            "actions": actions.to_dict() if actions else None,
            "emotion": emotion,
        }

    try:
        wav_bytes = _convert_to_wav_bytes(audio_path)
    except Exception as e:
        raise ValueError(
            f"Error loading or converting generated audio file to wav file '{audio_path}': {e}"
        )

    audio_base64 = base64.b64encode(wav_bytes).decode("utf-8")

    try:
        volumes = _get_volume_by_chunks(wav_bytes, chunk_length_ms)
    except Exception as e:
        logger.warning(f"Failed to calculate audio volumes: {e}")
        volumes = []

    payload = {
        "type": "audio",
        "audio": audio_base64,
        "volumes": volumes,
        "slice_length": chunk_length_ms,
        "display_text": display_text,
        "actions": actions.to_dict() if actions else None,
        "emotion": emotion,
    }

    return payload
