# ex:ts=4:et:

import re
from gi.repository import Gdk, GLib, GtkSource

TOOLTIP_TEMPLATE = re.sub(r"\s+", " ", """
    {line}<span foreground="#008899">:</span>{column}<span foreground="#008899">:</span>
    <span foreground="{c}"><b>{class}{error}</b></span> {escapedmsg}
""".strip())


class GutterRenderer(GtkSource.GutterRenderer):
    def __init__(self, view):
        GtkSource.GutterRenderer.__init__(self)
        
        self.view = view
        
        self.set_size(8)
        self.set_padding(3, 0)
        
        self.file_context = {}
        self.tooltip_line = 0
    
    def do_draw(self, cr, bg_area, cell_area, start, end, state):
        GtkSource.GutterRenderer.do_draw(self, cr, bg_area, cell_area, start, end, state)
        
        line = start.get_line() + 1
        
        if not self.view.context_data:
            return
        
        messages = self.view.context_data.get(line, None)
        if not messages:
            return
        
        level = sorted(m["level"] for m in messages)[-1]  # highest level
        
        background = Gdk.RGBA()
        background.parse(level.color)
        Gdk.cairo_set_source_rgba(cr, background)
        cr.rectangle(cell_area.x, cell_area.y, cell_area.width, cell_area.height)
        cr.fill()
    
    def do_query_tooltip(self, it, area, x, y, tooltip):
        line = it.get_line() + 1
        
        if not self.view.context_data:
            self.tooltip_line = 0
            return False
        
        messages = self.view.context_data.get(line, None)
        if not messages:
            self.tooltip_line = 0
            return False
        
        self.tooltip_line = line
        
        text = "\n".join(
            TOOLTIP_TEMPLATE.format(
                c=message["level"].color,
                escapedmsg=GLib.markup_escape_text(message["message"]),
                **message,
            )
            for message
            in messages
        )
        
        tooltip.set_markup(f'<span font="monospace">{text}</span>')
        return True
    
    def update(self):
        self.queue_draw()

