import os
import json
import random
import time
import wave
import hashlib
from threading import Thread
from queue import Queue
import pygame
from engine.llm_manager import LLMManager
from utils.config import Config
from utils.logger import log_system, log_recognition
from piper.voice import PiperVoice

class TTSManager:
    def __init__(self, jokes_file, cooldown, event_callback=None):
        self.cooldown = cooldown
        self.last_seen = {} # {name: timestamp}
        self.last_greeted = {} # {name: timestamp}
        self.GREETING_COOLDOWN = 3600 # 1 час
        self.jokes = self.load_jokes(jokes_file)
        self.speech_queue = Queue()
        self.event_callback = event_callback
        
        # Инициализиране на LLM мениджъра (Ollama + Gemini fallback)
        self.llm_manager = LLMManager()

        # Инициализиране на Piper TTS
        self.piper_enabled = False
        piper_model_path = Config.PIPER_MODEL_PATH
        piper_config_path = piper_model_path + ".json"
        
        if os.path.exists(piper_model_path) and os.path.exists(piper_config_path):
            try:
                self.piper_model = PiperVoice.load(piper_model_path, config_path=piper_config_path)
                self.piper_enabled = True
                log_system("Piper TTS initialized successfully with Bulgarian voice.")
            except Exception as e:
                log_system(f"Failed to initialize Piper TTS: {e}", "error")
        else:
            log_system("Piper TTS model or config files not found. Falling back to gTTS (if needed).", "error")

        # Инициализиране на аудио системата
        pygame.mixer.init()
        
        # Папка за временни аудио файлове
        self.temp_dir = "data/audio_cache"
        if not os.path.exists(self.temp_dir):
            os.makedirs(self.temp_dir)
            
        # Автоматично почистване на стария кеш при старт
        self._cleanup_cache(days=7)

        # Кеш система за спестяване на API заявки и токени
        self.ai_cache_file = "data/ai_jokes_cache.json"
        self.ai_jokes_cache = self._load_ai_jokes_cache()
        self.api_calls_log = []  # Лог с времеви клейма за API повиквания

        # Стартираме фонова нишка за обработка на опашката от шеги
        Thread(target=self._worker, daemon=True).start()

    def _cleanup_cache(self, days):
        """ Изтрива стари аудио файлове от кеша """
        try:
            now = time.time()
            max_age = days * 24 * 60 * 60
            count = 0
            
            for filename in os.listdir(self.temp_dir):
                file_path = os.path.join(self.temp_dir, filename)
                if os.path.isfile(file_path):
                    # Проверяваме времето на последен достъп или промяна
                    file_age = now - os.path.getmtime(file_path)
                    if file_age > max_age:
                        os.remove(file_path)
                        count += 1
            
            if count > 0:
                log_system(f"Cleanup: Removed {count} old audio files from cache.")
        except Exception as e:
            log_system(f"Failed to cleanup audio cache: {e}", "error")

    def _get_greeting(self):
        hour = time.localtime().tm_hour
        if 5 <= hour < 12:
            return "Добро утро"
        elif 12 <= hour < 18:
            return "Добър ден"
        else:
            return "Добър вечер"

    def load_jokes(self, jokes_file):
        try:
            with open(jokes_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            log_system(f"Error loading jokes: {e}", "error")
            return {}

    def _generate_ai_joke(self, name, mood="serious"):
        """ Генерира шега чрез LLM (Ollama с фолбек към Gemini) """
        # Списъци за динамично конструиране на огромен брой уникални роли и настроения
        tones = [
            "саркастичен", "супер ентусиазиран", "параноичен", "драматичен", 
            "мистериозен", "мързелив", "философски", "шеговит", "леко досаден", "поетичен"
        ]
        roles = [
            "робот", "детектив", "охранител на дискотека", "учен", "спортен коментатор", 
            "извънземен", "строг учител", "пират", "директор", "баба"
        ]

        tone = random.choice(tones)
        role = random.choice(roles)
        
        mood_context = "Човекът се усмихва, поздрави го за доброто настроение." if mood == "smiling" else "Човекът е сериозен, опитай се да го развеселиш с шега."
        style = f"Като {tone} {role}. {mood_context}"

        if name == "Непознат":
            prompt = f"Стил: {style} Пред камерата застана непознат човек."
        else:
            prompt = f"Стил: {style} Разпознат е човек с име {name}."

        system_instruction = (
            "Ти си гласов асистент за училищно AI огледало. Твоята задача е да напишеш една "
            "ЕДИНСТВЕНА, оригинална, много кратка и забавна закачка/реплика на български език, "
            "базирана на подадения стил, име и настроение. Правила: 1. Максимум едно кратко изречение. "
            "2. БЕЗ въвеждащи думи, кавички, звездички или обяснения. "
            "3. БЕЗ емоджита."
        )

        joke = self.llm_manager.generate(prompt, system_instruction)
        
        if joke:
            # Изчистване на случайни останали кавички и звездички за по-чисто изговаряне
            joke = joke.replace('"', '').replace('*', '').replace('„', '').replace('“', '') 
            log_system(f"AI generated joke for {name} (Mood: {mood})")
            return joke
        
        return None

    def speak_joke(self, name, mood="serious"):
        current_time = time.time()
        if name not in self.last_seen or (current_time - self.last_seen[name] > self.cooldown):
            self.last_seen[name] = current_time
            
            # Логване на разпознаването
            log_system(f"Recognized: {name} (Mood: {mood})")
            log_recognition(name)

            # Изчистване на опашката от стари разпознавания, ако има натрупване
            while self.speech_queue.qsize() > 1:
                try:
                    self.speech_queue.get_nowait()
                    self.speech_queue.task_done()
                except Exception:
                    pass

            # Добавяме името и настроението в опашката. 
            # Цялото мислене и генериране ще се случи във фоновата нишка.
            self.speech_queue.put((name, mood))

    def _load_ai_jokes_cache(self):
        try:
            if os.path.exists(self.ai_cache_file):
                with open(self.ai_cache_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            log_system(f"Error loading AI jokes cache: {e}", "error")
        return {}

    def _save_ai_jokes_cache(self):
        try:
            os.makedirs(os.path.dirname(self.ai_cache_file), exist_ok=True)
            with open(self.ai_cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.ai_jokes_cache, f, ensure_ascii=False, indent=4)
        except Exception as e:
            log_system(f"Error saving AI jokes cache: {e}", "error")

    def _is_api_rate_limited(self):
        current_time = time.time()
        # Почистваме лога от заявки по-стари от 1 час
        self.api_calls_log = [t for t in self.api_calls_log if current_time - t < 3600]
        
        # Лимит 1: Максимум заявки в рамките на 1 минута
        calls_last_minute = sum(1 for t in self.api_calls_log if current_time - t < 60)
        if calls_last_minute >= Config.AI_RATE_LIMIT_PER_MINUTE:
            return True
            
        # Лимит 2: Максимум 60 заявки в рамките на 1 час (предпазен буфер)
        if len(self.api_calls_log) >= 60:
            return True
            
        return False

    def _worker(self):
        """ Фонова нишка, която обработва имената, генерира шеги и ги пуска """
        while True:
            # Получаваме кортеж (name, mood)
            queue_item = self.speech_queue.get()
            if isinstance(queue_item, tuple):
                name, mood = queue_item
            else:
                name, mood = queue_item, "serious"
            
            joke = None
            cached_list = self.ai_jokes_cache.get(name, [])
            
            # Стратегия:
            # Ако AI е достъпен, винаги генерираме нова шега, за да отразим настроението
            if (self.llm_manager.ollama_enabled or self.llm_manager.gemini_enabled) and not self._is_api_rate_limited():
                joke = self._generate_ai_joke(name, mood)
                if joke:
                    # Добавяме в кеша
                    if name not in self.ai_jokes_cache:
                        self.ai_jokes_cache[name] = []
                    if joke not in self.ai_jokes_cache[name]:
                        self.ai_jokes_cache[name].append(joke)
                        self._save_ai_jokes_cache()
                    self.api_calls_log.append(time.time())

            # Фолбек към кеширана шега (ако AI е спрян или лимитиран)
            if not joke and cached_list:
                joke = random.choice(cached_list)
                log_system(f"Reusing cached AI joke for {name}")

            # Фолбек към локални шеги (jokes.json)
            if not joke:
                if name in self.jokes:
                    joke = random.choice(self.jokes[name])
                elif "Default" in self.jokes:
                    joke = random.choice(self.jokes["Default"])
            
            if joke:
                # Поздрав (само ако е време)
                current_time = time.time()
                if name not in self.last_greeted or (current_time - self.last_greeted[name] > self.GREETING_COOLDOWN):
                    greeting = self._get_greeting()
                    joke = f"{greeting}, {name}! {joke}"
                    self.last_greeted[name] = current_time

                self._generate_and_play(joke)

            self.speech_queue.task_done()

    def _generate_and_play(self, text):
        try:
            # Превръщаме текста в MD5 хеш за персистентен и надежден кеш
            text_hash = hashlib.md5(text.encode('utf-8')).hexdigest()
            wav_filename = os.path.join(self.temp_dir, f"joke_{text_hash}.wav")
            mp3_filename = os.path.join(self.temp_dir, f"joke_{text_hash}.mp3")
            
            filename = None
            if os.path.exists(wav_filename):
                filename = wav_filename
            elif os.path.exists(mp3_filename):
                filename = mp3_filename
            
            if not filename:
                if self.piper_enabled:
                    log_system(f"Generating new audio with Piper TTS for: {text[:30]}...")
                    try:
                        # Piper генерира директно във файл чрез wave модула
                        with wave.open(wav_filename, 'wb') as w:
                            self.piper_model.synthesize_wav(text, w)
                        filename = wav_filename
                    except Exception as e:
                        log_system(f"Piper TTS generation failed: {e}. Falling back to gTTS.", "error")
                
                # Ако Piper не е активиран или генерирането се е провалило
                if not filename:
                    try:
                        import socket
                        socket.create_connection(("8.8.8.8", 53), timeout=2)
                        
                        log_system(f"Generating new audio with gTTS for: {text[:30]}...")
                        from gtts import gTTS
                        tts = gTTS(text=text, lang='bg')
                        tts.save(mp3_filename)
                        filename = mp3_filename
                    except OSError:
                        log_system("No internet! Cannot generate new joke with gTTS.", "error")
                        return

            # Пускане на аудиото
            pygame.mixer.music.load(filename)
            pygame.mixer.music.play()
            
            # Известяване на уеб панела
            if self.event_callback:
                self.event_callback(os.path.basename(filename))

            # Изчакваме края на шегата, без да блокираме камерата
            while pygame.mixer.music.get_busy():
                time.sleep(0.1)
                
        except Exception as e:
            log_system(f"Error in Piper/gTTS/Pygame: {e}", "error")
