"""Контекстное меню списка файлов и воспроизведение в mpv."""
import os
import subprocess

import wx

from vc.resources import MPV_PATH
from vc.widgets import CheckListCombo


class ContextMenuMixin:
    """Примесь к VideoConverter: правый клик по строке и действия из меню."""
    def on_play_file(self, event):
        row = self.list.GetFirstSelected()
        if row == -1:
            return
        widgets = self._widgets_at(row)
        if not widgets:
            return
        path = widgets.get("path")
        if os.path.isfile(path):
            audio_stream_num = widgets.get("choice").GetSelection() + 1
            subprocess.Popen(
                [
                    MPV_PATH,
                    path,
                    f"--aid={str(audio_stream_num)}",
                    (
                        "--title=${filename} — [Аудио #${current-tracks/audio/id}]:"
                        "${current-tracks/audio/title:Без названия} (${current-tracks/audio/lang:-}), "
                        "каналов: ${current-tracks/audio/audio-channels:?}, кодек: ${current-tracks/audio/codec}"
                    ),
                    "--no-sub",
                    "--no-config",
                ],
            )

    def on_right_click(self, event):
        """Контекстное меню по правому клику"""
        # Получаем индекс строки из события
        item = event.GetIndex()

        # Показываем меню только если клик был на строке
        if item == wx.NOT_FOUND or item == -1:
            return

        # Если кликнутая строка ещё не выделена — снимаем выделение с остальных
        # и выделяем только её (иначе остаётся несколько выделенных строк).
        if not self.list.IsSelected(item):
            selected = []
            s = self.list.GetFirstSelected()
            while s != -1:
                selected.append(s)
                s = self.list.GetNextSelected(s)
            for s in selected:
                self.list.Select(s, False)
            self.list.Select(item)

        # Создаем контекстное меню
        menu = wx.Menu()

        # Пункты меню
        play_item = menu.Append(wx.ID_ANY, "▶ Воспроизвести")
        play_converted_item = menu.Append(wx.ID_ANY, "▶ Воспроизвести сконвертированный файл")
        widgets = self._widgets_at(item)
        if not widgets:
            menu.Destroy()
            return
        if self.list.GetItem(item, self.COL_STATUS).GetText() == "✅ Готово" and "output_file" in widgets:
            play_converted_item.Enable()
        else:
            play_converted_item.Enable(False)
        menu.AppendSeparator()

        apply_item = menu.Append(wx.ID_ANY, "↪ Применить для других файлов")
        apply_item.Enable(not self.converting and self.list.GetItemCount() > 1)
        self.Bind(wx.EVT_MENU, lambda e: wx.CallAfter(self.apply_to_other_files, item), apply_item)
        menu.AppendSeparator()

        probe_item = menu.Append(wx.ID_ANY, "🎯 Оценить размер контрольной кодировкой")
        probe_all_item = menu.Append(wx.ID_ANY, "🎯 Оценить размер всех файлов контрольной кодировкой")
        can_probe = not self.converting and not self.probing
        probe_item.Enable(can_probe)
        probe_all_item.Enable(can_probe)
        self.Bind(wx.EVT_MENU, lambda e: wx.CallAfter(self.start_probe, self._selected_rows()), probe_item)
        self.Bind(wx.EVT_MENU, lambda e: wx.CallAfter(self.start_probe, list(range(self.list.GetItemCount()))), probe_all_item)
        menu.AppendSeparator()

        status = self.list.GetItem(item, self.COL_STATUS).GetText()
        if self.converting and widgets.get("skip") and item > self._queue_row:
            unskip_item = menu.Append(wx.ID_ANY, "↩ Вернуть в очередь")
            self.Bind(wx.EVT_MENU, lambda e: wx.CallAfter(self.unskip_rows, self._selected_rows() or [item]), unskip_item)
        else:
            skip_item = menu.Append(wx.ID_ANY, "⏭ Пропустить")
            can_skip = self.converting and (status == "Ожидает" or "Конвертация" in status)
            skip_item.Enable(can_skip)
            self.Bind(wx.EVT_MENU, lambda e: wx.CallAfter(self.skip_rows, self._selected_rows() or [item]), skip_item)
        menu.AppendSeparator()

        settings_obj = widgets.get("settings")
        if settings_obj and not settings_obj.is_global:
            reset_convert_settings_item = menu.Append(wx.ID_ANY, "🔄 Сбросить настройки конвертации")
            menu.AppendSeparator()
            self.Bind(wx.EVT_MENU, lambda e: wx.CallAfter(self.reset_convert_settings, e), reset_convert_settings_item)

        open_folder_item = menu.Append(wx.ID_ANY, "📁 Открыть папку с файлом")
        open_output_folder_item = menu.Append(wx.ID_ANY, "📂 Открыть папку вывода")
        menu.AppendSeparator()

        remove_item = menu.Append(wx.ID_ANY, "🗑 Удалить из списка")
        clear_item = menu.Append(wx.ID_ANY, "🧹 Очистить весь список")

        # Отключаем пункты, если идет конвертация
        if self.converting:
            remove_item.Enable(False)
            clear_item.Enable(False)

        # Привязываем обработчики
        self.Bind(wx.EVT_MENU, lambda e: self.on_play_file(e), play_item)
        self.Bind(wx.EVT_MENU, lambda e: self.on_context_open_folder(e), open_folder_item)
        self.Bind(wx.EVT_MENU, lambda e: self.on_context_open_output_folder(e), open_output_folder_item)
        self.Bind(wx.EVT_MENU, lambda e: wx.CallAfter(self.on_remove_selected, e), remove_item)
        self.Bind(wx.EVT_MENU, lambda e: wx.CallAfter(self.on_clear, e), clear_item)
        self.Bind(wx.EVT_MENU, lambda e: wx.CallAfter(self.on_context_play_converted, e), play_converted_item)

        # Показываем меню в позиции курсора
        self.list.PopupMenu(menu)
        menu.Destroy()

    def on_context_open_folder(self, event):
        """Открыть папку с исходным файлом"""
        row = self.list.GetFirstSelected()
        if row == -1:
            return

        widgets = self._widgets_at(row)
        if not widgets:
            return

        path = widgets.get("path")
        if path and os.path.isfile(path):
            subprocess.Popen(f'explorer /select,"{path}"')

    def on_context_open_output_folder(self, event):
        """Открыть папку вывода"""
        row = self.list.GetFirstSelected()
        if row == -1:
            return
        widgets = self._widgets_at(row)
        if not widgets:
            return
        path = widgets.get("output_file", "")
        if path and os.path.isfile(path):
            subprocess.Popen(f'explorer /select,"{path}"')

    def on_context_play_converted(self, event):
        row = self.list.GetFirstSelected()
        if row == -1:
            return
        widgets = self._widgets_at(row)
        if not widgets:
            return
        if self.list.GetItem(row, self.COL_STATUS).GetText() == "✅ Готово" and "output_file" in widgets:
            output_file = widgets.get("output_file")
            if os.path.isfile(output_file):
                subprocess.Popen(
                    [
                        MPV_PATH,
                        output_file,
                        "--title=Сконвертированный файл: ${filename}",
                        "--no-sub",
                        "--no-config",
                    ],
                )
                return

    def apply_to_other_files(self, source_row: int):
        """
        Применяет к остальным файлам в списке те же настройки выбора дорожек:
        - ту же аудио дорожку по её порядковому номеру (если у файла она есть);
        - если включена опция «несколько аудио дорожек» — тот же набор дорожек по номерам;
        - если включена опция «сохранить субтитры» — те же субтитры по номеру.
        """
        if self.converting:
            return

        source = self._widgets_at(source_row)
        if not source:
            return

        source_choice: wx.Choice | None = source.get("choice")
        audio_index = source_choice.GetSelection() if source_choice else wx.NOT_FOUND

        multi_audio = self.chk_multi_audio.GetValue()
        source_multi: CheckListCombo | None = source.get("audio_multi")
        multi_indexes = source_multi.GetCheckedItems() if (multi_audio and source_multi) else []

        save_subtitles = self.chk_save_subtitles.GetValue()
        source_subtitles: CheckListCombo | None = source.get("subtitles")
        subtitle_indexes = source_subtitles.GetCheckedItems() if (save_subtitles and source_subtitles) else []

        applied = 0
        for row in range(self.list.GetItemCount()):
            if row == source_row:
                continue
            widgets = self._widgets_at(row)
            if not widgets:
                continue

            # Аудио дорожка по номеру
            choice: wx.Choice | None = widgets.get("choice")
            if choice and audio_index != wx.NOT_FOUND and audio_index < choice.GetCount():
                choice.SetSelection(audio_index)

            # Набор аудиодорожек по номерам
            if multi_audio:
                self.create_audio_multi_widget(row)
                multi: CheckListCombo | None = widgets.get("audio_multi")
                if multi:
                    track_count = len(widgets.get("audio_choices") or [])
                    multi.SetCheckedItems([i for i in multi_indexes if i < track_count])

            # Субтитры по номеру
            if save_subtitles:
                self.create_subtitle_widget(row)
                subtitles: CheckListCombo | None = widgets.get("subtitles")
                if subtitles:
                    track_count = len(widgets.get("subtitle_tracks") or [])
                    subtitles.SetCheckedItems([i for i in subtitle_indexes if i < track_count])

            applied += 1

        self.refresh_all_estimates()
        self.log.AppendText(f"\n↪ Настройки дорожек применены к остальным файлам ({applied}).\n")
