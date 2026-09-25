"""
Сборка выпуска: PyInstaller (dist\\VC) + установщик Inno Setup (dist\\Video_Converter <версия> Setup.exe).

Запускается из любой папки: uv run tools/make_release.py
Вывод PyInstaller и ISCC показывается как есть, чтобы был виден ход сборки.
"""

import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "vc" / "__init__.py"
ISS_FILE = ROOT / "tools" / "setup.iss"
DIST_DIR = ROOT / "dist"
BUILD_DIR = ROOT / "build"
APP_DIR = DIST_DIR / "VC"
WX_LOCALE_SRC = ROOT / ".venv" / "Lib" / "site-packages" / "wx" / "locale" / "ru"
WX_LOCALE_DST = APP_DIR / "_internal" / "wx" / "locale" / "ru"

# (источник относительно корня, папка назначения внутри сборки)
BUNDLED_DATA = [
    ("images/favicon.png", "images"),
    ("images/favicon.ico", "images"),
    ("ffprobe.exe", "."),
    ("ffmpeg.exe", "."),
    ("mpv.exe", "."),
    ("LICENSE", "."),
    ("sound.wav", "."),
]

ISCC_PATHS = [
    Path(R"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
    Path(R"C:\Program Files\Inno Setup 6\ISCC.exe"),
]


class ReleaseError(Exception):
    """Ошибка сборки с готовым для показа сообщением."""


def human_size(num_bytes: int) -> str:
    num = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024:
            return f"{num:.1f} {unit}" if unit != "B" else f"{int(num)} {unit}"
        num /= 1024
    return f"{num:.1f} TB"


def dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def fmt_cmd(command: list[str]) -> str:
    return " ".join(f'"{c}"' if " " in c else c for c in command)


def run_command(command: list[str], title: str) -> None:
    """Запускает команду, показывая её вывод как есть; при ошибке прерывает сборку."""
    print(f"$ {fmt_cmd(command)}\n")
    started = time.monotonic()
    result = subprocess.run(command, cwd=ROOT)
    elapsed = time.monotonic() - started
    if result.returncode != 0:
        raise ReleaseError(f"{title}: команда завершилась с кодом {result.returncode} (за {elapsed:.0f} с)")
    print(f"\n{title}: готово за {elapsed:.0f} с")


class Steps:
    """Печатает заголовки шагов и время, ушедшее на предыдущий."""

    def __init__(self, total: int):
        self.total = total
        self.started: float | None = None

    def _close(self) -> None:
        if self.started is not None:
            print(f"--- шаг занял {time.monotonic() - self.started:.1f} с")

    def next(self, number: int, title: str) -> None:
        self._close()
        print(f"\n=== [{number}/{self.total}] {title} ===")
        self.started = time.monotonic()

    def finish(self) -> None:
        self._close()
        self.started = None


def extract_version(path: Path) -> str:
    content = path.read_text(encoding="utf-8")
    match = re.search(r'__VERSION__\s*=\s*["\']([^"\']+)["\']', content)
    if not match:
        raise ReleaseError(f"__VERSION__ не найдена в {path}")
    return match.group(1)


def check_prerequisites() -> Path | None:
    """Проверяет ресурсы для сборки и ищет ISCC. Возвращает путь к ISCC или None (поиск через PATH)."""
    missing = [src for src, _ in BUNDLED_DATA if not (ROOT / src).is_file()]
    if missing:
        raise ReleaseError("не найдены файлы для сборки: " + ", ".join(missing))
    for src, _ in BUNDLED_DATA:
        print(f"  {src:22s} {human_size((ROOT / src).stat().st_size):>10s}")

    if WX_LOCALE_SRC.is_dir():
        print(f"  локализация wx:        {WX_LOCALE_SRC}")
    else:
        print(f"  ВНИМАНИЕ: локализация wx не найдена: {WX_LOCALE_SRC} (сборка продолжится без неё)")

    for path in ISCC_PATHS:
        if path.is_file():
            print(f"  Inno Setup:            {path}")
            return path
    found = shutil.which("ISCC")
    if found:
        print(f"  Inno Setup:            {found} (через PATH)")
        return Path(found)
    raise ReleaseError("не найден ISCC.exe: установите Inno Setup 6 или добавьте ISCC в PATH")


def run_pyinstaller() -> None:
    # Пути абсолютные: spec-файл лежит в build, и относительные пути PyInstaller считал бы от него.
    command = [
        "uv",
        "run",
        "pyinstaller",
        "--clean",
        "--noconsole",
        "--noconfirm",
        "--onedir",
        f"--specpath={BUILD_DIR}",
        f"--icon={ROOT / 'images' / 'favicon.ico'}",
        *(f"--add-data={ROOT / src};{dest}" for src, dest in BUNDLED_DATA),
        "--name=VC",
        str(ROOT / "main.py"),
    ]
    run_command(command, "PyInstaller")
    if not (APP_DIR / "VC.exe").is_file():
        raise ReleaseError(f"PyInstaller завершился, но {APP_DIR / 'VC.exe'} не найден")


def copy_wx_locale() -> None:
    if not WX_LOCALE_SRC.is_dir():
        print("Локализация wx пропущена: исходная папка не найдена")
        return
    shutil.copytree(WX_LOCALE_SRC, WX_LOCALE_DST, dirs_exist_ok=True)
    print(f"Локализация wx скопирована в {WX_LOCALE_DST.relative_to(ROOT)}")


def update_iss_version(version: str) -> None:
    content = ISS_FILE.read_text(encoding="utf-8")
    match = re.search(r'#define MyAppVersion "([^"]+)"', content)
    if not match:
        raise ReleaseError(f"в {ISS_FILE} не найдено определение MyAppVersion")
    old = match.group(1)
    if old == version:
        print(f"Версия в {ISS_FILE.name} уже {version}")
        return
    content = content.replace(match.group(0), f'#define MyAppVersion "{version}"')
    ISS_FILE.write_text(content, encoding="utf-8")
    print(f"Версия в {ISS_FILE.name}: {old} -> {version}")


def main() -> int:
    # Построчная буферизация: при перенаправлении в файл заголовки шагов идут перед выводом сборщиков.
    sys.stdout.reconfigure(line_buffering=True)
    total_started = time.monotonic()
    steps = Steps(5)
    try:
        steps.next(1, "Проверка")
        version = extract_version(VERSION_FILE)
        print(f"  версия:                {version} (из {VERSION_FILE.relative_to(ROOT)})")
        iscc = check_prerequisites()

        steps.next(2, "PyInstaller")
        run_pyinstaller()

        steps.next(3, "Локализация wxPython")
        copy_wx_locale()

        steps.next(4, "Версия в Inno Setup")
        update_iss_version(version)

        steps.next(5, "Установщик Inno Setup")
        run_command([str(iscc), str(ISS_FILE)], "ISCC")
        installer = DIST_DIR / f"Video_Converter {version} Setup.exe"
        if not installer.is_file():
            raise ReleaseError(f"ISCC завершился, но установщик не найден: {installer}")
        steps.finish()

    except ReleaseError as e:
        print(f"\nОШИБКА: сборка прервана: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nОШИБКА: сборка прервана пользователем", file=sys.stderr)
        return 1

    minutes, seconds = divmod(int(time.monotonic() - total_started), 60)
    print(f"\n=== Выпуск {version} собран за {minutes} мин {seconds} с ===")
    print(f"  сборка:      {APP_DIR}  ({human_size(dir_size(APP_DIR))})")
    print(f"  установщик:  {installer}  ({human_size(installer.stat().st_size)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
