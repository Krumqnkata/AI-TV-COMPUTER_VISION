import ollama
from google import genai
from utils.config import Config
from utils.logger import log_system

class LLMManager:
    def __init__(self):
        self.ollama_enabled = Config.OLLAMA_ENABLED
        self.gemini_enabled = bool(Config.GEMINI_API_KEY)
        self.gemini_client = None
        self.ollama_client = None
        
        if self.ollama_enabled:
            try:
                self.ollama_client = ollama.Client(host=Config.OLLAMA_BASE_URL)
                log_system(f"Ollama client initialized at {Config.OLLAMA_BASE_URL}")
            except Exception as e:
                log_system(f"Failed to initialize Ollama client: {e}", "error")
                self.ollama_enabled = False
        
        if self.gemini_enabled:
            try:
                self.gemini_client = genai.Client(api_key=Config.GEMINI_API_KEY)
                log_system("Gemini AI initialized as fallback.")
            except Exception as e:
                log_system(f"Failed to initialize Gemini AI: {e}", "error")
                self.gemini_enabled = False
        
        # Логове за активния доставчик
        if self.ollama_enabled:
            log_system(f"LLM Provider: Ollama (Model: {Config.OLLAMA_MODEL})")
        elif self.gemini_enabled:
            log_system(f"LLM Provider: Gemini (Model: {Config.GEMINI_MODEL_ID})")
        else:
            log_system("LLM Provider: None (AI generation disabled)")

    def generate(self, prompt, system_instruction):
        # 1. Опит с Ollama (ако е разрешено)
        if self.ollama_enabled and self.ollama_client:
            try:
                log_system("Attempting Ollama generation...")
                response = self.ollama_client.chat(model=Config.OLLAMA_MODEL, messages=[
                    {'role': 'system', 'content': system_instruction},
                    {'role': 'user', 'content': prompt},
                ])
                if response and 'message' in response and 'content' in response['message']:
                    log_system("Ollama generation successful.")
                    return response['message']['content'].strip()
            except Exception as e:
                log_system(f"Ollama generation failed: {e}. Falling back to Gemini.", "error")

        # 2. Фолбек към Gemini
        if self.gemini_enabled:
            try:
                log_system("Attempting Gemini generation...")
                response = self.gemini_client.models.generate_content(
                    model=Config.GEMINI_MODEL_ID,
                    contents=prompt,
                    config=genai.types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        temperature=Config.AI_TEMPERATURE,
                    )
                )
                if response and response.text:
                    log_system("Gemini generation successful.")
                    return response.text.strip()
            except Exception as e:
                log_system(f"Gemini generation failed: {e}", "error")
        
        return None
