import json
import os
import threading
import time
import argparse
import pyaudio
from typing import Optional
from websockets.sync.client import connect
from websockets.exceptions import ConnectionClosedOK

# Soniox WebSocket endpoint
SONIOX_WEBSOCKET_URL = "wss://stt-rt.soniox.com/transcribe-websocket"

# Audio recording parameters (Soniox recommends 16kHz, 16-bit PCM)
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 16000
# Buffer size (in bytes). Soniox suggests 3840 bytes for 120ms chunks at 16kHz.
# 16-bit = 2 bytes per sample. So 120ms = 0.120 * 16000 = 1920 samples. 1920 * 2 = 3840 bytes.
CHUNK = 3840 // 2 // 1 # Calculate number of frames per chunk

# Get Soniox STT config.
def get_config(api_key: str, audio_format: str, translation: str) -> dict:
    config = {
        # Get your API key at console.soniox.com, then run: export SONIOX_API_KEY=<YOUR_API_KEY>
        "api_key": api_key,
        #
        # Select the model to use.
        # See: soniox.com/docs/stt/models
        "model": "stt-rt-preview",
        #
        # Set language hints when possible to significantly improve accuracy.
        # See: soniox.com/docs/stt/concepts/language-hints
        "language_hints": ["en", "es", "hi"],
        #
        # Enable language identification. Each token will include a "language" field.
        # See: soniox.com/docs/stt/concepts/language-identification
        "enable_language_identification": True,
        #
        # Enable speaker diarization. Each token will include a "speaker" field.
        # See: soniox.com/docs/stt/concepts/speaker-diarization
        "enable_speaker_diarization": True,
        #
        # Set context to improve recognition of difficult and rare words.
        # Context is a string and can include words, phrases, sentences, or summaries (limit: 10K chars).
        # See: soniox.com/docs/stt/concepts/context
        "context": """
            Celebrex, Zyrtec, Xanax, Prilosec, Amoxicillin Clavulanate Potassium            
            The customer, Maria Lopez, contacted BrightWay Insurance to update her auto policy 
            after purchasing a new vehicle.
        """,
        #
        # Use endpointing to detect when the speaker stops.
        # It finalizes all non-final tokens right away, minimizing latency.
        # See: soniox.com/docs/stt/rt/endpoint-detection
        "enable_endpoint_detection": True,
    }

    # Audio format.
    # See: soniox.com/docs/stt/rt/real-time-transcription#audio-formats
    if audio_format == "auto":
        # Set to "auto" to let Soniox detect the audio format automatically.
        config["audio_format"] = "auto"
    elif audio_format == "pcm_s16le":
        # Example of a raw audio format; Soniox supports many others as well.
        config["audio_format"] = "pcm_s16le"
        config["sample_rate"] = 16000
        config["num_channels"] = 1
    else:
        raise ValueError(f"Unsupported audio_format: {audio_format}")

    # Translation options.
    # See: soniox.com/docs/stt/rt/real-time-translation#translation-modes
    if translation == "none":
        pass
    elif translation == "one_way":
        # Translates all languages into the target language.
        config["translation"] = {
            "type": "one_way",
            "target_language": "es",
        }
    elif translation == "two_way":
        # Translates from language_a to language_b and back from language_b to language_a.
        config["translation"] = {
            "type": "two_way",
            "language_a": "en",
            "language_b": "es",
        }
    else:
        raise ValueError(f"Unsupported translation: {translation}")

    return config


# Capture audio from the microphone and send its bytes to the websocket.
def stream_audio_from_mic(ws, stop_event) -> None:
    audio = pyaudio.PyAudio()
    
    try:
        # Open microphone stream
        stream = audio.open(
            format=FORMAT,
            channels=CHANNELS,
            rate=RATE,
            input=True,
            frames_per_buffer=CHUNK,
        )
        
        print("Microphone opened. Start speaking...")

        while not stop_event.is_set():
            # Read audio data from the microphone
            data = stream.read(CHUNK, exception_on_overflow=False)
            # Send the audio data to the Soniox WebSocket server
            ws.send(data)
        
        print("Stopping microphone stream...")
        
        # Stop and close the stream
        stream.stop_stream()
        stream.close()
        
    finally:
        audio.terminate()
        # Send an empty frame to signal end-of-audio to the server
        try:
            ws.send(b"") # Send empty binary frame
        except Exception as e:
            print(f"Error sending end frame: {e}")


# Convert tokens into a readable transcript.
def render_tokens(final_tokens: list[dict], non_final_tokens: list[dict]) -> str:
    text_parts: list[str] = []
    current_speaker: Optional[str] = None
    current_language: Optional[str] = None

    # Process all tokens in order.
    for token in final_tokens + non_final_tokens:
        text = token["text"]
        speaker = token.get("speaker")
        language = token.get("language")
        is_translation = token.get("translation_status") == "translation"

        # Speaker changed -> add a speaker tag.
        if speaker is not None and speaker != current_speaker:
            if current_speaker is not None:
                text_parts.append("\n\n")
            current_speaker = speaker
            current_language = None  # Reset language on speaker changes.
            text_parts.append(f"Speaker {current_speaker}:")

        # Language changed -> add a language or translation tag.
        if language is not None and language != current_language:
            current_language = language
            prefix = "[Translation] " if is_translation else ""
            text_parts.append(f"\n{prefix}[{current_language}] ")
            text = text.lstrip()

        text_parts.append(text)

    text_parts.append("\n===============================")

    return "".join(text_parts)


def run_session(
    api_key: str,
    audio_format: str,
    translation: str,
) -> None:
    config = get_config(api_key, audio_format, translation)

    print("Connecting to Soniox...")
    try:
        with connect(SONIOX_WEBSOCKET_URL) as ws:
            # Send first request with config.
            ws.send(json.dumps(config))

            # Create a stop event for the audio thread
            stop_event = threading.Event()

            # Start streaming audio from the microphone in the background.
            audio_thread = threading.Thread(
                target=stream_audio_from_mic,
                args=(ws, stop_event),
                daemon=True, # Thread will die when main program exits
            )
            audio_thread.start()

            print("Session started. Press Ctrl+C to stop.")
            final_tokens: list[dict] = []

            try:
                while True:
                    message = ws.recv()
                    res = json.loads(message)

                    # Error from server.
                    # See: https://soniox.com/docs/stt/api-reference/websocket-api#error-response
                    if res.get("error_code") is not None:
                        print(f"Error: {res['error_code']} - {res['error_message']}")
                        break

                    # Parse tokens from current response.
                    non_final_tokens: list[dict] = []
                    for token in res.get("tokens", []):
                        if token.get("text"):
                            if token.get("is_final"):
                                # Final tokens are returned once and should be appended to final_tokens.
                                final_tokens.append(token)
                            else:
                                # Non-final tokens update as more audio arrives; reset them on every response.
                                non_final_tokens.append(token)

                    # Render tokens.
                    text = render_tokens(final_tokens, non_final_tokens)
                    # Print without adding extra newlines from render_tokens
                    # We only print the last part for cleaner output
                    print(text.split('\n===============================')[0], end='\r') # Use \r to overwrite line

                    # Session finished.
                    if res.get("finished"):
                        print("\nSession finished by server.")
                        break

            except KeyboardInterrupt:
                print("\nInterrupted by user. Stopping...")
                stop_event.set()
            except ConnectionClosedOK:
                # Normal, server closed after finished.
                print("Connection closed by server.")
            except Exception as e:
                print(f"Error: {e}")
                stop_event.set()

            # Wait for the audio thread to finish gracefully
            audio_thread.join(timeout=2.0) # Wait up to 2 seconds
            if audio_thread.is_alive():
                print("Audio thread did not stop gracefully.")
                
    except Exception as e:
        print(f"Failed to connect: {e}")


def main():
    parser = argparse.ArgumentParser()
    # Removed --audio_path argument since we are using the microphone
    parser.add_argument("--audio_format", default="pcm_s16le") # Changed default to explicit format
    parser.add_argument("--translation", default="none")
    args = parser.parse_args()

    api_key = os.environ.get("SONIOX_API_KEY")
    if api_key is None:
        raise RuntimeError("Missing SONIOX_API_KEY.")

    # Ensure the format is compatible with microphone streaming
    if args.audio_format not in ["pcm_s16le", "auto"]:
        print(f"Warning: Using microphone with format '{args.audio_format}' might require specific configuration.")
        
    run_session(api_key, args.audio_format, args.translation)


if __name__ == "__main__":
    main()
