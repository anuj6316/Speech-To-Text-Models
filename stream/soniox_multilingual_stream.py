
import json
import os
import threading
import time
import argparse
import pyaudio
import pandas as pd
import re
from typing import Optional
from websockets.sync.client import connect
from websockets.exceptions import ConnectionClosedOK

# Soniox WebSocket endpoint
SONIOX_WEBSOCKET_URL = "wss://stt-rt.soniox.com/transcribe-websocket"

# Audio recording parameters
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 16000
CHUNK = 3840 // 2 // 1  # 120ms of audio

def get_config(api_key: str, audio_format: str) -> dict:
    """Get Soniox STT config."""
    config = {
        "api_key": api_key,
        "model": "stt-rt-preview",
        "language_hints": ["en", "hi", "gu"],
        "enable_language_identification": True,
        "enable_speaker_diarization": True,
        "enable_endpoint_detection": True,
    }
    if audio_format == "auto":
        config["audio_format"] = "auto"
    elif audio_format == "pcm_s16le":
        config["audio_format"] = "pcm_s16le"
        config["sample_rate"] = RATE
        config["num_channels"] = CHANNELS
    else:
        raise ValueError(f"Unsupported audio_format: {audio_format}")
    return config

def load_dictionary(filename: str) -> dict:
    """Load dictionary from CSV file with fallback to sample data."""
    try:
        if os.path.exists(filename):
            df = pd.read_csv(filename)
            return dict(zip(df.iloc[:, 0], df.iloc[:, 1]))
        else:
            print(f"Warning: Dictionary file '{filename}' not found. Using sample data.")
            sample_data = {
                'hi_data_cleaned.csv': {
                    'dosto': 'दोस्तों', 'aaj': 'आज', 'mausam': 'मौसम', 'accha': 'अच्छा',
                    'kal': 'कल', 'kya': 'क्या', 'tum': 'तुम', 'office': 'ऑफिस', 'rahe': 'रहे', 'ho': 'हो',
                    'namaste': 'नमस्ते', 'dhanyawad': 'धन्यवाद', 'main': 'मैं', 'tha': 'था', 'thi': 'थी'
                },
                'gu_data_cleaned.csv': {
                    'kem': 'કેમ', 'cho': 'છો', 'che': 'છે', 'tame': 'તમે', 'kyā': 'ક્યાં',
                    'jaī': 'જઈ', 'rahyā': 'રહ્યા', 'baje': 'બજે', 'hu': 'હું', 'chhu': 'છું',
                    'chhe': 'છે', 'aavu': 'આવું', 'padharo': 'પધારો'
                }
            }
            return sample_data.get(filename, {})
    except Exception as e:
        print(f"Error loading dictionary {filename}: {e}")
        return {}

def map_languages(text: str, hindi_dict: dict, gujarati_dict: dict) -> str:
    """Map Romanized words to native scripts."""
    words = re.findall(r"[\w']+|[.,!?;]", text)
    result = []
    for word in words:
        if re.match(r"[.,!?;]", word):
            result.append(word)
            continue
        
        lower_word = word.lower()
        if lower_word in hindi_dict:
            result.append(hindi_dict[lower_word])
        elif lower_word in gujarati_dict:
            result.append(gujarati_dict[lower_word])
        else:
            result.append(word)
    return " ".join(result)

def stream_audio_from_mic(ws, stop_event: threading.Event) -> None:
    """Capture audio from the microphone and send its bytes to the websocket."""
    audio = pyaudio.PyAudio()
    try:
        stream = audio.open(
            format=FORMAT,
            channels=CHANNELS,
            rate=RATE,
            input=True,
            frames_per_buffer=CHUNK,
        )
        print("Microphone opened. Start speaking...")
        while not stop_event.is_set():
            data = stream.read(CHUNK, exception_on_overflow=False)
            ws.send(data)
        print("Stopping microphone stream...")
        stream.stop_stream()
        stream.close()
    finally:
        audio.terminate()
        try:
            ws.send(b"")  # Send empty binary frame to signal end of audio
        except Exception as e:
            print(f"Error sending end frame: {e}")

def run_session(api_key: str, audio_format: str) -> None:
    """Load dictionaries and run the real-time transcription session."""
    print("Loading dictionaries...")
    hindi_dict = load_dictionary('hi_data_cleaned.csv')
    gujarati_dict = load_dictionary('gu_data_cleaned.csv')
    print("Dictionaries loaded.")

    config = get_config(api_key, audio_format)

    print("Connecting to Soniox...")
    try:
        with connect(SONIOX_WEBSOCKET_URL) as ws:
            ws.send(json.dumps(config))

            stop_event = threading.Event()
            audio_thread = threading.Thread(
                target=stream_audio_from_mic,
                args=(ws, stop_event),
                daemon=True,
            )
            audio_thread.start()

            print("Session started. Press Ctrl+C to stop.")
            
            try:
                while True:
                    message = ws.recv()
                    res = json.loads(message)

                    if res.get("error_code") is not None:
                        print(f"Error: {res['error_code']} - {res['error_message']}")
                        break

                    all_tokens = res.get("tokens", [])
                    final_tokens = [t for t in all_tokens if t.get("is_final")]
                    non_final_tokens = [t for t in all_tokens if not t.get("is_final")]

                    final_text = "".join(t["text"] for t in final_tokens)
                    non_final_text = "".join(t["text"] for t in non_final_tokens)

                    mapped_final_text = map_languages(final_text, hindi_dict, gujarati_dict)
                    mapped_non_final_text = map_languages(non_final_text, hindi_dict, gujarati_dict)

                    # Overwrite the current line with the latest transcription
                    print(f"{mapped_final_text}{mapped_non_final_text}" + " " * 20, end='\r')

                    if res.get("finished"):
                        print("\nSession finished by server.")
                        break

            except KeyboardInterrupt:
                print("\nInterrupted by user. Stopping...")
            except ConnectionClosedOK:
                print("\nConnection closed normally.")
            except Exception as e:
                print(f"Error: {e}")
            finally:
                stop_event.set()
                audio_thread.join(timeout=2.0)

    except Exception as e:
        print(f"Failed to connect: {e}")

def main():
    parser = argparse.ArgumentParser(description="Real-time multilingual transcription using Soniox.")
    parser.add_argument("--audio_format", default="pcm_s16le", help="Audio format for microphone.")
    args = parser.parse_args()

    api_key = os.environ.get("SONIOX_API_KEY")
    if api_key is None:
        raise RuntimeError("Missing SONIOX_API_KEY. Please set it as an environment variable.")

    if args.audio_format not in ["pcm_s16le", "auto"]:
        print(f"Warning: Using microphone with format '{args.audio_format}' might require specific configuration.")

    run_session(api_key, args.audio_format)

if __name__ == "__main__":
    main()
