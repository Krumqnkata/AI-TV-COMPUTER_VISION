import threading
from datetime import date
from utils.logger import log_system


class DailyPeopleCounter:
    """ Брояч на уникални разпознати хора за днешния ден. Нулира се автоматично в полунощ. """
    def __init__(self):
        self._lock = threading.Lock()
        self._today = date.today()
        self._seen_names = set()        # Уникални имена (познати или уникални Unknown-и)

    def _check_date_reset(self):
        """ Ако датата се е сменила (след полунощ), нулираме брояча. """
        today = date.today()
        if today != self._today:
            self._today = today
            self._seen_names.clear()
            log_system("Daily people counter reset (new day).")

    def register(self, name):
        """ Регистрира засечено лице. Игнорира дубликати. """
        with self._lock:
            self._check_date_reset()
            if name not in self._seen_names:
                self._seen_names.add(name)
                log_system(f"New person detected today: {name} (total: {len(self._seen_names)})")

    def get_count(self):
        """ Връща общия брой засечени хора днес (познати + уникални непознати). """
        with self._lock:
            self._check_date_reset()
            return len(self._seen_names)
