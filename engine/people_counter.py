import threading
from datetime import date
from utils.logger import log_system


class DailyPeopleCounter:
    """ Брояч на уникални разпознати хора за днешния ден. Нулира се автоматично в полунощ. """
    def __init__(self):
        self._lock = threading.Lock()
        self._today = date.today()
        self._seen_names = set()        # Уникални имена (без "Unknown")
        self._unknown_count = 0         # Брой засичания на непознати хора
        self._prev_unknown_in_frame = 0 # Колко непознати е имало в предишния кадър

    def _check_date_reset(self):
        """ Ако датата се е сменила (след полунощ), нулираме брояча. """
        today = date.today()
        if today != self._today:
            self._today = today
            self._seen_names.clear()
            self._unknown_count = 0
            self._prev_unknown_in_frame = 0
            log_system("Daily people counter reset (new day).")

    def register(self, name):
        """ Регистрира засечено лице. Игнорира 'Unknown' и дубликати. """
        if name == "Unknown":
            return
        with self._lock:
            self._check_date_reset()
            if name not in self._seen_names:
                self._seen_names.add(name)
                log_system(f"New person detected today: {name} (total: {self.get_total_unlocked()})")

    def update_unknowns(self, current_unknown_count):
        """
        Обновява броя непознати спрямо текущия кадър.
        Ако в този кадър има ПОВЕЧЕ непознати от предишния - новите се броят.
        Така един непознат, стоящ пред камерата, се брои само веднъж.
        Ако си тръгне и се върне - брои се отново.
        """
        with self._lock:
            self._check_date_reset()
            if current_unknown_count > self._prev_unknown_in_frame:
                new_unknowns = current_unknown_count - self._prev_unknown_in_frame
                self._unknown_count += new_unknowns
                log_system(f"Unknown person(s) detected: +{new_unknowns} (total unknowns today: {self._unknown_count})")
            self._prev_unknown_in_frame = current_unknown_count

    def get_total_unlocked(self):
        """ Вътрешен метод (без lock) - общ брой. """
        return len(self._seen_names) + self._unknown_count

    def get_count(self):
        """ Връща общия брой засечени хора днес (познати + непознати). """
        with self._lock:
            self._check_date_reset()
            return len(self._seen_names) + self._unknown_count
