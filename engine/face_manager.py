import face_recognition
import cv2
import os
import numpy as np
import pickle
import json
import hashlib
import mediapipe as mp
from utils.config import Config

class FaceManager:
    def __init__(self, faces_path):
        self.faces_path = faces_path
        self.cache_path = "data/face_encodings.pkl"
        self.known_face_encodings = []
        self.known_face_names = []
        self.names_mapping = self._load_names_mapping()
        
        # Инициализиране на MediaPipe Face Detection
        self.mp_face_detection = mp.solutions.face_detection.FaceDetection(
            model_selection=1, # 0 за близки лица (2м), 1 за далечни (5м)
            min_detection_confidence=0.5
        )
        # CLAHE за подобряване на контраста
        self.clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))

    def _apply_clahe(self, image):
        """Прилага CLAHE върху L-канала в LAB цветовото пространство."""
        lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        l_enhanced = self.clahe.apply(l)
        lab_enhanced = cv2.merge((l_enhanced, a, b))
        return cv2.cvtColor(lab_enhanced, cv2.COLOR_LAB2BGR)

    def _load_names_mapping(self):
        try:
            if os.path.exists(Config.NAMES_MAPPING_PATH):
                with open(Config.NAMES_MAPPING_PATH, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            print(f"Error loading names mapping: {e}")
        return {}

    def _get_mapped_name(self, raw_name):
        # Премахваме цифри и излишни разстояния от името на файла/папката
        clean_name = ''.join([i for i in raw_name if not i.isdigit()]).strip()
        
        # Ако е системна дума, не я форматираме
        if clean_name.lower() == "unknown":
            return self.names_mapping.get("Unknown", "Непознат")
            
        if clean_name in self.names_mapping:
            return self.names_mapping[clean_name]
            
        # Автоматично генерираме красиво име: ivan_petrov -> Ivan Petrov
        beautiful_name = clean_name.replace('_', ' ').replace('-', ' ').title()
        
        # Запазваме автоматично новото име в мапинга за по-лесно превеждане на кирилица по-късно
        self.names_mapping[clean_name] = beautiful_name
        self._save_names_mapping()
        
        return beautiful_name

    def _save_names_mapping(self):
        try:
            # Уверяваме се, че директорията съществува
            os.makedirs(os.path.dirname(Config.NAMES_MAPPING_PATH), exist_ok=True)
            with open(Config.NAMES_MAPPING_PATH, 'w', encoding='utf-8') as f:
                json.dump(self.names_mapping, f, ensure_ascii=False, indent=4)
            print(f"Names mapping updated automatically at: {Config.NAMES_MAPPING_PATH}")
        except Exception as e:
            print(f"Error saving names mapping: {e}")

    def _calculate_faces_fingerprint(self):
        """
        Генерира уникален хеш за цялата папка с лица.
        Следи за промени в имената, размерите и датите на промяна на всички файлове.
        """
        if not os.path.exists(self.faces_path):
            return "no_folder"
            
        fingerprint_parts = []
        
        # Обхождаме всички файлове рекурсивно
        for root, dirs, files in os.walk(self.faces_path):
            for file in sorted(files):
                if file.lower().endswith(('.png', '.jpg', '.jpeg')):
                    file_path = os.path.join(root, file)
                    try:
                        stats = os.stat(file_path)
                        # Събираме метаданни за файла: път, размер, дата на последна промяна
                        fingerprint_parts.append(f"{file_path}_{stats.st_size}_{stats.st_mtime}")
                    except Exception:
                        continue
        
        if not fingerprint_parts:
            return "empty"
            
        # Създаваме един общ хеш от всички метаданни
        full_string = "|".join(fingerprint_parts)
        return hashlib.md5(full_string.encode('utf-8')).hexdigest()

    def load_faces(self):
        # 1. Изчисляваме текущия отпечатък на папката
        current_fingerprint = self._calculate_faces_fingerprint()
        
        # 2. Проверка на кеша
        if os.path.exists(self.cache_path):
            try:
                with open(self.cache_path, 'rb') as f:
                    data = pickle.load(f)
                
                # Проверяваме дали отпечатъкът съвпада
                cached_fingerprint = data.get('fingerprint', '')
                
                if current_fingerprint == cached_fingerprint and current_fingerprint != "empty":
                    print("Loading from cache (no changes in faces detected)...")
                    self.known_face_encodings = data['encodings']
                    self.known_face_names = data['names']
                    print(f"DONE: Loaded {len(set(self.known_face_names))} faces from cache.")
                    return
                else:
                    print(f"Changes detected in photos (or cache is old). Recalculating encodings...")
            except Exception as e:
                print(f"Error reading cache: {e}. Rebuilding...")

        # 3. Ако кешът е остарял или го няма, анализираме снимките
        if not os.path.exists(self.faces_path):
            os.makedirs(self.faces_path)
            
        current_items = os.listdir(self.faces_path)
        for item in current_items:
            item_path = os.path.join(self.faces_path, item)
            
            if os.path.isdir(item_path):
                person_name = self._get_mapped_name(item)
                for image_name in os.listdir(item_path):
                    if image_name.lower().endswith(('.png', '.jpg', '.jpeg')):
                        self._process_image(os.path.join(item_path, image_name), person_name)
            
            elif os.path.isfile(item_path) and item.lower().endswith(('.png', '.jpg', '.jpeg')):
                person_name = os.path.splitext(item)[0]
                person_name = ''.join([i for i in person_name if not i.isdigit()]).strip()
                person_name = self._get_mapped_name(person_name)
                self._process_image(item_path, person_name)

        # 4. Обновяваме кеша с новия отпечатък
        if self.known_face_encodings:
            with open(self.cache_path, 'wb') as f:
                pickle.dump({
                    'encodings': self.known_face_encodings,
                    'names': self.known_face_names,
                    'fingerprint': current_fingerprint
                }, f)
            print(f"Cache updated with new faces. Fingerprint: {current_fingerprint}")

    def _process_image(self, image_path, name):
        try:
            with open(image_path, "rb") as f:
                chunk = f.read()
            chunk_array = np.frombuffer(chunk, dtype=np.uint8)
            image = cv2.imdecode(chunk_array, cv2.IMREAD_COLOR)
            
            if image is None: 
                print(f"  [!] Failed to decode: {image_path}")
                return
            
            height, width = image.shape[:2]
            if width > 1000:
                scale = 1000 / width
                image = cv2.resize(image, (0,0), fx=scale, fy=scale)
            
            rgb_image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            
            # Прилагаме CLAHE за по-добро разпознаване
            enhanced_image = self._apply_clahe(image)
            enhanced_rgb = cv2.cvtColor(enhanced_image, cv2.COLOR_BGR2RGB)
            
            encodings = face_recognition.face_encodings(enhanced_rgb)
            if encodings:
                self.known_face_encodings.append(encodings[0])
                self.known_face_names.append(name)
                print(f"  [OK] Analyzed face: {name} (from {os.path.basename(image_path)})")
            else:
                print(f"  [!] No face found in: {os.path.basename(image_path)}")
        except Exception as e:
            print(f"  [ERROR] {image_path}: {e}")

    def identify_face(self, frame, resize_factor=0.25):
        height, width = frame.shape[:2]
        
        # 1. Засичане чрез MediaPipe върху умален кадър за бързина
        small_w = int(width * resize_factor)
        small_h = int(height * resize_factor)
        small_frame = cv2.resize(frame, (small_w, small_h), interpolation=cv2.INTER_NEAREST)
        rgb_small = cv2.cvtColor(small_frame, cv2.COLOR_BGR2RGB)
        
        results = self.mp_face_detection.process(rgb_small)
        
        face_locations = []
        if results.detections:
            for detection in results.detections:
                bbox = detection.location_data.relative_bounding_box
                
                # Преобразуваме координатите директно към оригиналния размер (висока резолюция)
                left = int(bbox.xmin * width)
                top = int(bbox.ymin * height)
                right = int((bbox.xmin + bbox.width) * width)
                bottom = int((bbox.ymin + bbox.height) * height)
                
                # Добавяме лек padding (20%) за по-добро разпознаване от dlib
                pad_w = int((right - left) * 0.20)
                pad_h = int((bottom - top) * 0.20)
                
                left = max(0, left - pad_w)
                top = max(0, top - pad_h)
                right = min(width, right + pad_w)
                bottom = min(height, bottom + pad_h)
                
                face_locations.append((top, right, bottom, left))

        if not face_locations:
            return [], []

        # 2. Разпознаване чрез face_recognition (използвайки локациите от MediaPipe)
        face_names = []
        valid_face_locations = []
        
        for (top, right, bottom, left) in face_locations:
            # Изрязваме лицето от оригиналния кадър
            face_crop = frame[top:bottom, left:right]
            if face_crop.size == 0:
                continue
                
            # Ограничаваме разделителната способност на лицето за бързина и стабилност на dlib
            h_c, w_c = face_crop.shape[:2]
            target_w = 160
            if w_c > target_w:
                scale = target_w / w_c
                face_crop = cv2.resize(face_crop, (0, 0), fx=scale, fy=scale, interpolation=cv2.INTER_LINEAR)
                
            # Прилагаме CLAHE само върху лицето (много по-бързо)
            enhanced_crop = self._apply_clahe(face_crop)
            enhanced_rgb_crop = cv2.cvtColor(enhanced_crop, cv2.COLOR_BGR2RGB)
            
            # Извличаме характеристиките само за това изрязано лице чрез по-бързия 5-точков модел
            h_crop, w_crop = enhanced_rgb_crop.shape[:2]
            encodings = face_recognition.face_encodings(enhanced_rgb_crop, [(0, w_crop, h_crop, 0)], model="small")
            
            if encodings:
                face_encoding = encodings[0]
                matches = face_recognition.compare_faces(self.known_face_encodings, face_encoding)
                name = self._get_mapped_name("Unknown")
                if self.known_face_encodings:
                    face_distances = face_recognition.face_distance(self.known_face_encodings, face_encoding)
                    if len(face_distances) > 0:
                        best_match_index = np.argmin(face_distances)
                        if matches[best_match_index]:
                            name = self.known_face_names[best_match_index]
                face_names.append(name)
                valid_face_locations.append((top, right, bottom, left))
        
        return valid_face_locations, face_names