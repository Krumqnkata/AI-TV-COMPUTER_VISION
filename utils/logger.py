import logging
import os
import csv
from datetime import datetime

# Настройка на системния лог
if not os.path.exists("logs"):
    os.makedirs("logs")

log_file = os.path.join("logs", "system.log")
logging.basicConfig(
    filename=log_file,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    encoding='utf-8'
)

def log_system(message, level="info"):
    if level == "info":
        logging.info(message)
    elif level == "error":
        logging.error(message)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")

def log_recognition(name):
    """ Записва всяко разпознаване в CSV файл за история """
    history_file = os.path.join("logs", "history.csv")
    file_exists = os.path.isfile(history_file)

    with open(history_file, mode='a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        # Ако файлът е нов, добавяме заглавия
        if not file_exists:
            writer.writerow(['Дата', 'Час', 'Име'])

        now = datetime.now()
        writer.writerow([now.strftime('%Y-%m-%d'), now.strftime('%H:%M:%S'), name])