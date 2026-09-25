"""Выделение строк списка рамкой при протягивании мыши."""
import ctypes

import wx


class MarqueeSelectionMixin:
    """
    Примесь к VideoConverter. Использует self.list (UltimateListCtrl) и
    self._selected_rows().
    """
    def _init_marquee_selection(self):
        """
        Выделение строк рамкой при протягивании мыши. UltimateListCtrl этого не
        умеет, поэтому обрабатываем мышь главного окна списка сами: координаты
        считаем в «непрокрученной» системе, чтобы колесо во время протягивания
        не ломало прямоугольник. Сама рамка — отдельное полупрозрачное дочернее
        окно поверх списка: оно только двигается и меняет размер, список под ним
        не перерисовывается, поэтому не мерцает. Рисовать рамку через wx.Overlay
        или в обработчике отрисовки списка нельзя: в первом случае затирается
        подсветка строк, во втором между кадрами проскакивает список без рамки.
        """
        self._marquee_win = self.list._mainWin
        self._marquee_start: wx.Point | None = None  # непрокрученные координаты нажатия
        self._marquee_active = False
        self._marquee_base: set[int] = set()  # выделение до начала рамки (при Ctrl)
        self._marquee_box = wx.Window(self._marquee_win, style=wx.BORDER_NONE)
        self._marquee_box.SetBackgroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_HIGHLIGHT))
        self._marquee_box.Hide()
        self._make_window_translucent(self._marquee_box, alpha=70)
        self._marquee_win.Bind(wx.EVT_LEFT_DOWN, self._on_marquee_left_down)
        self._marquee_win.Bind(wx.EVT_MOTION, self._on_marquee_motion)
        self._marquee_win.Bind(wx.EVT_LEFT_UP, self._on_marquee_left_up)
        self._marquee_win.Bind(wx.EVT_MOUSE_CAPTURE_LOST, self._on_marquee_capture_lost)

    def _on_marquee_left_down(self, event: wx.MouseEvent):
        x, y = event.GetPosition()
        self._marquee_start = wx.Point(*self._marquee_win.CalcUnscrolledPosition(x, y))
        self._marquee_active = False
        self._marquee_base = set(self._selected_rows()) if event.ControlDown() else set()
        event.Skip()  # обычный клик списка: выбор строки или сброс выделения

    def _on_marquee_motion(self, event: wx.MouseEvent):
        if self._marquee_start is None or not event.LeftIsDown():
            event.Skip()
            return
        x, y = event.GetPosition()
        cur = wx.Point(*self._marquee_win.CalcUnscrolledPosition(x, y))
        if not self._marquee_active:
            if abs(cur.x - self._marquee_start.x) < 4 and abs(cur.y - self._marquee_start.y) < 4:
                event.Skip()
                return
            self._marquee_active = True
            if not self._marquee_win.HasCapture():
                self._marquee_win.CaptureMouse()
        rect = wx.Rect(
            min(self._marquee_start.x, cur.x),
            min(self._marquee_start.y, cur.y),
            max(abs(cur.x - self._marquee_start.x), 1),
            max(abs(cur.y - self._marquee_start.y), 1),
        )
        self._marquee_apply(rect)
        self._marquee_set_rect(rect)

    def _marquee_apply(self, rect: wx.Rect):
        """Выделяет строки, пересекающие прямоугольник (непрокрученные координаты)."""
        wanted = set(self._marquee_base)
        for row in range(self.list.GetItemCount()):
            if self._marquee_win.GetLineRect(row).Intersects(rect):
                wanted.add(row)
        for row in range(self.list.GetItemCount()):
            selected = self.list.IsSelected(row)
            if selected != (row in wanted):
                self.list.Select(row, row in wanted)

    def _marquee_scrolled_rect(self, rect: wx.Rect) -> wx.Rect:
        x, y = self._marquee_win.CalcScrolledPosition(rect.x, rect.y)
        return wx.Rect(x, y, rect.width, rect.height)

    @staticmethod
    def _make_window_translucent(window: wx.Window, alpha: int):
        """Делает дочернее окно полупрозрачным (WS_EX_LAYERED, Windows 8+)."""
        try:
            user32 = ctypes.windll.user32
            hwnd = window.GetHandle()
            GWL_EXSTYLE, WS_EX_LAYERED, LWA_ALPHA = -20, 0x80000, 0x2
            style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED)
            user32.SetLayeredWindowAttributes(hwnd, 0, alpha, LWA_ALPHA)
        except Exception:
            pass  # останется непрозрачная рамка

    def _marquee_set_rect(self, rect: wx.Rect | None):
        """Показывает рамку в заданном прямоугольнике или прячет её."""
        if rect is None:
            self._marquee_box.Hide()
            return
        self._marquee_box.SetRect(self._marquee_scrolled_rect(rect))
        if not self._marquee_box.IsShown():
            self._marquee_box.Show()
        self._marquee_box.Raise()  # поверх виджетов строк, созданных позже

    def _marquee_finish(self):
        was_active = self._marquee_active
        self._marquee_start = None
        self._marquee_active = False
        if was_active:
            self._marquee_set_rect(None)
            if self._marquee_win.HasCapture():
                self._marquee_win.ReleaseMouse()
            # Сбрасываем внутреннее состояние клика ULC: отпускание кнопки к нему не дойдёт.
            self._marquee_win._lineSelectSingleOnUp = -1
            self._marquee_win._dragCount = 0
        return was_active

    def _on_marquee_left_up(self, event: wx.MouseEvent):
        if not self._marquee_finish():
            event.Skip()

    def _on_marquee_capture_lost(self, event):
        self._marquee_start = None
        self._marquee_active = False
        self._marquee_set_rect(None)
