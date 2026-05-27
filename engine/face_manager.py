import face_recognition
import cv2
import os
import numpy as np

class FaceManager:
    def __init__(self, faces_path):
        self.faces_path = faces_path
        self.known_face_encodings = []
        self.known_face_names = []

    def load_faces(self):
        print("Зареждане на лица...")
        if not os.path.exists(self.faces_path):
            os.makedirs(self.faces_path)
            
        for name in os.listdir(self.faces_path):
            person_dir = os.path.join(self.faces_path, name)
            if not os.path.isdir(person_dir):
                continue
            
            for image_name in os.listdir(person_dir):
                image_path = os.path.join(person_dir, image_name)
                try:
                    image = face_recognition.load_image_file(image_path)
                    encodings = face_recognition.face_encodings(image)
                    if encodings:
                        self.known_face_encodings.append(encodings[0])
                        self.known_face_names.append(name)
                        print(f"Заредено лице за: {name} ({image_name})")
                except Exception as e:
                    print(f"Грешка при зареждане на {image_path}: {e}")
        print(f"Заредени са общо {len(self.known_face_names)} лица.")

    def identify_face(self, frame):
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        face_locations = face_recognition.face_locations(rgb_frame)
        face_encodings = face_recognition.face_encodings(rgb_frame, face_locations)

        face_names = []
        for face_encoding in face_encodings:
            matches = face_recognition.compare_faces(self.known_face_encodings, face_encoding)
            name = "Unknown"

            face_distances = face_recognition.face_distance(self.known_face_encodings, face_encoding)
            if len(face_distances) > 0:
                best_match_index = np.argmin(face_distances)
                if matches[best_match_index]:
                    name = self.known_face_names[best_match_index]

            face_names.append(name)
        return face_locations, face_names
