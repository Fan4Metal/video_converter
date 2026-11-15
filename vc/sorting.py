"""Сортировка списка файлов по клику на заголовок столбца."""
import os
import re

import wx

from vc.file_row import FileRow


class SortingMixin:
    """
    Примесь к VideoConverter. Перестраивает список целиком: снимает состояние
    строк, сортирует и пересоздаёт встроенные виджеты.
    """
    def on_col_click(self, event):
        col = event.GetColumn()
        if col is None or col < 0:
            return
        if col in (self.COL_SETTINGS, self.COL_PROGRESS):
            return
        if self.converting:
            return
        if self.list.GetItemCount() < 2:
            return

        # Повторный клик по тому же столбцу меняет направление сортировки.
        if self._sort_col == col:
            self._sort_ascending = not self._sort_ascending
        else:
            self._sort_col = col
            self._sort_ascending = True

        self.sort_rows(col, self._sort_ascending)
        self._update_sort_indicator()

    @staticmethod
    def _to_number(text, default=0.0) -> float:
        """Извлекает первое число из строки (для сортировки числовых столбцов)."""
        m = re.search(r"-?\d+(?:[.,]\d+)?", str(text))
        if not m:
            return default
        try:
            return float(m.group(0).replace(",", "."))
        except ValueError:
            return default

    def _row_sort_key(self, s: dict, col: int):
        """Ключ сортировки для снимка строки по выбранному столбцу."""
        entry: FileRow = s["entry"]
        info = entry.info or {}
        if col == self.COL_RES:
            return self._to_number(info.get("width"), 0) * self._to_number(info.get("height"), 0)
        if col == self.COL_BR:
            return self._to_number(info.get("bitrate"), 0)
        if col == self.COL_SIZE:
            return float(info.get("size") or 0)
        if col == self.COL_EST:
            return float(entry.est_bytes or 0)
        if col == self.COL_TIME:
            return float(entry.duration or 0.0)
        if col == self.COL_SUBTITLES:
            return float(len(entry.subtitle_tracks or []))
        if col == self.COL_AUDIO:
            sel = s["audio_sel"]
            choices = entry.audio_choices or []
            return (choices[sel] if 0 <= sel < len(choices) else "").lower()
        if col == self.COL_STATUS:
            return s["col_status"].lower()
        # COL_FILE и всё остальное — по имени файла
        return os.path.basename(entry.path or "").lower()

    def sort_rows(self, col: int, ascending: bool):
        snaps = [self._snapshot_row(r) for r in range(self.list.GetItemCount())]
        snaps.sort(key=lambda s: self._row_sort_key(s, col), reverse=not ascending)
        self._rebuild_rows(snaps)

    def _snapshot_row(self, row: int) -> dict:
        """
        Снимок строки: сама строка (FileRow переносится как есть) плюс то, что
        живёт только в списке и виджетах — тексты столбцов и выбор дорожек.
        """
        uid = self.row_order[row]
        entry = self.entries[uid]
        return {
            "uid": uid,
            "entry": entry,
            "audio_sel": entry.choice.GetSelection() if entry.choice else wx.NOT_FOUND,
            "audio_multi_checked": entry.audio_multi.GetCheckedItems() if entry.audio_multi else None,
            "subtitle_checked": entry.subtitles.GetCheckedItems() if entry.subtitles else None,
            "gauge_value": entry.gauge.GetValue() if entry.gauge else 0,
            "col_file": self.list.GetItem(row, self.COL_FILE).GetText(),
            "col_res": self.list.GetItem(row, self.COL_RES).GetText(),
            "col_res_tooltip": self.list.GetItem(row, self.COL_RES).GetToolTip(),
            "col_br": self.list.GetItem(row, self.COL_BR).GetText(),
            "col_size": self.list.GetItem(row, self.COL_SIZE).GetText(),
            "col_est": self.list.GetItem(row, self.COL_EST).GetText(),
            "col_time": self.list.GetItem(row, self.COL_TIME).GetText(),
            "col_status": self.list.GetItem(row, self.COL_STATUS).GetText(),
            "col_settings": self.list.GetItem(row, self.COL_SETTINGS).GetText(),
        }

    def _rebuild_rows(self, snaps: list[dict]):
        """Перестраивает список в порядке snaps, пересоздавая встроенные виджеты."""
        # Уничтожаем старые виджеты
        for entry in self.entries.values():
            entry.destroy_windows()

        self.list.DeleteAllItems()
        self.entries.clear()
        self.row_order.clear()

        for s in snaps:
            row = self.list.GetItemCount()
            self.list.InsertStringItem(row, s["col_file"])
            self.list.SetStringItem(row, self.COL_RES, s["col_res"])
            self._set_cell_tooltip(row, self.COL_RES, s["col_res_tooltip"] or "")
            self.list.SetStringItem(row, self.COL_BR, s["col_br"])
            self.list.SetStringItem(row, self.COL_TIME, s["col_time"])
            self.list.SetStringItem(row, self.COL_SIZE, s["col_size"])
            self.list.SetStringItem(row, self.COL_EST, s["col_est"])
            self.list.SetStringItem(row, self.COL_STATUS, s["col_status"])
            self.list.SetStringItem(row, self.COL_SETTINGS, s["col_settings"])

            entry: FileRow = s["entry"]
            choice = wx.Choice(self.list, choices=entry.audio_choices)
            sel = s["audio_sel"]
            if sel != wx.NOT_FOUND and 0 <= sel < choice.GetCount():
                choice.SetSelection(sel)
            choice.Bind(wx.EVT_CHOICE, self.on_audio_choice)
            self.list.SetItemWindow(row, self.COL_AUDIO, choice, expand=True)

            gauge = wx.Gauge(self.list, range=100, size=self.FromDIP(wx.Size(-1, 18)), style=wx.GA_HORIZONTAL)
            gauge.SetValue(int(s["gauge_value"] or 0))
            self.list.SetItemWindow(row, self.COL_PROGRESS, gauge, expand=True)

            # Строка та же, меняются только её виджеты (необязательные
            # столбцы destroy_windows уже обнулил).
            entry.choice = choice
            entry.gauge = gauge
            uid = s["uid"]
            self.row_order.append(uid)
            self.entries[uid] = entry
            if s["audio_multi_checked"] is not None:
                self.create_audio_multi_widget(row)
                if entry.audio_multi is not None:
                    entry.audio_multi.SetCheckedItems(s["audio_multi_checked"])
            if s["subtitle_checked"] is not None:
                self.create_subtitle_widget(row)
                if entry.subtitles is not None:
                    entry.subtitles.SetCheckedItems(s["subtitle_checked"])

        self._reindex_item_windows()
        self._schedule_progress_fit()

    def _update_sort_indicator(self):
        """Обновляет заголовки столбцов: добавляет стрелку у активного столбца."""
        for col, label in self.COL_LABELS.items():
            if not self.list.IsColumnShown(col):
                continue
            arrow = ""
            if col == self._sort_col:
                arrow = "  ▲" if self._sort_ascending else "  ▼"
            width = self.list.GetColumnWidth(col)
            ci = self.list.GetColumn(col)
            ci.SetText(label + arrow)
            self.list.SetColumn(col, ci)
            # SetColumn может сбросить ширину — восстанавливаем её.
            self.list.SetColumnWidth(col, width)
