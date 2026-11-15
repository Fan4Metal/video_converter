"""
Поддерживаемые форматы файлов. Модуль без зависимостей: его импортирует и
tools/make_release.py, чтобы прописать в установщике пункт контекстного меню.
"""

# Входные форматы: контейнеры, которые читает ffmpeg из поставки и которые
# встречаются как обычные видеофайлы. Порядок — для диалога и описания.
INPUT_EXTENSIONS = (
    ".mkv", ".mp4", ".m4v", ".mov", ".avi", ".wmv", ".flv", ".webm",
    ".ts", ".m2ts", ".mts", ".mpg", ".mpeg", ".vob", ".3gp", ".ogv", ".mxf",
)

# Расширения, которым не нужен пункт «Сконвертировать» в Проводнике:
# .ts — ещё и расширение исходников TypeScript.
NO_CONTEXT_MENU_EXTENSIONS = (".ts",)

# Расширения с пунктом «Сконвертировать» в контекстном меню Проводника.
CONTEXT_MENU_EXTENSIONS = tuple(ext for ext in INPUT_EXTENSIONS if ext not in NO_CONTEXT_MENU_EXTENSIONS)

# Контейнеры семейства MP4, теги которых копируются через mutagen.
MP4_TAG_EXTENSIONS = (".mp4", ".m4v")


def input_formats_text() -> str:
    """Список входных форматов для пользователя: «MKV, MP4, …»."""
    return ", ".join(ext[1:].upper() for ext in INPUT_EXTENSIONS)


def input_files_wildcard() -> str:
    """Фильтр для wx.FileDialog: видеофайлы и все файлы."""
    pattern = ";".join(f"*{ext}" for ext in INPUT_EXTENSIONS)
    return f"Видео файлы ({input_formats_text()})|{pattern}|Все файлы (*.*)|*.*"
