import speech_recognition as sr
import pandas as pd
import threading
import re
import time
import os

class TerminalMultilingualASR:
    def __init__(self):
        # Initialize speech recognition
        self.recognizer = sr.Recognizer()
        self.microphone = sr.Microphone()
        
        # Load dictionaries
        self.hindi_dict = self.load_dictionary('hi_data_cleaned.csv')
        self.gujarati_dict = self.load_dictionary('gu_data_cleaned.csv')
        
        # Control variables
        self.is_recording = False
        
        print("Multilingual Speech Recognition System")
        print("Supporting English, Hindi, and Gujarati")
        print("Press Ctrl+C to stop recording\n")
    
    def load_dictionary(self, filename):
        """Load dictionary from CSV file with fallback to sample data"""
        try:
            if os.path.exists(filename):
                df = pd.read_csv(filename)
                return dict(zip(df.iloc[:, 0], df.iloc[:, 1]))
            else:
                # Create sample dictionary if file doesn't exist
                sample_data = {
                    'hi_data_cleaned.csv': {
                        'dosto': 'दोस्तों', 'aaj': 'आज', 'mausam': 'मौसम', 'accha': 'अच्छा',
                        'kal': 'कल', 'kya': 'क्या', 'tum': 'तुम', 'office': 'ऑफिस', 'rahe': 'रहे', 'ho': 'हो',
                        'namaste': 'नमस्ते', 'dhanyawad': 'धन्यवाद', 'main': 'मैं', 'tha': 'था', 'thi': 'थी'
                    },
                    'gu_data_cleaned.csv': {
                        'kem': 'કેમ', 'cho': 'છો', 'che': 'છે', 'tame': 'તમે', 'kyā': 'ક્યાં', 
                        'jaī': 'જઈ', 'rahyā': 'રહ્યા', 'baje': 'બજે', 'hu': 'હું', 'chhu': 'છું',
                        'kem': 'કેમ', 'chhe': 'છે', 'aavu': 'આવું', 'padharo': 'પધારો'
                    }
                }
                return sample_data.get(filename, {})
        except Exception as e:
            print(f"Error loading dictionary {filename}: {e}")
            return {}
    
    def map_languages(self, text):
        """Map Romanized words to native scripts"""
        words = re.findall(r"[\w']+|[.,!?;]", text)
        result = []
        
        for word in words:
            # Skip punctuation
            if re.match(r"[.,!?;]", word):
                result.append(word)
                continue
                
            # Check in Hindi dictionary
            if word.lower() in self.hindi_dict:
                result.append(self.hindi_dict[word.lower()])
            # Check in Gujarati dictionary
            elif word.lower() in self.gujarati_dict:
                result.append(self.gujarati_dict[word.lower()])
            else:
                result.append(word)
        
        return " ".join(result)
    
    def start_recording(self):
        """Start recording and processing audio"""
        with self.microphone as source:
            self.recognizer.adjust_for_ambient_noise(source, duration=1)
            print("Calibrated for ambient noise. Start speaking...")
        
        self.is_recording = True
        
        try:
            while self.is_recording:
                print("\nListening...")
                with self.microphone as source:
                    try:
                        # Listen for audio with timeout
                        audio = self.recognizer.listen(source, timeout=1, phrase_time_limit=5)
                        
                        # Recognize speech using Google Web Speech API
                        print("Recognizing...")
                        text = self.recognizer.recognize_google(audio, language="en-IN")
                        
                        # Process text for language mapping
                        processed_text = self.map_languages(text)
                        
                        # Output to terminal
                        print(f"Original: {text}")
                        print(f"Mapped:   {processed_text}")
                        
                    except sr.WaitTimeoutError:
                        # No speech detected within timeout
                        continue
                    except sr.UnknownValueError:
                        print("Could not understand audio")
                    except sr.RequestError as e:
                        print(f"Error with the service: {e}")
        except KeyboardInterrupt:
            print("\nStopping recording...")
            self.is_recording = False

def main():
    asr_system = TerminalMultilingualASR()
    
    try:
        asr_system.start_recording()
    except KeyboardInterrupt:
        print("\nProgram terminated by user")

if __name__ == "__main__":
    main()