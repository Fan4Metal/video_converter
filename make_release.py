import re
import subprocess
import sys
from pathlib import Path


def run_command(command, shell=False):
    """Запускает команду и проверяет результат"""
    print(f"Выполняется: {command}")
    result = subprocess.run(command, shell=shell, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Ошибка: {result.stderr}")
        sys.exit(1)
    print("Успешно выполнено")
    return result


def extract_version_from_file(file_path):
    """Извлекает версию из файла Python"""
    version_pattern = r'__VERSION__\s*=\s*["\']([^"\']+)["\']'

    with open(file_path, "r", encoding="utf-8") as file:
        content = file.read()
        match = re.search(version_pattern, content)
        if match:
            return match.group(1)
        else:
            raise ValueError(f"Версия не найдена в файле {file_path}")


def update_iss_version(iss_file_path, new_version):
    """Обновляет версию в файле Inno Setup"""
    with open(iss_file_path, "r", encoding="utf-8") as file:
        content = file.read()

    # Заменяем версию в определении MyAppVersion
    old_pattern = r'#define MyAppVersion "[^"]+"'
    new_define = f'#define MyAppVersion "{new_version}"'
    content = re.sub(old_pattern, new_define, content)

    with open(iss_file_path, "w", encoding="utf-8") as file:
        file.write(content)

    print(f"Версия в {iss_file_path} обновлена до {new_version}")


def main():
    try:
        # Шаг 1: Извлекаем версию из main.py
        print("=== Извлечение версии ===")
        version = extract_version_from_file("main.py")
        print(f"Найдена версия: {version}")

        # Шаг 2: Запускаем PyInstaller
        print("\n=== Запуск PyInstaller ===")
        pyinstaller_cmd = [
            "uv",
            "run",
            "pyinstaller",
            "--clean",
            "--noconsole",
            "--noconfirm",
            "--onedir",
            "--icon=.\\images\\favicon.ico",
            "--add-data=images\\favicon.png;.\\images",
            "--add-data=ffprobe.exe;.",
            "--add-data=ffmpeg.exe;.",
            "--name=VC",
            "main.py",
        ]
        run_command(pyinstaller_cmd)

        # Шаг 3: Обновляем версию в vc.iss
        print("\n=== Обновление версии в Inno Setup ===")
        update_iss_version(".\\dist\\vc.iss", version)

        # Шаг 4: Компилируем установщик Inno Setup
        print("\n=== Компиляция установщика ===")
        # Путь к компилятору Inno Setup (может потребоваться изменить)
        iscc_paths = [R"C:\Program Files (x86)\Inno Setup 6\ISCC.exe", R"C:\Program Files\Inno Setup 6\ISCC.exe"]

        iscc_found = False
        for iscc_path in iscc_paths:
            if Path(iscc_path).exists():
                iscc_cmd = [iscc_path, ".\\dist\\vc.iss"]
                run_command(iscc_cmd)
                iscc_found = True
                break

        if not iscc_found:
            # Если не нашли ISCC в стандартных путях, используем команду напрямую
            print("ISCC не найден в стандартных путях, пытаемся запустить через PATH...")
            run_command(["ISCC", ".\\dist\\vc.iss"])

        print(f"\n=== Выпуск версии {version} успешно создан! ===")

    except Exception as e:
        print(f"Ошибка при создании выпуска: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
