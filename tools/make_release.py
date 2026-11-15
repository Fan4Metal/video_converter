"""
Сборка выпуска: PyInstaller (dist\\VC) + установщик Inno Setup (dist\\Video_Converter <версия> Setup.exe).

Запускается из любой папки: uv run tools/make_release.py [--install]
С --install сборка затем тихо устанавливается поверх установленной копии, чтобы её попробовать.
Вывод PyInstaller и ISCC показывается как есть, чтобы был виден ход сборки.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
import winreg
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from vc import __VERSION__
from vc.formats import CONTEXT_MENU_EXTENSIONS, NO_CONTEXT_MENU_EXTENSIONS

ISS_FILE = ROOT / "tools" / "setup.iss"
DIST_DIR = ROOT / "dist"
BUILD_DIR = ROOT / "build"
# Списки расширений для установщика, генерируются из vc/formats.py.
FORMATS_INCLUDE = BUILD_DIR / "formats.iss"
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

# AppId установщика (setup.iss) и где Inno Setup записывает установку.
APP_ID = "{F9F05D79-D5E3-490B-A324-A878E44C23B4}"
UNINSTALL_KEY = Rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{APP_ID}_is1"
# Тихая установка: без мастера, вопросов и перезагрузки; прежние ответы
# (папка, пункт контекстного меню) используются снова.
SILENT_INSTALL = ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"]

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


def version_info_version(version: str) -> str:
    """
    Версия для ресурса VERSIONINFO установщика: от одного до четырёх чисел,
    дополненных нулями до четырёх («0.4.0» → «0.4.0.0»).
    """
    if not re.fullmatch(r"\d+(\.\d+){0,3}", version):
        raise ReleaseError(f"__VERSION__ = {version!r}: нужны от одного до четырёх чисел через точку")
    parts = version.split(".")
    return ".".join(parts + ["0"] * (4 - len(parts)))


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


def iss_array(name: str, extensions: tuple[str, ...]) -> list[str]:
    """Массив препроцессора Inno Setup из расширений (без точки)."""
    lines = [f"#dim {name}[{len(extensions)}]"]
    lines += [f'#define {name}[{i}] "{ext.lstrip(".")}"' for i, ext in enumerate(extensions)]
    return lines


def write_formats_include() -> Path:
    """Пишет списки расширений контекстного меню для setup.iss (см. FormatsInclude)."""
    lines = [
        "; Сгенерировано tools/make_release.py из vc/formats.py, не править вручную.",
        *iss_array("Exts", CONTEXT_MENU_EXTENSIONS),
        *iss_array("NoExts", NO_CONTEXT_MENU_EXTENSIONS),
    ]
    FORMATS_INCLUDE.parent.mkdir(parents=True, exist_ok=True)
    FORMATS_INCLUDE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Контекстное меню: {', '.join(CONTEXT_MENU_EXTENSIONS)}")
    print(f"Без пункта меню:  {', '.join(NO_CONTEXT_MENU_EXTENSIONS) or '—'}")
    return FORMATS_INCLUDE


def installed_folder() -> Path:
    """Папка установленной копии: где её записал Inno Setup, иначе папка по умолчанию (DefaultDirName)."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            return Path(winreg.QueryValueEx(key, "InstallLocation")[0])
    except OSError:
        return Path(os.environ.get("APPDATA", "")) / "video_converter"


def processes_in(folder: Path) -> list[tuple[str, str]]:
    """(PID, имя exe) процессов, запущенных из folder: VC.exe и запущенные им ffmpeg.exe, mpv.exe."""
    query = (
        "Get-Process | Where-Object { $_.Path -and $_.Path.StartsWith($env:VC_FOLDER, 'OrdinalIgnoreCase') }"
        ' | ForEach-Object { "$($_.Id) $($_.Name).exe" }'
    )
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", query],
        capture_output=True,
        text=True,
        env={**os.environ, "VC_FOLDER": str(folder).rstrip("\\") + "\\"},
    )
    return [tuple(line.split(" ", 1)) for line in out.stdout.splitlines() if line.strip()]


def close_running_copy(folder: Path) -> None:
    """
    Запущенные из папки установки exe заблокированы Windows, и установщик не смог бы их заменить.
    Сначала WM_CLOSE (taskkill без /F), чтобы окно закрылось само, через 3 секунды — принудительно
    (так закрываются и ffmpeg/mpv, и приложение, которое во время конвертации спрашивает подтверждение).
    """
    running = processes_in(folder)
    if not running:
        return
    for pid, name in running:
        print(f"  закрываю {name} (PID {pid})")
        subprocess.run(["taskkill", "/PID", pid], capture_output=True)
    deadline = time.monotonic() + 3
    while running and time.monotonic() < deadline:
        time.sleep(0.2)
        running = processes_in(folder)
    if not running:
        return
    for pid, name in running:
        print(f"  принудительно завершаю {name} (PID {pid})")
        subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
    time.sleep(0.5)
    if processes_in(folder):
        raise ReleaseError(f"не удалось закрыть программы из {folder}: закройте Video Converter и повторите")


def main() -> int:
    parser = argparse.ArgumentParser(description="Сборка выпуска Video Converter")
    parser.add_argument("--install", action="store_true", help="затем тихо установить сборку поверх установленной копии")
    args = parser.parse_args()

    # Построчная буферизация: при перенаправлении в файл заголовки шагов идут перед выводом сборщиков.
    sys.stdout.reconfigure(line_buffering=True)
    total_started = time.monotonic()
    steps = Steps(5 + args.install)
    try:
        steps.next(1, "Проверка")
        version = __VERSION__
        file_version = version_info_version(version)
        print(f"  версия:                {version} (из vc/__init__.py), VERSIONINFO {file_version}")
        iscc = check_prerequisites()

        steps.next(2, "PyInstaller")
        run_pyinstaller()

        steps.next(3, "Локализация wxPython")
        copy_wx_locale()

        steps.next(4, "Расширения для Inno Setup")
        formats_include = write_formats_include()

        steps.next(5, "Установщик Inno Setup")
        iscc_defines = [
            f"/DMyAppVersion={version}",
            f"/DVersionInfoVersion={file_version}",
            f"/DFormatsInclude={formats_include}",
        ]
        run_command([str(iscc), *iscc_defines, str(ISS_FILE)], "ISCC")
        installer = DIST_DIR / f"Video_Converter {version} Setup.exe"
        if not installer.is_file():
            raise ReleaseError(f"ISCC завершился, но установщик не найден: {installer}")

        if args.install:
            steps.next(6, "Тихая установка")
            folder = installed_folder()
            installed = folder / "VC.exe"
            # Закрываем сами, а не установщиком (/CLOSEAPPLICATIONS — запасной вариант),
            # чтобы закрыть и ffmpeg/mpv, запущенные приложением.
            close_running_copy(folder)
            run_command([str(installer), *SILENT_INSTALL], "Установка")
            if not installed.is_file():
                raise ReleaseError(f"установщик завершился, но {installed} не найден")
            print(f"Установлено: {installed}")
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
    if args.install:
        print(f"  установлено: {installed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
