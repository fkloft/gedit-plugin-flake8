# ex:ts=4:et:

import enum
import functools
import gi
import re
import subprocess
import sys
import tempfile

from .gutterrenderer import GutterRenderer

gi.require_version('Gedit', '3.0')
gi.require_version('Gtk', '3.0')

from gi.repository import GObject, Gedit, GLib, GtkSource, Gtk, Pango, PeasGtk, Gio  # noqa


@enum.unique
@functools.total_ordering
class Level(enum.Enum):
    WARN = ("W", "#FFFF00")
    ERROR = ("E", "#FF0000")
    UNKNOWN = ("?", "#FF7F00")
    
    def __lt__(self, other):
        members = list(type(self).__members__.values())
        a = members.index(self)
        b = members.index(other)
        return a < b
    
    @classmethod
    def by_code(clz, code):
        for level in Level.__members__.values():
            if level.code == code:
                return level
        return clz.UNKNOWN
    
    @property
    def code(self):
        return self.value[0]
    
    @property
    def color(self):
        return self.value[1]


class Flake8ViewActivatable(GObject.Object, Gedit.ViewActivatable):
    view = GObject.Property(type=Gedit.View)
    
    def __init__(self):
        super().__init__()
        
        self.context_data = {}
        self.update_timeout = 0
        self.parse_signal = 0
        self.connected = False
    
    def do_activate(self):
        self.gutter_renderer = GutterRenderer(self)
        self.gutter = self.view.get_gutter(Gtk.TextWindowType.LEFT)
        
        self.view_signals = [
            self.view.connect('notify::buffer', self.on_notify_buffer),
        ]
        
        self.buffer = None
        self.on_notify_buffer(self.view)
    
    def do_deactivate(self):
        if self.update_timeout != 0:
            GLib.source_remove(self.update_timeout)
        if self.parse_signal != 0:
            GLib.source_remove(self.parse_signal)
            self.parse_signal = 0
        
        self.disconnect_buffer()
        self.buffer = None
        
        self.disconnect_view()
        self.gutter.remove(self.gutter_renderer)
    
    def disconnect(self, obj, signals):
        for sid in signals:
            obj.disconnect(sid)
        
        signals[:] = []
    
    def disconnect_buffer(self):
        self.disconnect(self.buffer, self.buffer_signals)
    
    def disconnect_view(self):
        self.disconnect(self.view, self.view_signals)
    
    def on_notify_buffer(self, view, gspec=None):
        if self.update_timeout != 0:
            GLib.source_remove(self.update_timeout)
        if self.parse_signal != 0:
            GLib.source_remove(self.parse_signal)
            self.parse_signal = 0
        
        if self.buffer:
            self.disconnect_buffer()
        
        self.buffer = view.get_buffer()
        
        # The changed signal is connected to in update_location().
        self.buffer_signals = [
            self.buffer.connect('saved', self.update_location),
            self.buffer.connect('loaded', self.update_location),
            self.buffer.connect('notify::language', self.update_location),
        ]
    
    def should_check(self):
        if self.location is None:
            return False
        
        if self.buffer.get_language().get_id().startswith("python"):
            return True
        
        return False
    
    def update_location(self, *unused):
        self.location = self.buffer.get_file().get_location()
        
        if not self.should_check():
            if self.connected:
                self.gutter.remove(self.gutter_renderer)
                self.buffer.disconnect(self.buffer_signals.pop())
                self.connected = False
            return
        
        if not self.connected:
            self.gutter.insert(self.gutter_renderer, 40)
            self.buffer_signals.append(self.buffer.connect('changed', self.update))
            self.connected = True
            self.update()
    
    def update(self, *unused):
        # We don't let the delay accumulate
        if self.update_timeout != 0:
            return
        if self.parse_signal != 0:
            GLib.source_remove(self.parse_signal)
            self.parse_signal = 0
        
        if self.connected:
            self.update_location()
        
        # Do the initial diff without a delay
        if not self.context_data:
            self.on_update_timeout()
        else:
            n_lines = self.buffer.get_line_count()
            delay = min(10000, 200 * (n_lines // 2000 + 1))
            
            self.update_timeout = GLib.timeout_add(delay, self.on_update_timeout)
    
    def on_update_timeout(self):
        self.update_timeout = 0
        if self.parse_signal != 0:
            GLib.source_remove(self.parse_signal)
            self.parse_signal = 0
        
        if not self.buffer:
            self.context_data = {}
        
        folder = self.location.get_parent().get_path()
        text = self.buffer.get_text(self.buffer.get_start_iter(), self.buffer.get_end_iter(), True)
        
        with tempfile.TemporaryFile("w+t") as fd:
            fd.write(text)
            fd.flush()
            fd.seek(0)
            
            proc = subprocess.Popen(
                ("flake8", "-"),
                cwd=folder,
                stdin=fd,
                stdout=subprocess.PIPE,
                universal_newlines=True,
            )
        
        data = ""
        
        def on_read(stdout, flags, proc):
            nonlocal data
            
            data += stdout.read(4096)
            if not (flags & GLib.IO_HUP):
                return True
            
            try:
                proc.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                return True
            
            data += stdout.read()
            self.parse_flake8(data)
            self.parse_signal = 0
            return False
        
        self.parse_signal = GLib.io_add_watch(proc.stdout, GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, on_read, proc)
    
    def parse_flake8(self, data):
        if not data:
            lines = []
        else:
            lines = data.strip("\n").split("\n")
        
        context_data = {}
        
        for line in lines:
            match = re.match(
                r"stdin:(?P<line>\d+):(?P<column>\d+):\s+(?P<class>[A-Z])(?P<error>\d+)\s+(?P<message>.*$)",
                line,
                flags=re.I,
            )
            if not match:
                print("Unknown line:", repr(line), file=sys.stderr)
                continue
            
            error = match.groupdict()
            error["line"] = int(error["line"])
            error["column"] = int(error["column"])
            error["level"] = Level.by_code(error["class"])
            
            context_data.setdefault(error["line"], []).append(error)
        
        self.context_data = context_data
        self.gutter_renderer.update()

