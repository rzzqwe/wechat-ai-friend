"""Smooth, viewport-scoped mouse-wheel handling for the desktop workbench."""
from __future__ import annotations

import math
import time
import tkinter as tk


class SmoothScroll:
    _FRAME_MS = 8
    _MIN_DURATION = 90.0
    _MAX_DURATION = 240.0

    def __init__(self, canvas, body, scrollbar, *, nested=None, pixels_per_notch=48):
        self.canvas = canvas
        self.body = body
        self.root = canvas.winfo_toplevel()
        self.nested = nested or {}
        self.pixels_per_notch = pixels_per_notch
        self._job = None
        self._target = None
        self._animation_start = None
        self._animation_started_at = None
        self._animation_duration = 0.0
        self._last_position = None
        self._pixel_remainder = 0.0
        self._nested_remainders = {}
        self._nested_jobs = {}
        self._nested_queues = {}
        self._size = None
        self._closed = False
        self._tag = f'WorkbenchWheel{id(self)}'
        self._bindings = {}
        canvas.configure(yscrollincrement=1)
        # Intercept before Text/Treeview class bindings swallow the event.
        for sequence in ('<MouseWheel>', '<Button-4>', '<Button-5>'):
            self._bindings[sequence] = self.root.bind_class(self._tag, sequence, self.on_wheel)
        self._bindings['<ButtonPress-1>'] = self.root.bind_class(self._tag, '<ButtonPress-1>', self.stop)
        self._attach(canvas)
        self._attach(scrollbar)
        body.bind('<Configure>', self.update_region, add='+')
        canvas.bind('<Configure>', self.stop, add='+')
        canvas.bind('<Destroy>', self.close, add='+')

    def _attach(self, widget):
        widget.bindtags((self._tag, *widget.bindtags()))
        for child in widget.winfo_children():
            self._attach(child)

    def update_region(self, event):
        size = (event.width, event.height)
        # Moving the embedded frame also emits Configure: do no layout work then.
        if size != self._size:
            self._size = size
            self.stop()
            self.canvas.configure(scrollregion=(0, 0, *size))

    def stop(self, event=None):
        if self._job is not None:
            self.canvas.after_cancel(self._job)
        self._job = None
        self._target = None
        self._animation_start = None
        self._animation_started_at = None
        self._animation_duration = 0.0
        self._last_position = None
        self._pixel_remainder = 0.0

    def _position(self):
        height = max(1, self.body.winfo_height())
        maximum = max(0, height - self.canvas.winfo_height())
        return self.canvas.yview()[0] * height, maximum, height

    def on_wheel(self, event):
        if self._closed or getattr(event, 'state', 0) & 1:
            return None  # Leave Shift+wheel horizontal scrolling to the widget.
        button = getattr(event, 'num', None)
        notches = -1.0 if button == 4 else 1.0 if button == 5 else -float(event.delta) / 120.0
        if not notches:
            return None
        nested = self.nested.get(event.widget)
        if nested is not None:
            first, last = nested.yview()
            if (notches < 0 and first > 0) or (notches > 0 and last < 1):
                self.stop()
                remainder = self._nested_remainders.get(nested, 0.0)
                if remainder * notches < 0:
                    remainder = 0.0
                lines = remainder + notches * 3
                whole = math.trunc(lines)
                self._nested_remainders[nested] = lines - whole
                if whole:
                    self._animate_nested(nested, whole)
                return 'break'
            self._nested_remainders.pop(nested, None)
        self.scroll_pixels(notches * self.pixels_per_notch)
        return 'break'

    def _animate_nested(self, widget, units):
        """Apply nested-widget wheel units over a few frames.

        Treeview and Text do not expose pixel scrolling through their normal
        wheel bindings. Applying one unit per frame keeps those controls from
        jumping several rows/lines at once while still allowing a fast wheel
        gesture to build up a queue.
        """
        queue = self._nested_queues.get(widget, 0) + units
        # A long wheel burst should not leave work running long after the
        # pointer stops. The normal three units per notch are retained.
        self._nested_queues[widget] = max(-24, min(24, queue))
        if widget not in self._nested_jobs:
            self._nested_jobs[widget] = widget.after(self._FRAME_MS,
                                                     lambda w=widget: self._step_nested(w))

    def _step_nested(self, widget):
        self._nested_jobs.pop(widget, None)
        units = self._nested_queues.get(widget, 0)
        if not units:
            self._nested_queues.pop(widget, None)
            return
        step = 1 if units > 0 else -1
        try:
            widget.yview_scroll(step, 'units')
        except tk.TclError:
            self._nested_queues.pop(widget, None)
            return
        units -= step
        if units:
            self._nested_queues[widget] = units
            self._nested_jobs[widget] = widget.after(self._FRAME_MS,
                                                     lambda w=widget: self._step_nested(w))
        else:
            self._nested_queues.pop(widget, None)

    def scroll_pixels(self, distance):
        position, maximum, _ = self._position()
        if maximum == 0:
            self.stop()
            self._pixel_remainder = 0.0
            return
        if self._target is not None and (self._target - position) * distance < 0:
            self.stop()  # Reverse immediately instead of finishing the old direction.
        if self._pixel_remainder * distance < 0:
            self._pixel_remainder = 0.0
        requested = distance + self._pixel_remainder
        whole = math.trunc(requested)
        self._pixel_remainder = requested - whole
        if not whole:
            return
        origin = position if self._target is None else self._target
        self._target = min(maximum, max(0, origin + whole))
        if self._target == position:
            self.stop()
            return
        if self._job is None:
            self._animation_start = position
            self._animation_started_at = time.monotonic()
            self._animation_duration = self._duration(abs(self._target - position))
            self._last_position = position
            self._job = self.canvas.after(0, self._step)
        else:
            # Extend the current animation when more wheel input arrives.
            # Keeping its start point avoids a visible discontinuity, while a
            # longer target gets enough time to remain smooth.
            if self._animation_start is not None:
                distance_from_start = abs(self._target - self._animation_start)
                self._animation_duration = min(
                    self._MAX_DURATION,
                    max(self._animation_duration, self._duration(distance_from_start)),
                )

    def _duration(self, distance):
        # A single wheel notch takes about 140 ms; larger bursts ease out over
        # a little longer but remain responsive instead of feeling sluggish.
        return min(self._MAX_DURATION, max(self._MIN_DURATION, 80.0 + distance * 1.25))

    def _step(self):
        self._job = None
        if self._closed or self._target is None:
            return
        position, maximum, height = self._position()
        if self._last_position is not None and abs(position - self._last_position) > 2:
            self.stop()  # Respect scrollbar drags and programmatic navigation.
            return
        self._target = min(maximum, self._target)
        if self._animation_start is None or self._animation_started_at is None:
            self._animation_start = position
            self._animation_started_at = time.monotonic()
            self._animation_duration = self._duration(abs(self._target - position))
        elapsed = (time.monotonic() - self._animation_started_at) * 1000.0
        progress = min(1.0, max(0.0, elapsed / max(1.0, self._animation_duration)))
        # Cubic ease-out: responsive at the beginning, with no abrupt stop.
        eased = 1.0 - (1.0 - progress) ** 3
        desired = self._animation_start + (self._target - self._animation_start) * eased
        if progress >= 1.0 or abs(self._target - desired) <= 0.5:
            self.canvas.yview_moveto(self._target / height)
            self.stop()
            return
        self.canvas.yview_moveto(desired / height)
        self._last_position = self.canvas.yview()[0] * height
        self._job = self.canvas.after(self._FRAME_MS, self._step)

    def close(self, event=None):
        if self._closed:
            return
        self.stop()
        self._closed = True
        for job in self._nested_jobs.values():
            try:
                self.canvas.after_cancel(job)
            except tk.TclError:
                pass
        self._nested_jobs.clear()
        self._nested_queues.clear()
        for sequence, command in self._bindings.items():
            try:
                self.root.unbind_class(self._tag, sequence)
                self.root.deletecommand(command)
            except tk.TclError:
                pass  # The root may already be tearing down.
