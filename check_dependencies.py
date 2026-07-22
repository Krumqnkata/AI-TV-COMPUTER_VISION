import sys
import os
import re

# Настройване на конзолния изход да поддържа UTF-8 (за да се избегнат UnicodeEncodeError в Windows)
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')


# Опитваме се да заредим нужните стандартни библиотеки за проверка на пакети
try:
    from importlib.metadata import version, PackageNotFoundError
except ImportError:
    # За по-стари версии на Python (под 3.8)
    try:
        import pkg_resources
        def version(pkg_name):
            try:
                return pkg_resources.get_distribution(pkg_name).version
            except pkg_resources.DistributionNotFound:
                raise PackageNotFoundError()
    except ImportError:
        # Абсолютен fallback
        version = None

def parse_requirements(file_path):
    """ Прочита requirements.txt и извлича пакетите и техните версии. """
    packages = []
    if not os.path.exists(file_path):
        return packages
        
    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            # Игнорираме коментари и празни редове
            if not line or line.startswith('#'):
                continue
            
            # Разделяме името на пакета от версията (поддържа ==, >=, <=, ~=, > , <)
            match = re.split(r'(==|>=|<=|~=|!=|>)', line)
            pkg_name = match[0].strip()
            
            # Специфично име на пакет (премахваме допълнителни параметри)
            pkg_name = pkg_name.split('[')[0].strip()
            
            if pkg_name:
                packages.append(pkg_name)
    return packages

def check_dependencies():
    requirements_file = 'requirements.txt'
    
    print("=" * 60)
    print(" Проверка на системните зависимости за AI-TV-COMPUTER-VISION")
    print("=" * 60)
    
    # 1. Проверка дали съществува папката .venv
    venv_python = os.path.join('.venv', 'Scripts', 'python.exe')
    if not os.path.exists(venv_python):
        print("\n [!] ГРЕШКА: Виртуалната среда (.venv) не е намерена!")
        print(" За да стартирате проекта под Windows, изпълнете следните стъпки:\n")
        print(" Стъпка 1: Изтеглете и инсталирайте Python 3.10, 3.11 или 3.12")
        print("   -> Свалете от: https://www.python.org/downloads/")
        print("   -> ВАЖНО: По време на инсталацията отметнете квадратчето \"Add Python to PATH\".\n")
        print(" Стъпка 2: Създайте виртуалната среда (отворете терминал в тази папка):")
        print("   python -m venv .venv\n")
        print(" Стъпка 3: Инсталирайте библиотеките:")
        print("   .venv\\Scripts\\pip install -r requirements.txt\n")
        print(" Стъпка 4: Стартирайте проекта отново чрез двукликов файл 'run.bat'.")
        print("=" * 60)
        return False

    # 2. Проверка дали текущият скрипт се изпълнява в самата виртуална среда
    is_venv_active = (sys.prefix != sys.base_prefix) or ('venv' in sys.executable.lower())
    if not is_venv_active:
        print("\n [!] ВНИМАНИЕ: Скриптът беше стартиран от глобалния Python, а не от виртуалната среда!")
        print(" Моля, стартирайте проекта чрез 'run.bat', който автоматично използва .venv.")
        print("=" * 60)
        return False
        
    if not os.path.exists(requirements_file):
        print(f"[!] Грешка: Файлът '{requirements_file}' не беше намерен в текущата директория.")
        sys.exit(1)
        
    packages = parse_requirements(requirements_file)
    missing_packages = []
    installed_packages = []
    
    for pkg in packages:
        # Някои пакети се инсталират с различно име от това в pip (напр. opencv-python)
        # Но в метаданните (importlib.metadata) името обикновено съвпада с това в изискванията
        normalized_name = pkg.lower().replace('_', '-')
        
        try:
            if version:
                ver = version(normalized_name)
                # Опит за допълнителна проверка за някои пакети с различни имена в метаданните
                if not ver and normalized_name == 'opencv-python':
                    ver = version('opencv-python-headless')
            else:
                # В краен случай се опитваме да направим реален import
                # Това е само ако нямаме метаданни
                if normalized_name == 'opencv-python':
                    import cv2
                elif normalized_name == 'pillow':
                    import PIL
                elif normalized_name == 'python-dotenv':
                    import dotenv
                elif normalized_name == 'google-genai':
                    import google.genai
                elif normalized_name == 'piper-tts':
                    import piper
                elif normalized_name == 'python-multipart':
                    import multipart
                else:
                    __import__(normalized_name.replace('-', '_'))
                ver = "Инсталиран"
                
            installed_packages.append((pkg, ver))
        except (PackageNotFoundError, ImportError, ModuleNotFoundError):
            missing_packages.append(pkg)

    # Принтиране на резултатите
    if installed_packages:
        print("\n Налични пакети:")
        for pkg, ver in installed_packages:
            print(f"  [+] {pkg:<25} -> Версия: {ver}")
            
    if missing_packages:
        print("\n[!] Липсващи пакети:")
        for pkg in missing_packages:
            print(f"  [-] {pkg}")
            
        print("\n" + "=" * 60)
        print(" ЗА ДА ИНСТАЛИРАТЕ ЛИПСВАЩИТЕ ПАКЕТИ, ИЗПЪЛНЕТЕ:")
        print("=" * 60)
        # Формираме команда за инсталиране само на липсващите
        missing_str = " ".join(missing_packages)
        print(f"pip install {missing_str}")
        print("\nИли за инсталация на всички наведнъж:")
        print("pip install -r requirements.txt")
        print("=" * 60)
        
        return False
    else:
        print("\n[OK] Всички зависимости са успешно инсталирани! Проектът е готов за стартиране.")
        print("=" * 60)
        return True

if __name__ == "__main__":
    success = check_dependencies()
    sys.exit(0 if success else 1)
