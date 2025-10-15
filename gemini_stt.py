from google import genai
from whisper.prompt import prompt
from dotenv import load_dotenv
import os

load_dotenv()

client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))

myfile = client.files.upload(
    file="/home/anujkumar/work/Speech-To-Text-Models/whisper/da5b-cd90-478d-8cd5-3b2e700d4aaf.mp3"
)

response = client.models.generate_content(
    model="gemini-2.5-flash", contents=[prompt, myfile]
)

print(response.text)
