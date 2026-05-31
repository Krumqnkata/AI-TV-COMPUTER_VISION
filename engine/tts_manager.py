import os
import json
import random
import time
import wave
from threading import Thread
from queue import Queue
import pygame
from engine.llm_manager import LLMManager
from utils.config import Config
from utils.logger import log_system, log_recognition
from piper.voice import PiperVoice

class TTSManager:
    def __init__(self, jokes_file, cooldown, state_manager=None):
        self.cooldown = cooldown
        self.last_seen = {} # {name: timestamp}
        self.last_greeted = {} # {name: timestamp}
        self.GREETING_COOLDOWN = 3600 # 1 час
        self.jokes = self.load_jokes(jokes_file)
        self.speech_queue = Queue()
        self.state_manager = state_manager
        
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

        # Кеш система за спестяване на API заявки и токени
        self.ai_cache_file = "data/ai_jokes_cache.json"
        self.ai_jokes_cache = self._load_ai_jokes_cache()
        self.api_calls_log = []  # Лог с времеви клейма за API повиквания

        # Стартираме фонова нишка за обработка на опашката от шеги
        Thread(target=self._worker, daemon=True).start()

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

    def _generate_ai_joke(self, name):
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
        contexts = [
            "сякаш го хващаш в крачка или закъснение",
            "сякаш току-що си открил нов странен вид",
            "сякаш разкриваш голяма негова тайна",
            "сякаш посрещаш суперзвезда",
            "сякаш ти е омръзнало да виждаш хора",
            "с лека ирония"
        ]

        tone = random.choice(tones)
        role = random.choice(roles)
        context = random.choice(contexts)
        style = f"Като {tone} {role}, {context}."

        if name == "Непознат":
            prompt = f"Стил: {style} Пред камерата застана непознат човек."
        else:
            prompt = f"Стил: {style} Разпознат е човек с име {name}."

        system_instruction = (
            "Ти си гласов асистент за училищно AI огледало. Твоята задача е да напишеш една "
            "ЕДИНСТВЕНА, оригинална, много кратка и забавна закачка/реплика на български език, "
            "базирана на подадения стил и име. Правила: 1. Максимум едно кратко изречение. "
            "2. БЕЗ въвеждащи думи, кавички, звездички или обяснения. "
            "3. БЕЗ емоджита (тъй като гласовият синтезатор не може да ги изчете)."
        )

        joke = self.llm_manager.generate(prompt, system_instruction)
        
        if joke:
            # Изчистване на случайни останали кавички и звездички за по-чисто изговаряне
            joke = joke.replace('"', '').replace('*', '').replace('„', '').replace('“', '') 
            log_system(f"AI generated joke for {name} (Style: {style})")
            return joke
        
        return None

    def speak_joke(self, name):
        current_time = time.time()
        if name not in self.last_seen or (current_time - self.last_seen[name] > self.cooldown):
            self.last_seen[name] = current_time
            
            # Логване на разпознаването
            log_system(f"Recognized: {name}")
            log_recognition(name)

            # Изчистване на опашката от стари разпознавания, ако има натрупване
            while self.speech_queue.qsize() > 1:
                try:
                    self.speech_queue.get_nowait()
                    self.speech_queue.task_done()
                except Exception:
                    pass

            # Добавяме името в опашката. 
            # Цялото мислене и генериране ще се случи във фоновата нишка.
            self.speech_queue.put(name)

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
            name = self.speech_queue.get()
            
            joke = None
            cached_list = self.ai_jokes_cache.get(name, [])
            
            # Стратегия за спестяване на API заявки / токени:
            # 1. Ако API е лимитирано или ИИ е спрян -> преизползваме генерирана ИИ шега (ако има)
            # 2. Ако имаме вече поне 3 уникални генерирани шеги за този човек, в 60% от случаите ги преизползваме
            # 3. Иначе правим нова заявка към Gemini API
            use_cache = False
            if not (self.llm_manager.ollama_enabled or self.llm_manager.gemini_enabled) or self._is_api_rate_limited():
                use_cache = True
            elif len(cached_list) >= 3 and random.random() < Config.AI_CACHE_REUSE_PROB:
                use_cache = True

            if use_cache and cached_list:
                joke = random.choice(cached_list)
                log_system(f"Reusing cached AI joke for {name} (Token saving / Rate limiter active)")

            # Ако не ползваме кеш (или няма такъв) и API е достъпно
            if not joke and (self.llm_manager.ollama_enabled or self.llm_manager.gemini_enabled) and not self._is_api_rate_limited():
                joke = self._generate_ai_joke(name)
                if joke:
                    # Добавяме новата шега в кеша
                    if name not in self.ai_jokes_cache:
                        self.ai_jokes_cache[name] = []
                    if joke not in self.ai_jokes_cache[name]:
                        self.ai_jokes_cache[name].append(joke)
                        self._save_ai_jokes_cache()
                    
                    # Записваме времето на успешна API заявка
                    self.api_calls_log.append(time.time())
            
            # 2. Фолбек към локални статични шеги от jokes.json (ако ИИ няма резултат)
            if not joke:
                if name in self.jokes:
                    joke = random.choice(self.jokes[name])
                elif "Default" in self.jokes:
                    joke = random.choice(self.jokes["Default"])
                elif "Общи" in self.jokes:
                    joke = random.choice(self.jokes["Общи"])
            
            # 3. Ако имаме шега, я превръщаме в говор и я пускаме
            if joke:
                # Проверка дали трябва да поздравим
                current_time = time.time()
                if name not in self.last_greeted or (current_time - self.last_greeted[name] > self.GREETING_COOLDOWN):
                    greeting = self._get_greeting()
                    joke = f"{greeting}, {name}! {joke}"
                    self.last_greeted[name] = current_time

                self._generate_and_play(joke)

            self.speech_queue.task_done()

    def _generate_and_play(self, text):
        try:
            # Piper генерира WAV файл.
            filename = os.path.join(self.temp_dir, f"joke_{hash(text)}.wav") 
            
            if not os.path.exists(filename):
                if self.piper_enabled:
                    log_system(f"Generating new audio with Piper TTS for: {text[:30]}...")
                    try:
                        # Piper генерира директно във файл чрез wave модула
                        with wave.open(filename, 'wb') as w:
                            self.piper_model.synthesize_wav(text, w)
                    except Exception as e:
                        log_system(f"Piper TTS generation failed: {e}. Falling back to gTTS.", "error")
                        
                        # Фолбек към gTTS, ако Piper се провали
                        try:
                            import socket
                            socket.create_connection(("8.8.8.8", 53), timeout=2)
                            
                            log_system(f"Generating new audio with gTTS for: {text[:30]}...")
                            from gtts import gTTS
                            tts = gTTS(text=text, lang='bg')
                            tts.save(filename.replace(".wav", ".mp3"))
                            filename = filename.replace(".wav", ".mp3")
                        except OSError:
                            log_system("No internet! Cannot generate new joke with gTTS.", "error")
                            return
                else: 
                    # Ако Piper не е enabled, директно пробваме gTTS
                    try:
                        import socket
                        socket.create_connection(("8.8.8.8", 53), timeout=2)
                        
                        log_system(f"Generating new audio with gTTS for: {text[:30]}...")
                        from gtts import gTTS
                        tts = gTTS(text=text, lang='bg')
                        tts.save(filename.replace(".wav", ".mp3"))
                        filename = filename.replace(".wav", ".mp3")
                    except OSError:
                        log_system("No internet! Cannot generate new joke with gTTS.", "error")
                        return

            # Пускане на аудиото
            pygame.mixer.music.load(filename)
            pygame.mixer.music.play()
            
            # Известяване на уеб панела
            if self.state_manager:
                self.state_manager.on_speech_ready(os.path.basename(filename))

            # Изчакваме края на шегата, без да блокираме камерата
            while pygame.mixer.music.get_busy():
                time.sleep(0.1)
                
        except Exception as e:
            log_system(f"Error in Piper/gTTS/Pygame: {e}", "error")