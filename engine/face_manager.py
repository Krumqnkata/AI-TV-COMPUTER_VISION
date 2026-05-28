import face_recognition
import cv2
import os
import numpy as np
import pickle
import json
from utils.config import Config

class FaceManager:
    def __init__(self, faces_path):
        self.faces_path = faces_path
        self.cache_path = "data/face_encodings.pkl"
        self.known_face_encodings = []
        self.known_face_names = []
        self.names_mapping = self._load_names_mapping()

    def _load_names_mapping(self):
        try:
            if os.path.exists(Config.NAMES_MAPPING_PATH):
                with open(Config.NAMES_MAPPING_PATH, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            print(f"Error loading names mapping: {e}")
        return {}

    def _get_mapped_name(self, name):
        return self.names_mapping.get(name, name)

    def load_faces(self):
        # Преброяваме колко снимки/папки имаме реално в директорията
        current_items = os.listdir(self.faces_path)
        
        # 1. Проверка на кеша
        if os.path.exists(self.cache_path):
            with open(self.cache_path, 'rb') as f:
                data = pickle.load(f)
                
            # Проверяваме дали броят на уникалните имена в кеша съвпада с броя на обектите в папката
            cached_names_count = len(set(data['names']))
            actual_folders_count = len([d for d in current_items if os.path.isdir(os.path.join(self.faces_path, d))])
            
            # Ако броят на папките е същият, зареждаме мигновено
            if cached_names_count == actual_folders_count and actual_folders_count > 0:
                print("Loading from cache (no changes in faces)...")
                self.known_face_encodings = data['encodings']
                self.known_face_names = data['names']
                print(f"DONE: Loaded {len(set(self.known_face_names))} faces.")
                return
            else:
                print("Changes detected in photos. Recalculating faces...")

        # 2. Ако кешът е остарял или го няма, анализираме снимките
        if not os.path.exists(self.faces_path):
            os.makedirs(self.faces_path)
            
        for item in current_items:
            item_path = os.path.join(self.faces_path, item)
            
            if os.path.isdir(item_path):
                person_name = self._get_mapped_name(item)
                for image_name in os.listdir(item_path):
                    self._process_image(os.path.join(item_path, image_name), person_name)
            
            elif os.path.isfile(item_path) and item.lower().endswith(('.png', '.jpg', '.jpeg')):
                person_name = os.path.splitext(item)[0]
                person_name = ''.join([i for i in person_name if not i.isdigit()]).strip()
                person_name = self._get_mapped_name(person_name)
                self._process_image(item_path, person_name)

        # 3. Обновяваме кеша
        if self.known_face_encodings:
            with open(self.cache_path, 'wb') as f:
                pickle.dump({
                    'encodings': self.known_face_encodings,
                    'names': self.known_face_names
                }, f)
            print("Cache updated with new faces.")

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
            
            encodings = face_recognition.face_encodings(rgb_image)
            if encodings:
                self.known_face_encodings.append(encodings[0])
                self.known_face_names.append(name)
                print(f"  [OK] Analyzed face: {name} (from {os.path.basename(image_path)})")
            else:
                print(f"  [!] No face found in: {os.path.basename(image_path)}")
        except Exception as e:
            print(f"  [ERROR] {image_path}: {e}")

    def identify_face(self, frame):
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        face_locations = face_recognition.face_locations(rgb_frame)
        face_encodings = face_recognition.face_encodings(rgb_frame, face_locations)

        face_names = []
        for face_encoding in face_encodings:
            matches = face_recognition.compare_faces(self.known_face_encodings, face_encoding)
            name = self._get_mapped_name("Unknown")
            if self.known_face_encodings:
                face_distances = face_recognition.face_distance(self.known_face_encodings, face_encoding)
                if len(face_distances) > 0:
                    best_match_index = np.argmin(face_distances)
                    if matches[best_match_index]:
                        name = self.known_face_names[best_match_index]
            face_names.append(name)
        return face_locations, face_names