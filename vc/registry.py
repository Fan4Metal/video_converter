"""Хранение настроек приложения в реестре Windows."""
import winreg


def save_reg(name: str, data: str):
    """
    Сохраняет в реестре параметры приложения.
    "save_path" - путь к папке для сохранения файлов.
    """

    soft = winreg.OpenKeyEx(winreg.HKEY_CURRENT_USER, "SOFTWARE")
    key = winreg.CreateKey(soft, "video_converter")
    winreg.SetValueEx(key, name, 0, winreg.REG_SZ, data)
    if key:
        winreg.CloseKey(key)


def get_reg(name):
    reg_path = R"SOFTWARE\video_converter"
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path, 0, winreg.KEY_READ)
        value = winreg.QueryValueEx(key, name)[0]
        winreg.CloseKey(key)
        return value
    except WindowsError:
        return
