import os
import json
import random
import time
import wave
from threading import Thread
from queue import Queue
import pygame
from google import genai
from utils.config import Config
from utils.logger import log_system, log_recognition
from piper.voice import PiperVoice

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
                self.model_id = Config.GEMINI_MODEL_ID
                self.ai_enabled = True
                log_system(f"Gemini AI initialized with model: {self.model_id}")
            except Exception as e:
                log_system(f"Failed to initialize Gemini AI: {e}", "error")

        # Инициализиране на Piper TTS
        self.piper_enabled = False
        piper_model_path = os.path.join("engine", "piper", "bg_BG-dimitar-medium.onnx")
        piper_config_path = os.path.join("engine", "piper", "bg_BG-dimitar-medium.onnx.json")
        
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
        """ Генерира шега чрез Gemini AI с рандомизирани и оптимизирани стилове """
        if not self.ai_enabled:
            return None
        
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

        try:
            # Използваме system_instruction за по-ефективно кеширане на контекста и спестяване на токени
            system_instruction = (
                "Ти си гласов асистент за училищно AI огледало. Твоята задача е да напишеш една "
                "ЕДИНСТВЕНА, оригинална, много кратка и забавна закачка/реплика на български език, "
                "базирана на подадения стил и име. Правила: 1. Максимум едно кратко изречение. "
                "2. БЕЗ въвеждащи думи, кавички, звездички или обяснения. "
                "3. БЕЗ емоджита (тъй като гласовият синтезатор не може да ги изчете)."
            )

            # Вдигаме temperature за по-нестандартни отговори
            response = self.client.models.generate_content(
                model=self.model_id,
                contents=prompt,
                config=genai.types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0.95,
                )
            )
            if response and response.text:
                joke = response.text.strip()
                # Изчистване на случайни останали кавички и звездички за по-чисто изговаряне
                joke = joke.replace('"', '').replace('*', '').replace('„', '').replace('“', '') 
                log_system(f"AI generated joke for {name} (Style: {style})")
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

    def _worker(self):
        """ Фонова нишка, която обработва имената, генерира шеги и ги пуска """
        while True:
            name = self.speech_queue.get()
            
            joke = None
            
            # 1. Опит за генериране с ИИ
            if self.ai_enabled:
                joke = self._generate_ai_joke(name)
            
            # 2. Фолбек към локални шеги (само ако ИИ се провали и имаме записани шеги)
            if not joke and name in self.jokes:
                joke = random.choice(self.jokes[name])
            
            # 3. Ако имаме шега, я превръщаме в говор и я пускаме
            if joke:
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
            
            # Изчакваме края на шегата, без да блокираме камерата
            while pygame.mixer.music.get_busy():
                time.sleep(0.1)
                
        except Exception as e:
            log_system(f"Error in Piper/gTTS/Pygame: {e}", "error")