import cv2
from utils.config import Config
from engine.face_manager import FaceManager
from engine.tts_manager import TTSManager

def main():
    face_manager = FaceManager(Config.FACES_DATA_PATH)
    face_manager.load_faces()
    
    tts_manager = TTSManager(Config.JOKES_FILE_PATH, Config.COOLDOWN_SECONDS)
    
    video_capture = cv2.VideoCapture(Config.CAMERA_INDEX)

    if not video_capture.isOpened():
        print(f"Грешка: Не може да се отвори камерата с индекс {Config.CAMERA_INDEX}")
        return

    print("Стартиране на камерата. Натисни 'q' за изход.")

    while True:
        ret, frame = video_capture.read()
        if not ret:
            print("Грешка при четене от камерата.")
            break

        # Намаляваме размера на кадъра за по-бърза обработка
        small_frame = cv2.resize(frame, (0, 0), fx=0.25, fy=0.25)
        
        face_locations, face_names = face_manager.identify_face(small_frame)

        for (top, right, bottom, left), name in zip(face_locations, face_names):
            # Мащабираме обратно координатите (4 пъти)
            top *= 4
            right *= 4
            bottom *= 4
            left *= 4

            # Рисуване на рамка
            cv2.rectangle(frame, (left, top), (right, bottom), (0, 255, 0), 2)
            cv2.putText(frame, name, (left, top - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2)

            if name != "Unknown":
                tts_manager.speak_joke(name)

        cv2.imshow('School AI TV', frame)

        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    video_capture.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()
