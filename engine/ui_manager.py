import cv2
import numpy as np
import time
import math
from datetime import datetime
from PIL import Image, ImageDraw

class UIManager:
    """ Управлява предварително рендерираните графични активи за максимална производителност """
    def __init__(self, target_width, target_height, font_main, font_small, font_counter_title):
        self.target_width = target_width
        self.target_height = target_height
        self.assets = {}
        self.last_status = None
        self.last_time_str = None
        self.last_count = -1
        
        # Шрифтове
        self.font_main = font_main
        self.font_small = font_small
        self.font_counter_title = font_counter_title
        
        # Данни за визуализация "Невронна мрежа"
        self.neurons = []
        for _ in range(8):
            self.neurons.append({
                "x": np.random.randint(0, 100), 
                "y": np.random.randint(0, 100),
                "vx": np.random.uniform(-1, 1),
                "vy": np.random.uniform(-1, 1)
            })
        
        # Система за известия
        self.notification_text = ""
        self.notification_expiry = 0
        self.notification_asset = None
        self.notification_mask = None
        
        # Предварително рендериране на статични компоненти
        self._pre_render_static_elements()

    def _pre_render_static_elements(self):
        """ Рендерира веднъж елементите, които никога не се променят """
        # 1. Горна HUD лента (основа)
        hud_h = 80
        hud_base = np.zeros((hud_h, self.target_width, 3), dtype=np.uint8)
        cv2.rectangle(hud_base, (0, 0), (self.target_width, hud_h), (30, 30, 30), -1)
        self.assets['hud_base'] = hud_base

        # 2. SCHOOL AI Текст
        self.assets['title'], self.assets['title_mask'] = self._render_text_asset(
            "SCHOOL AI", self.font_main, (255, 255, 0), (300, 80), (20, 18)
        )

        # 3. Долен панел (основа)
        panel_w, panel_h = 380, 60
        panel_base = np.zeros((panel_h, panel_w, 3), dtype=np.uint8)
        cv2.rectangle(panel_base, (0, 0), (panel_w, panel_h), (20, 20, 20), -1)
        cv2.line(panel_base, (0, 0), (panel_w, 0), (255, 255, 0), 2) # Cyan border
        self.assets['panel_base'] = panel_base

    def _render_text_asset(self, text, font, color_rgb, size, position=(0, 0)):
        """ Помощна функция за рендериране на PIL текст в OpenCV формат с маска """
        img = Image.new("RGBA", size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.text(position, text, font=font, fill=(*color_rgb, 255))
        
        # Конвертираме към numpy array директно
        data = np.array(img)
        rgb = cv2.cvtColor(data[:, :, :3], cv2.COLOR_RGB2BGR)
        mask = data[:, :, 3] > 0
        return rgb, mask

    def show_notification(self, name, duration=3):
        """ Задава ново известие за разпознат човек """
        if not name: return
        
        if name == "Unknown" or name == "Непознат":
            text = "ЗАСЕЧЕН Е НЕПОЗНАТ"
            color = (200, 200, 200)
        else:
            text = f"РАЗПОЗНАТ: {name.upper()}"
            color = (0, 255, 255) # Cyan

        if text != self.notification_text:
            self.notification_text = text
            try:
                tw = int(self.font_main.getlength(text))
            except: tw = 400
            self.notification_asset, self.notification_mask = self._render_text_asset(
                text, self.font_main, color, (tw + 20, 50)
            )
        
        self.notification_expiry = time.time() + duration

    def draw(self, frame, face_data, is_processing, people_counter):
        h_orig, w_orig = frame.shape[:2]
        
        # Винаги изчисляваме мащаба, за да напаснем координатите на лицата
        scale_x = self.target_width / w_orig
        scale_y = self.target_height / h_orig

        # Преоразмеряваме кадъра, ако не съвпада с HUD активите
        if w_orig != self.target_width or h_orig != self.target_height:
            frame = cv2.resize(frame, (self.target_width, self.target_height), interpolation=cv2.INTER_LINEAR)
            h, w = self.target_height, self.target_width
        else:
            h, w = h_orig, w_orig

        # Обновяваме известията
        for _, name in face_data:
            self.show_notification(name)

        # 1. Горна HUD лента
        roi_hud = frame[0:80, 0:w]
        cv2.addWeighted(self.assets['hud_base'], 0.6, roi_hud, 0.4, 0, roi_hud)

        # 2. SCHOOL AI Заглавие
        title_h, title_w = self.assets['title'].shape[:2]
        mask_title = self.assets['title_mask']
        frame[0:title_h, 0:title_w][mask_title] = self.assets['title'][mask_title]

        # 3. Интелигентно известие
        current_time = time.time()
        if current_time < self.notification_expiry and self.notification_asset is not None:
            n_h, n_w = self.notification_asset.shape[:2]
            start_x = 320 
            if start_x + n_w < w - 320:
                roi_notif = frame[18:18+n_h, start_x:start_x+n_w]
                roi_notif[self.notification_mask] = self.notification_asset[self.notification_mask]

        # 4. Динамичен часовник
        time_str = datetime.now().strftime("TIME: %H:%M:%S")
        if time_str != self.last_time_str:
            self.assets['time'], self.assets['time_mask'] = self._render_text_asset(
                time_str, self.font_main, (255, 255, 255), (290, 50)
            )
            self.last_time_str = time_str

        t_h, t_w = self.assets['time'].shape[:2]
        start_x_time = w - t_w - 20
        frame[18:18+t_h, start_x_time:start_x_time+t_w][self.assets['time_mask']] = self.assets['time'][self.assets['time_mask']]

        # 5. Статус на системата
        if is_processing != self.last_status:
            status_text = "STATUS: ACTIVE" if is_processing else "STATUS: PAUSED"
            status_color = (0, 255, 0) if is_processing else (255, 0, 0)
            try: sw = int(self.font_main.getlength(status_text))
            except: sw = 300
            self.assets['status'], self.assets['status_mask'] = self._render_text_asset(
                status_text, self.font_main, status_color, (sw + 20, 50)
            )
            self.last_status = is_processing

        s_h, s_w = self.assets['status'].shape[:2]
        start_x_status = start_x_time - s_w - 60
        frame[18:18+s_h, start_x_status:start_x_status+s_w][self.assets['status_mask']] = self.assets['status'][self.assets['status_mask']]

        # 6. Долен панел
        count = people_counter.get_count()
        px, py = 15, h - 75
        roi_panel = frame[py:py+60, px:px+380]
        cv2.addWeighted(self.assets['panel_base'], 0.7, roi_panel, 0.3, 0, roi_panel)

        if count != self.last_count:
            count_text = f"ЗАСЕЧЕНИ ДНЕС: {count}"
            self.assets['count'], self.assets['count_mask'] = self._render_text_asset(
                count_text, self.font_counter_title, (0, 255, 255), (360, 50)
            )
            self.last_count = count

        c_h, c_w = self.assets['count'].shape[:2]
        frame[py+12:py+12+c_h, px+12:px+12+c_w][self.assets['count_mask']] = self.assets['count'][self.assets['count_mask']]

        # 7. Рамки около лицата и ИМЕНА (на Кирилица)
        if 'name_labels' not in self.assets:
            self.assets['name_labels'] = {} # Кеш за рендерирани имена

        for (top_orig, right_orig, bottom_orig, left_orig), name in face_data:
            # Мащабираме координатите към текущия размер на екрана
            top = int(top_orig * scale_y)
            right = int(right_orig * scale_x)
            bottom = int(bottom_orig * scale_y)
            left = int(left_orig * scale_x)

            color = (0, 255, 255) if name != "Unknown" else (200, 200, 200)
            cv2.rectangle(frame, (left, top), (right, bottom), color, 2)
            
            # Използваме кеширано изображение за името
            label = name.upper()
            if label not in self.assets['name_labels']:
                # Рендерираме името веднъж и го кешираме
                try: tw = int(self.font_small.getlength(label))
                except: tw = len(label) * 20
                self.assets['name_labels'][label] = self._render_text_asset(
                    label, self.font_small, color, (tw + 10, 50)
                )
            
            label_img, label_mask = self.assets['name_labels'][label]
            lh, lw = label_img.shape[:2]
            
            # Позиционираме името под рамката
            lx = left
            ly = bottom + 10
            
            if ly + lh < h and lx + lw < w:
                roi_label = frame[ly:ly+lh, lx:lx+lw]
                roi_label[label_mask] = label_img[label_mask]

        # 8. Пулсиращ индикатор за "мислене"
        if is_processing:
            self._draw_thinking_indicator(frame, w, h)

        return frame

    def _draw_thinking_indicator(self, frame, w, h):
        """ Рисува динамична "кибер-невронна мрежа" в долния десен ъгъл при обработка """
        # Параметри на визуализацията (увеличени)
        center_x, center_y = w - 150, h - 150
        neuron_radius = 6
        connection_threshold = 100
        area_size = 150 # Размер на зоната на движение
        
        # Обновяваме позициите
        for n in self.neurons:
            n["x"] += n["vx"]
            n["y"] += n["vy"]
            
            # Отскачане от границите на зоната
            if n["x"] < 0 or n["x"] > area_size: n["vx"] *= -1
            if n["y"] < 0 or n["y"] > area_size: n["vy"] *= -1
        
        # Рисуваме връзките (Сини/Циано: (255, 255, 0))
        for i in range(len(self.neurons)):
            for j in range(i + 1, len(self.neurons)):
                n1 = self.neurons[i]
                n2 = self.neurons[j]
                
                # Позиции в рамките на кадъра
                p1 = (int(center_x + n1["x"] - area_size/2), int(center_y + n1["y"] - area_size/2))
                p2 = (int(center_x + n2["x"] - area_size/2), int(center_y + n2["y"] - area_size/2))
                
                dist = math.sqrt((n1["x"] - n2["x"])**2 + (n1["y"] - n2["y"])**2)
                
                if dist < connection_threshold:
                    cv2.line(frame, p1, p2, (122, 184, 154), 2)
        
        # Рисуваме невроните (точките)
        for n in self.neurons:
            pos = (int(center_x + n["x"] - area_size/2), int(center_y + n["y"] - area_size/2))
            cv2.circle(frame, pos, neuron_radius, (145, 249, 248), -1)
