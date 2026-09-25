"""Вспомогательные wx-виджеты: приём перетаскивания и выпадающий список с галочками."""

from collections.abc import Callable

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


class CheckListPopup(wx.ComboPopup):
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
            if self.combo.on_change:
                self.combo.on_change()
        event.Skip()


class CheckListCombo(wx.ComboCtrl):
    """
    Выпадающий список с галочками (дорожки субтитров, дополнительные аудиодорожки).
    В свёрнутом виде показывает сводку: empty_label (нет вариантов), none_label
    (ничего не отмечено) или «Выбраны: N».
    on_change вызывается после каждого изменения галочек пользователем.
    """

    def __init__(
        self,
        parent,
        choices: list[str],
        empty_label: str = "Нет субтитров",
        none_label: str = "Не выбраны",
        on_change: Callable[[], None] | None = None,
    ):
        super().__init__(parent, style=wx.CB_READONLY)
        self.choices = choices
        self.empty_label = empty_label
        self.none_label = none_label
        self.on_change = on_change
        self.popup = CheckListPopup()
        self.SetPopupControl(self.popup)
        self.popup.combo = self
        self.SetValue(empty_label if not choices else none_label)
        self.populate_popup()

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
            text = self.empty_label
        elif not checked:
            text = self.none_label
        else:
            text = f"Выбраны: {len(checked)}"
        self.SetValue(text)
