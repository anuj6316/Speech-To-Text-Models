import json
import os
import threading
import time
import argparse
import pyaudio
from typing import Optional
from websockets.sync.client import connect
from websockets.exceptions import ConnectionClosedOK
import gradio as gr

# Soniox WebSocket endpoint
SONIOX_WEBSOCKET_URL = "wss://stt-rt.soniox.com/transcribe-websocket"

# Audio recording parameters
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 16000
CHUNK = 3840 // 2 // 1


class TranscriptionSession:
    def __init__(
        self, api_key: str, audio_format: str = "pcm_s16le", translation: str = "none"
    ):
        self.api_key = api_key
        self.audio_format = audio_format
        self.translation = translation
        self.ws = None
        self.stop_event = threading.Event()
        self.audio_thread = None
        self.receive_thread = None
        self.final_tokens = []
        self.is_running = False
        self.current_transcript = ""

    def get_config(self) -> dict:
        config = {
            "api_key": self.api_key,
            "model": "stt-rt-preview",
            "language_hints": ["en", "es"],
            "enable_language_identification": True,
            "enable_speaker_diarization": True,
            "context": """
                Celebrex, Zyrtec, Xanax, Prilosec, Amoxicillin Clavulanate Potassium            
                The customer, Maria Lopez, contacted BrightWay Insurance to update her auto policy 
                after purchasing a new vehicle.
            """,
            "enable_endpoint_detection": True,
        }

        if self.audio_format == "auto":
            config["audio_format"] = "auto"
        elif self.audio_format == "pcm_s16le":
            config["audio_format"] = "pcm_s16le"
            config["sample_rate"] = 16000
            config["num_channels"] = 1

        if self.translation == "one_way":
            config["translation"] = {
                "type": "one_way",
                "target_language": "es",
            }
        elif self.translation == "two_way":
            config["translation"] = {
                "type": "two_way",
                "language_a": "en",
                "language_b": "es",
            }

        return config

    def stream_audio_from_mic(self) -> None:
        audio = pyaudio.PyAudio()

        try:
            stream = audio.open(
                format=FORMAT,
                channels=CHANNELS,
                rate=RATE,
                input=True,
                frames_per_buffer=CHUNK,
            )

            while not self.stop_event.is_set():
                data = stream.read(CHUNK, exception_on_overflow=False)
                if self.ws:
                    self.ws.send(data)

            stream.stop_stream()
            stream.close()

        finally:
            audio.terminate()
            try:
                if self.ws:
                    self.ws.send(b"")
            except Exception as e:
                print(f"Error sending end frame: {e}")

    def render_tokens(
        self, final_tokens: list[dict], non_final_tokens: list[dict]
    ) -> str:
        text_parts = []
        current_speaker = None
        current_language = None

        for token in final_tokens + non_final_tokens:
            text = token["text"]
            speaker = token.get("speaker")
            language = token.get("language")
            is_translation = token.get("translation_status") == "translation"

            if speaker is not None and speaker != current_speaker:
                if current_speaker is not None:
                    text_parts.append("\n\n")
                current_speaker = speaker
                current_language = None
                text_parts.append(f"Speaker {current_speaker}:")

            if language is not None and language != current_language:
                current_language = language
                prefix = "[Translation] " if is_translation else ""
                text_parts.append(f"\n{prefix}[{current_language}] ")
                text = text.lstrip()

            text_parts.append(text)

        return "".join(text_parts)

    def receive_messages(self):
        try:
            while not self.stop_event.is_set():
                message = self.ws.recv()
                res = json.loads(message)

                if res.get("error_code") is not None:
                    self.current_transcript = (
                        f"Error: {res['error_code']} - {res['error_message']}"
                    )
                    break

                non_final_tokens = []
                for token in res.get("tokens", []):
                    if token.get("text"):
                        if token.get("is_final"):
                            self.final_tokens.append(token)
                        else:
                            non_final_tokens.append(token)

                self.current_transcript = self.render_tokens(
                    self.final_tokens, non_final_tokens
                )

                if res.get("finished"):
                    break

        except ConnectionClosedOK:
            pass
        except Exception as e:
            if not self.stop_event.is_set():
                self.current_transcript += f"\n\nError: {e}"

    def start(self):
        if self.is_running:
            return "Session already running"

        try:
            self.ws = connect(SONIOX_WEBSOCKET_URL)
            config = self.get_config()
            self.ws.send(json.dumps(config))

            self.stop_event.clear()
            self.final_tokens = []
            self.current_transcript = "🎤 Listening... Start speaking!\n\n"
            self.is_running = True

            self.audio_thread = threading.Thread(
                target=self.stream_audio_from_mic,
                daemon=True,
            )
            self.audio_thread.start()

            self.receive_thread = threading.Thread(
                target=self.receive_messages,
                daemon=True,
            )
            self.receive_thread.start()

            return "✅ Recording started"
        except Exception as e:
            self.is_running = False
            return f"❌ Failed to start: {e}"

    def stop(self):
        if not self.is_running:
            return "No active session"

        self.stop_event.set()
        self.is_running = False

        if self.audio_thread:
            self.audio_thread.join(timeout=2.0)
        if self.receive_thread:
            self.receive_thread.join(timeout=2.0)

        if self.ws:
            self.ws.close()
            self.ws = None

        return "⏹️ Recording stopped"

    def get_transcript(self):
        return self.current_transcript


# Global session object
session = None


def initialize_session():
    global session
    api_key = os.environ.get("SONIOX_API_KEY")
    if api_key is None:
        return "❌ SONIOX_API_KEY environment variable not set"
    session = TranscriptionSession(api_key)
    return "✅ Ready to start transcription"


def start_recording():
    if session is None:
        return "❌ Session not initialized", ""
    status = session.start()
    return status, session.get_transcript()


def stop_recording():
    if session is None:
        return "❌ Session not initialized", ""
    status = session.stop()
    return status, session.get_transcript()


def update_transcript():
    if session is None or not session.is_running:
        return session.get_transcript() if session else ""
    return session.get_transcript()


# Create Gradio interface
with gr.Blocks(title="Soniox Real-time Transcription", theme=gr.themes.Soft()) as demo:
    gr.Markdown(
        """
        # 🎙️ Soniox Real-time Transcription
        
        Live speech-to-text transcription with speaker diarization and language identification.
        
        **Instructions:**
        1. Make sure `SONIOX_API_KEY` environment variable is set
        2. Click **Start Recording** to begin
        3. Speak into your microphone
        4. Click **Stop Recording** when done
        """
    )

    with gr.Row():
        with gr.Column(scale=1):
            status_box = gr.Textbox(
                label="Status",
                value="Click 'Initialize' to start",
                interactive=False,
                lines=2,
            )

            with gr.Row():
                init_btn = gr.Button("🔧 Initialize", variant="secondary")
                start_btn = gr.Button("🎤 Start Recording", variant="primary")
                stop_btn = gr.Button("⏹️ Stop Recording", variant="stop")

            gr.Markdown(
                """
                ### Features Enabled:
                - ✅ Speaker Diarization
                - ✅ Language Identification (EN/ES)
                - ✅ Endpoint Detection
                - ✅ Medical/Insurance Context
                """
            )

        with gr.Column(scale=2):
            transcript_box = gr.Textbox(
                label="Live Transcript",
                value="",
                interactive=False,
                lines=20,
                max_lines=30,
            )

    # Event handlers
    init_btn.click(fn=initialize_session, outputs=status_box)

    start_btn.click(fn=start_recording, outputs=[status_box, transcript_box])

    stop_btn.click(fn=stop_recording, outputs=[status_box, transcript_box])

    # Auto-update transcript every 500ms while recording
    # Using Timer for Gradio 4.x+
    try:
        timer = gr.Timer(0.5)
        timer.tick(fn=update_transcript, outputs=transcript_box)
    except (AttributeError, TypeError):
        # Fallback for older Gradio versions
        pass

if __name__ == "__main__":
    demo.launch(share=True, server_name="0.0.0.0", server_port=7860)
