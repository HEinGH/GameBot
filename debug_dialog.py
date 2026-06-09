import sys, tkinter as tk
sys.path.insert(0, '.')
from pathlib import Path
from gui.app import CharacterDialog

root = tk.Tk()
root.withdraw()

chars = [{
    'name': 'TestChar',
    'portrait_template': '',
    'skill_bar_template': '',
    'result_screen_template': '',
    'avatar_template': '',
    'runs': 4,
    'combos': [
        {'keys': ['e'], 'duration': 0.15, 'delay_after': 0.5},
        {'keys': ['q'], 'duration': 0.15, 'delay_after': 1.0},
    ]
}]

# Create dialog and inspect it
dialog_created = [None]
def on_close():
    root.quit()

def create():
    d = CharacterDialog(root, chars, 0, Path('.'), lambda: None)
    # Inspect what's inside
    print("Dialog title:", d.dialog.title())
    print("Dialog size:", d.dialog.winfo_width(), "x", d.dialog.winfo_height())
    
    # Find combo_frame
    for child in d.dialog.winfo_children():
        if isinstance(child, ttk.Frame):
            for sub in child.winfo_children():
                if isinstance(sub, ttk.LabelFrame):
                    print("LabelFrame text:", sub.cget("text"))
    
    # Schedule close
    d.dialog.after(100, d.dialog.destroy)
    d.dialog.after(200, on_close)

root.after(100, create)
root.mainloop()
print("Done")
