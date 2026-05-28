import os
import json
import random
import time
from threading import Thread
from queue import Queue
from gtts import gTTS
import pygame
from utils.logger import log_system, log_recognition

class TTSManager:
    def __init__(self, jokes_file, cooldown):
        self.cooldown = cooldown
        self.last_seen = {} # {name: timestamp}
        self.jokes = self.load_jokes(jokes_file)
        self.speech_queue = Queue()
        
        # Инициализиране на аудио системата
        pygame.mixer.init()
        
        # Папка за временни аудио файлове
        self.temp_dir = "data/audio_cache"
        if not os.path.exists(self.temp_dir):
            os.makedirs(self.temp_dir)

        # Стартираме фонова нишка за обработка на опашката от шеги
        Thread(target=self._worker, daemon=True).start()

    def load_jokes(self, jokes_file):
        try:
            with open(jokes_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            log_system(f"Грешка при зареждане на шеги: {e}", "error")
            return {}

    def speak_joke(self, name):
        current_time = time.time()
        if name in self.jokes:
            if name not in self.last_seen or (current_time - self.last_seen[name] > self.cooldown):
                joke = random.choice(self.jokes[name])
                self.last_seen[name] = current_time
                
                # Логване на разпознаването
                log_system(f"👤 Разпознат: {name}")
                log_recognition(name)
                
                # Добавяне в опашката за говорене
                self.speech_queue.put(joke)

    def _worker(self):
        """ Фонова нишка, която пуска шегите една след друга """
        while True:
            joke = self.speech_queue.get()
            self._generate_and_play(joke)
            self.speech_queue.task_done()

    def _generate_and_play(self, text):
        try:
            # Кешираме файла, за да не го теглим всеки път
            filename = os.path.join(self.temp_dir, f"joke_{hash(text)}.mp3")
            
            if not os.path.exists(filename):
                log_system(f"🌐 Генериране на нов аудио файл за: {text[:30]}...")
                tts = gTTS(text=text, lang='bg')
                tts.save(filename)

            # Пускане на аудиото
            pygame.mixer.music.load(filename)
            pygame.mixer.music.play()
            
            # Изчакваме края на шегата, без да блокираме камерата
            while pygame.mixer.music.get_busy():
                time.sleep(0.1)
                
        except Exception as e:
            log_system(f"Грешка при Google TTS/Pygame: {e}", "error")
