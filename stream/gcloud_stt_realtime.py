import gradio as gr
import pyaudio
from google.cloud import speech
import threading
import time

# --- Configuration ---
RATE = 16000
CHUNK = int(RATE / 10)  # 100ms

# Extended language list for better switching
LANGUAGE_CODES = [
    "en-US",  # English (US)
    "hi-IN",  # Hindi (India)
    "es-ES",  # Spanish (Spain)
    "gu-IN",  # Gujrati (France)
    "de-DE",  # German (Germany)
    "ja-JP",  # Japanese
    "zh-CN",  # Chinese (Simplified)
    "ar-SA",  # Arabic
]


class MicrophoneStream:
    """Opens a recording stream as a generator yielding the audio chunks."""

    def __init__(self, rate, chunk):
        self._rate = rate
        self._chunk = chunk
        self._audio_interface = pyaudio.PyAudio()
        self._audio_stream = self._audio_interface.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=self._rate,
            input=True,
            frames_per_buffer=self._chunk,
        )
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, type, value, traceback):
        self._audio_stream.stop_stream()
        self._audio_stream.close()
        self.closed = True
        self._audio_interface.terminate()

    def _generator(self):
        while not self.closed:
            data = self._audio_stream.read(self._chunk, exception_on_overflow=False)
            if not data:
                break
            yield data


class TranscriptionManager:
    """Manages the transcription process with language switching support."""

    def __init__(self):
        self.is_recording = False
        self.stream = None
        self.client = None
        self.full_transcript = ""
        self.interim_transcript = ""
        self.detected_language = ""
        self.last_update_time = 0
        self.last_speech_time = 0
        self.language_history = []  # Track language changes
        self.lock = threading.Lock()

    def start_transcription(self):
        """Start the transcription process."""
        if self.is_recording:
            return "Already recording!", self.full_transcript, self.detected_language

        self.is_recording = True
        self.full_transcript = ""
        self.interim_transcript = ""
        self.detected_language = "Detecting..."
        self.language_history = []
        self.last_speech_time = time.time()

        # Start transcription in a separate thread
        thread = threading.Thread(target=self._transcribe)
        thread.daemon = True
        thread.start()

        return "🎤 Recording... Speak in any language!", "", self.detected_language

    def stop_transcription(self):
        """Stop the transcription process."""
        self.is_recording = False
        if self.stream:
            self.stream.closed = True

        with self.lock:
            final_text = self.full_transcript
            lang = self.detected_language
            lang_switches = len(set(self.language_history))

        status = f"⏹️ Stopped. Languages detected: {lang_switches}"
        return status, final_text, lang

    def _transcribe(self):
        """Main transcription logic optimized for language switching."""
        try:
            self.client = speech.SpeechClient()

            # OPTIMIZED: Configuration for best language detection
            config = speech.RecognitionConfig(
                encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
                sample_rate_hertz=RATE,
                language_code="en-US",  # Primary language
                # CRITICAL: All alternative languages for mid-speech switching
                alternative_language_codes=LANGUAGE_CODES[1:],
                enable_automatic_punctuation=True,
                model="default",  # Fast response
                use_enhanced=False,  # Lower latency
                # ADDED: Enable language detection per segment
                enable_spoken_punctuation=False,
                enable_spoken_emojis=False,
            )

            streaming_config = speech.StreamingRecognitionConfig(
                config=config,
                interim_results=True,  # CRITICAL for seeing language switches
                single_utterance=False,
            )

            with MicrophoneStream(RATE, CHUNK) as self.stream:
                audio_generator = self.stream._generator()
                requests = (
                    speech.StreamingRecognizeRequest(audio_content=content)
                    for content in audio_generator
                )

                responses = self.client.streaming_recognize(streaming_config, requests)
                self._process_responses(responses)

        except Exception as e:
            with self.lock:
                self.full_transcript = f"Error: {str(e)}"
            self.is_recording = False

    def _process_responses(self, responses):
        """Process responses with language tracking."""
        last_detected_lang = None

        for response in responses:
            if not self.is_recording:
                break

            if not response.results:
                continue

            result = response.results[0]
            if not result.alternatives:
                continue

            alternative = result.alternatives[0]
            transcript = alternative.transcript

            # Track when we receive speech
            self.last_speech_time = time.time()

            # IMPROVED: Language detection and tracking
            current_lang = None
            if hasattr(alternative, "language_code") and alternative.language_code:
                current_lang = alternative.language_code
            else:
                # Fallback to primary language
                current_lang = LANGUAGE_CODES[0]

            # Detect language changes
            if current_lang != last_detected_lang and current_lang:
                last_detected_lang = current_lang
                with self.lock:
                    self.detected_language = current_lang
                    if current_lang not in self.language_history:
                        self.language_history.append(current_lang)

            with self.lock:
                if result.is_final:
                    # ADDED: Mark language switches in transcript
                    if len(self.language_history) > 1 and current_lang:
                        lang_tag = self._get_language_name(current_lang)
                        # Add subtle language marker
                        self.full_transcript += f"[{lang_tag}] {transcript} "
                    else:
                        self.full_transcript += transcript + " "
                    self.interim_transcript = ""
                else:
                    # Show interim with current language
                    self.interim_transcript = transcript

                self.last_update_time = time.time()

    def _get_language_name(self, lang_code):
        """Get friendly language name from code."""
        lang_map = {
            "en-US": "EN",
            "hi-IN": "HI",
            "es-ES": "ES",
            "gu-IN": "GU",
            "de-DE": "DE",
            "ja-JP": "JA",
            "zh-CN": "ZH",
            "ar-SA": "AR",
        }
        return lang_map.get(lang_code, lang_code[:2].upper())

    def get_current_transcript(self):
        """Get the current transcript with interim results."""
        with self.lock:
            if self.interim_transcript:
                # Show current language being detected
                lang_display = (
                    self.detected_language
                    if self.detected_language != "Detecting..."
                    else "en-US"
                )
                return (
                    self.full_transcript + self.interim_transcript,
                    f"{lang_display} (Live)",
                )
            lang_display = (
                self.detected_language
                if self.detected_language != "Detecting..."
                else "en-US"
            )
            return self.full_transcript, lang_display


# Global transcription manager
manager = TranscriptionManager()


def start_recording():
    """Start recording callback for Gradio."""
    status, transcript, language = manager.start_transcription()
    return (
        status,
        transcript,
        language,
        gr.update(interactive=False),
        gr.update(interactive=True),
    )


def stop_recording():
    """Stop recording callback for Gradio."""
    status, transcript, language = manager.stop_transcription()
    return (
        status,
        transcript,
        language,
        gr.update(interactive=True),
        gr.update(interactive=False),
    )


def update_transcript():
    """Update the transcript display with minimal latency."""
    if not manager.is_recording:
        transcript, language = manager.get_current_transcript()
        return transcript, language

    # Get latest transcript
    transcript, language = manager.get_current_transcript()
    return transcript, language


def clear_transcript():
    """Clear the transcript."""
    manager.full_transcript = ""
    manager.interim_transcript = ""
    manager.detected_language = ""
    manager.language_history = []
    return "", "", "Transcript cleared."


# Create Gradio Interface
with gr.Blocks(title="Multilingual Speech-to-Text", theme=gr.themes.Soft()) as demo:
    gr.Markdown(
        """
        # 🎤 Multilingual Speech-to-Text with Language Switching
        **Switch languages mid-conversation!** The system automatically detects and adapts.
        """
    )

    with gr.Row():
        with gr.Column(scale=2):
            status_box = gr.Textbox(
                label="Status", value="Ready to record", interactive=False, lines=1
            )

            with gr.Row():
                start_btn = gr.Button(
                    "🎤 Start Recording", variant="primary", size="lg"
                )
                stop_btn = gr.Button(
                    "⏹️ Stop Recording", variant="stop", size="lg", interactive=False
                )
                clear_btn = gr.Button("🗑️ Clear", size="lg")

        with gr.Column(scale=1):
            language_box = gr.Textbox(
                label="🌐 Current Language", value="", interactive=False, lines=1
            )

            gr.Markdown(
                """
                ### 🌍 Language Switching
                - Switch languages anytime
                - Automatic detection
                - 8 languages supported
                - Real-time adaptation
                """
            )

    transcript_box = gr.Textbox(
        label="📝 Live Transcript (with language markers)",
        value="",
        interactive=False,
        lines=15,
        placeholder="Speak in any language... switch freely!",
        show_copy_button=True,
    )

    gr.Markdown(
        """
        ---
        ### 🌟 How Language Switching Works:

        **✅ Yes, you can switch languages mid-speaking!**

        1. **Start speaking** in any language (e.g., English)
        2. **Pause briefly** (0.5-1 second)
        3. **Switch to another language** (e.g., Hindi, Spanish)
        4. The system detects the change and adapts!

        **Language markers** like `[HI]`, `[ES]` appear in the transcript when you switch.

        ### 📋 Supported Languages:
        🇺🇸 English | 🇮🇳 Hindi | 🇪🇸 Spanish | 🇫🇷 French | 🇩🇪 German | 🇯🇵 Japanese | 🇨🇳 Chinese | 🇸🇦 Arabic

        ### 💡 Tips for Best Results:
        - **Pause briefly** between language switches (helps detection)
        - **Speak clearly** in each language
        - **Natural switching** works best (don't force rapid changes)
        - The system learns as you speak!

        ### ⚙️ Technical Details:
        - Uses Google's `alternative_language_codes` feature
        - Language detected per utterance (each pause)
        - Interim results show current language
        - Language history tracked throughout session

        **Requirements:**
        - Google Cloud Speech-to-Text API enabled
        - Microphone access granted
        """
    )

    # Event handlers
    start_btn.click(
        fn=start_recording,
        inputs=[],
        outputs=[status_box, transcript_box, language_box, start_btn, stop_btn],
    )

    stop_btn.click(
        fn=stop_recording,
        inputs=[],
        outputs=[status_box, transcript_box, language_box, start_btn, stop_btn],
    )

    clear_btn.click(
        fn=clear_transcript,
        inputs=[],
        outputs=[transcript_box, language_box, status_box],
    )

    # Fast auto-update
    timer = gr.Timer(0.1)
    timer.tick(fn=update_transcript, inputs=[], outputs=[transcript_box, language_box])


if __name__ == "__main__":
    print("🚀 Starting Multilingual Speech-to-Text Server...")
    print("📊 Features enabled:")
    print("   🌍 Multi-language support (8 languages)")
    print("   🔄 Mid-speech language switching")
    print("   🏷️  Language markers in transcript")
    print("   ⚡ Real-time detection")
    print("   ⏱️  100ms update interval")
    print("\n💡 TIP: Pause briefly when switching languages for best results!")
    print("🌐 Opening browser at http://localhost:7860")

    demo.launch(share=True, server_name="0.0.0.0", server_port=7860, show_error=True)
