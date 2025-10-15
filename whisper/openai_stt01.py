from openai import OpenAI
from dotenv import load_dotenv
from prompt import prompt

load_dotenv()

client = OpenAI()
audio_file = open(
    "/home/anujkumar/work/Speech-To-Text-Models/whisper/da5b-cd90-478d-8cd5-3b2e700d4aaf.mp3",
    "rb",
)

transcription = client.audio.transcriptions.create(
    model="gpt-4o-transcribe",
    file=audio_file,
    # language=["hi", "en", "gu"],
    prompt=prompt,
)

print(transcription.text)
