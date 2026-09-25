"""Вспомогательные wx-виджеты: приём перетаскивания и список субтитров с галочками."""
import wx


# --- Drag&Drop класс ---
class FileDropTarget(wx.FileDropTarget):
    def __init__(self, frame):
        super().__init__()
        self.frame = frame

    def OnDropFiles(self, x, y, filenames):
        if filenames:
            self.frame.add_files(filenames)
        return True


class SubtitleCheckPopup(wx.ComboPopup):
    def __init__(self):
        super().__init__()
        self.combo = None
        self.checklist: wx.CheckListBox | None = None

    def Init(self):
        self.checklist = None

    def Create(self, parent):
        self.checklist = wx.CheckListBox(parent, choices=[])
        self.checklist.Bind(wx.EVT_CHECKLISTBOX, self.on_check)
        return True

    def GetControl(self):
        return self.checklist

    def SetStringValue(self, value):
        return

    def GetStringValue(self):
        return self.combo.GetValue() if self.combo else ""

    def GetAdjustedSize(self, min_width, pref_height, max_height):
        height = min(max_height, max(self.combo.FromDIP(80), min(self.combo.FromDIP(220), pref_height))) if self.combo else pref_height
        return wx.Size(max(min_width, self.combo.FromDIP(240) if self.combo else min_width), height)

    def on_check(self, event):
        if self.combo:
            self.combo.update_summary()
        event.Skip()


class SubtitleCheckCombo(wx.ComboCtrl):
    def __init__(self, parent, choices: list[str]):
        super().__init__(parent, style=wx.CB_READONLY)
        self.choices = choices
        self.popup = SubtitleCheckPopup()
        self.SetPopupControl(self.popup)
        self.popup.combo = self
        self.SetValue("Нет субтитров" if not choices else "Не выбраны")
        wx.CallAfter(self.populate_popup)

    def populate_popup(self):
        checklist = self.popup.checklist
        if not checklist:
            return
        checklist.Set(self.choices)
        self.update_summary()

    def GetCheckedItems(self) -> list[int]:
        checklist = self.popup.checklist
        if not checklist:
            return []
        return [i for i in range(checklist.GetCount()) if checklist.IsChecked(i)]

    def SetCheckedItems(self, indexes: list[int]):
        checklist = self.popup.checklist
        if not checklist:
            return
        wanted = set(indexes)
        for i in range(checklist.GetCount()):
            checklist.Check(i, i in wanted)
        self.update_summary()

    def update_summary(self):
        checked = self.GetCheckedItems()
        if not self.choices:
            text = "Нет субтитров"
        elif not checked:
            text = "Не выбраны"
        else:
            text = f"Выбраны: {len(checked)}"
        self.SetValue(text)
