import pyttsx3
import json
import random
import time
from threading import Thread

class TTSManager:
    def __init__(self, jokes_file, cooldown):
        self.engine = pyttsx3.init()
        self.cooldown = cooldown
        self.last_seen = {} # {name: timestamp}
        self.jokes = self.load_jokes(jokes_file)

    def load_jokes(self, jokes_file):
        try:
            with open(jokes_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"Грешка при зареждане на шеги: {e}")
            return {}

    def speak_joke(self, name):
        current_time = time.time()
        if name in self.jokes:
            if name not in self.last_seen or (current_time - self.last_seen[name] > self.cooldown):
                joke = random.choice(self.jokes[name])
                self.last_seen[name] = current_time
                print(f"Казвам шега на {name}: {joke}")
                
                # Пускане в отделна нишка, за да не блокира видеото
                Thread(target=self._speak, args=(joke,)).start()

    def _speak(self, text):
        self.engine.say(text)
        self.engine.runAndWait()
