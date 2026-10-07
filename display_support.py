"""Windows DPI setup and logical-pixel layout helpers for the desktop UI."""
from __future__ import annotations

import ctypes
import sys
import tkinter as tk
from tkinter import ttk


def enable_dpi_awareness():
    # Run before the first Tk window; otherwise Windows may stretch a bitmap.
    if sys.platform != 'win32':
        return
    try:
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        setter = user32.SetProcessDpiAwarenessContext
        setter.argtypes = [ctypes.c_void_p]
        setter.restype = ctypes.c_bool
        if setter(ctypes.c_void_p(-4)):
            return
        if ctypes.get_last_error() == 5:
            return  # The host/manifest already selected a DPI awareness mode.
    except (AttributeError, OSError):
        pass
    try:
        setter = ctypes.windll.shcore.SetProcessDpiAwareness
        setter.argtypes = [ctypes.c_int]
        setter.restype = ctypes.c_long
        if setter(2) in (0, -2147024891):
            return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


def configure_root(app):
    dpi = float(app.winfo_fpixels('1i'))
    if sys.platform == 'win32':
        try:
            getter = ctypes.windll.user32.GetDpiForWindow
            getter.argtypes = [ctypes.c_void_p]
            getter.restype = ctypes.c_uint
            native_dpi = getter(app.winfo_id())
            if native_dpi:
                dpi = float(native_dpi)
        except (AttributeError, OSError):
            pass
    app._ui_dpi = dpi
    app._ui_scale = dpi / 96.0
    # Font sizes are points, so let Tk rasterize glyphs at the native DPI.
    app.tk.call('tk', 'scaling', dpi / 72.0)


def px(widget, value):
    factor = getattr(widget._root(), '_ui_scale', 1.0)
    if isinstance(value, (tuple, list)):
        return tuple(px(widget, item) for item in value)
    return round(float(str(value)) * factor)


def size_window(window, width, height, min_width, min_height):
    margin = px(window, 80)
    available_width = max(640, window.winfo_screenwidth() - margin)
    available_height = max(480, window.winfo_screenheight() - px(window, 120))
    width = min(px(window, width), available_width)
    height = min(px(window, height), available_height)
    window.geometry(f'{width}x{height}')
    window.minsize(min(px(window, min_width), available_width),
                   min(px(window, min_height), available_height))


def _scale_distance(widget, value):
    if isinstance(value, (tuple, list)):
        return tuple(px(widget, item) for item in value)
    parts = str(value).split()
    if len(parts) > 1:
        return tuple(px(widget, item) for item in parts)
    return px(widget, value)


def scale_widgets(parent):
    # Only pixel dimensions are converted; text widths and font points stay intact.
    for widget in parent.winfo_children():
        if not getattr(widget, '_ui_pixels_scaled', False):
            options = ['padx', 'pady']
            if isinstance(widget, (tk.Frame, tk.Canvas)):
                options.extend(['width', 'height'])
            if isinstance(widget, tk.Text):
                options.extend(['spacing1', 'spacing2', 'spacing3'])
            supported = widget.keys()
            changes = {key: _scale_distance(widget, widget.cget(key))
                       for key in options if key in supported}
            if changes:
                widget.configure(**changes)
            manager = widget.winfo_manager()
            if manager in ('pack', 'grid'):
                info = widget.pack_info() if manager == 'pack' else widget.grid_info()
                changes = {key: _scale_distance(widget, info[key])
                           for key in ('padx', 'pady', 'ipadx', 'ipady') if key in info}
                if manager == 'pack':
                    widget.pack_configure(**changes)
                else:
                    widget.grid_configure(**changes)
            elif manager == 'place':
                info = widget.place_info()
                changes = {key: _scale_distance(widget, info[key])
                           for key in ('x', 'y', 'width', 'height') if info.get(key) not in (None, '')}
                widget.place_configure(**changes)
            if isinstance(widget, tk.Canvas):
                factor = getattr(widget._root(), '_ui_scale', 1.0)
                widget.scale('all', 0, 0, factor, factor)
            if isinstance(widget, ttk.Treeview):
                for column in widget['columns']:
                    info = widget.column(column)
                    widget.column(column, width=px(widget, info['width']), minwidth=px(widget, info['minwidth']))
            widget._ui_pixels_scaled = True
        scale_widgets(widget)


def scale_styles(app):
    style = app._ui_style
    for name, option in [('TButton', 'padding'), ('TEntry', 'padding'),
                         ('Treeview', 'rowheight'), ('Treeview.Heading', 'padding'),
                         ('Vertical.TScrollbar', 'arrowsize')]:
        style.configure(name, **{option: _scale_distance(app, style.lookup(name, option))})
