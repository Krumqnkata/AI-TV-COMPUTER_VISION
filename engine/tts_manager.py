import os
import json
import random
import time
from threading import Thread
from queue import Queue
from gtts import gTTS
import pygame
from google import genai
from utils.config import Config
from utils.logger import log_system, log_recognition

class TTSManager:
    def __init__(self, jokes_file, cooldown):
        self.cooldown = cooldown
        self.last_seen = {} # {name: timestamp}
        self.jokes = self.load_jokes(jokes_file)
        self.speech_queue = Queue()
        
        # Инициализиране на Gemini AI (Нов SDK)
        self.ai_enabled = False
        if Config.GEMINI_API_KEY:
            try:
                self.client = genai.Client(api_key=Config.GEMINI_API_KEY)
                self.model_id = "gemini-2.5-flash" # Използваме модел от вашия списък
                self.ai_enabled = True
                log_system("Gemini AI (google-genai) initialized successfully.")
            except Exception as e:
                log_system(f"Failed to initialize Gemini AI: {e}", "error")

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
            log_system(f"Error loading jokes: {e}", "error")
            return {}

    def _generate_ai_joke(self, name):
        """ Генерира шега чрез Gemini AI """
        if not self.ai_enabled:
            return None
        
        # Специален промпт, ако човекът е непознат
        if name == "Непознат":
            prompt = (
                "Напиши една ЕДИНСТВЕНА, много кратка и забавна закачка на български за непознат човек, "
                "който току-що се появи пред камерата. Можеш да се пошегуваш, че е нов тук или че изглежда мистериозно. "
                "ВАЖНО: Върни САМО текста на закачката, без въведения и обяснения. Максимум 1 изречение."
            )
        else:
            prompt = (
                f"Напиши една ЕДИНСТВЕНА, много кратка, забавна и оригинална шега или закачка на български "
                f"за човек на име {name}. Той току-що влезе в стаята и беше разпознат от камерата. "
                f"ВАЖНО: Върни САМО текста на шегата, без никакви въведения, без 'Ето варианти', "
                f"без номерация и без обяснения. Максимум 1 изречение."
            )
        
        try:
            response = self.client.models.generate_content(
                model=self.model_id,
                contents=prompt
            )
            if response and response.text:
                joke = response.text.strip()
                log_system(f"AI generated joke for {name}")
                return joke
        except Exception as e:
            log_system(f"Gemini AI error: {e}", "error")
        return None

    def speak_joke(self, name):
        current_time = time.time()
        if name not in self.last_seen or (current_time - self.last_seen[name] > self.cooldown):
            self.last_seen[name] = current_time
            
            # Логване на разпознаването
            log_system(f"Recognized: {name}")
            log_recognition(name)

            joke = None
            
            # 1. Опит за генериране с ИИ (вече и за непознати)
            if self.ai_enabled:
                joke = self._generate_ai_joke(name)
            
            # 2. Фолбек към локални шеги (само ако ИИ се провали и имаме записани шеги)
            if not joke and name in self.jokes:
                joke = random.choice(self.jokes[name])
            
            # 3. Добавяне в опашката, ако имаме шега
            if joke:
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
                # Проверка: дали имаме интернет за генериране?
                try:
                    import socket
                    socket.create_connection(("8.8.8.8", 53), timeout=2)
                    
                    log_system(f"Generating new audio for: {text[:30]}...")
                    tts = gTTS(text=text, lang='bg')
                    tts.save(filename)
                except OSError:
                    log_system("No internet! Cannot generate new joke.", "error")
                    # ТРИК: Ако няма интернет, пусни случайна стара шега от кеша
                    cached_files = [f for f in os.listdir(self.temp_dir) if f.endswith(".mp3")]
                    if cached_files:
                        filename = os.path.join(self.temp_dir, random.choice(cached_files))
                        log_system("Playing cached joke instead.")
                    else:
                        return # Няма интернет и няма кеш - просто мълчим

            # Пускане на аудиото
            pygame.mixer.music.load(filename)
            pygame.mixer.music.play()
            
            # Изчакваме края на шегата, без да блокираме камерата
            while pygame.mixer.music.get_busy():
                time.sleep(0.1)
                
        except Exception as e:
            log_system(f"Error in gTTS/Pygame: {e}", "error")
