#!/usr/bin/env python3
import os
import sys
import time
import signal
import shutil
import json
import subprocess
import threading
from datetime import datetime

# Constants
ENGINE = "/var/lib/snapd/snap/bin/acestreamplayer.engine"
PLAYER = "/var/lib/snapd/snap/bin/acestreamplayer.mpv"
OUTPUT_DIR = os.path.expanduser("~/Desktop/acestream_recordings")
os.makedirs(OUTPUT_DIR, exist_ok=True)

FFMPEG_BIN = shutil.which("ffmpeg")
PLAYER_BIN = shutil.which(PLAYER)

# Get the directory where the script is located
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CHANNELS_FILE = os.path.join(SCRIPT_DIR, "channels.json")

# Utilities
def safe_name(s: str) -> str:
    keep = "".join(c if (c.isalnum() or c in " _-") else "_" for c in s)
    return "_".join(keep.split())[:80]

def ensure_engine():
    try:
        if shutil.which("pgrep"):
            rc = subprocess.call(["pgrep", "-f", "acestreamplayer.engine"], 
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if rc != 0:
                subprocess.Popen([ENGINE], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                time.sleep(2)
        else:
            subprocess.Popen([ENGINE], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(2)
    except Exception:
        pass

# Try GTK (PyGObject) first
USE_GTK = False
try:
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk, Gdk, GLib
    USE_GTK = True
except Exception:
    USE_GTK = False

# If no GTK, try ttkbootstrap
if not USE_GTK:
    try:
        import ttkbootstrap as tb
        from ttkbootstrap.constants import *
        TB_AVAILABLE = True
    except Exception:
        TB_AVAILABLE = False
        import tkinter as tk
        from tkinter import messagebox

# ---------------- GTK UI Implementation ----------------
if USE_GTK:
    class GTKApp:
        def __init__(self):
            self.builder = None
            self.window = Gtk.Window(title="AceStream Recorder — GTK")
            self.window.set_default_size(900, 650)
            self.window.connect("destroy", Gtk.main_quit)
            
            # State
            self.links = []
            self.displayed_indices = []
            self.selected_index = -1
            self.current_proc = None
            self.current_pg = None
            self.stop_flag = False
            self.minuts = 60
            self.shutdown_after = False
            self.output_dir = OUTPUT_DIR
            
            # UI
            self._build_ui()
            self.load_links()
        
        def _build_ui(self):
            main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            self.window.add(main)
            
            # Top controls
            top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            main.pack_start(top, False, False, 6)
            
            btn_refresh = Gtk.Button(label="Refresh")
            btn_refresh.connect("clicked", lambda w: threading.Thread(target=self.load_links, daemon=True).start())
            top.pack_start(btn_refresh, False, False, 0)
            
            btn_record = Gtk.Button(label="Record Selected")
            btn_record.connect("clicked", lambda w: self.on_record_selected())
            top.pack_start(btn_record, False, False, 0)
            
            btn_stop = Gtk.Button(label="Stop Recording")
            btn_stop.connect("clicked", lambda w: self.stop_recording())
            top.pack_start(btn_stop, False, False, 0)
            
            # Search + minutes + shutdown
            search_area = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            main.pack_start(search_area, False, False, 0)
            
            lbl_search = Gtk.Label(label="Search:")
            search_area.pack_start(lbl_search, False, False, 0)
            
            self.search_entry = Gtk.SearchEntry()
            self.search_entry.set_hexpand(True)
            self.search_entry.connect("search-changed", lambda w: self.on_search_changed())
            search_area.pack_start(self.search_entry, True, True, 0)
            
            lbl_minutes = Gtk.Label(label="Minutes:")
            search_area.pack_start(lbl_minutes, False, False, 0)
            
            self.spin_minutes = Gtk.SpinButton()
            self.spin_minutes.set_range(1, 1440)
            self.spin_minutes.set_value(60)
            search_area.pack_start(self.spin_minutes, False, False, 0)
            
            self.shutdown_chk = Gtk.CheckButton(label="Shutdown at end (30s wait)")
            search_area.pack_start(self.shutdown_chk, False, False, 0)
            
            # Output directory selector
            dir_area = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            main.pack_start(dir_area, False, False, 6)
            
            lbl_dir = Gtk.Label(label="Destination directory:")
            dir_area.pack_start(lbl_dir, False, False, 0)
            
            self.entry_output_dir = Gtk.Entry()
            self.entry_output_dir.set_text(OUTPUT_DIR)
            self.entry_output_dir.set_hexpand(True)
            dir_area.pack_start(self.entry_output_dir, True, True, 0)
            
            btn_browse = Gtk.Button(label="Browse...")
            btn_browse.connect("clicked", lambda w: self._browse_directory())
            dir_area.pack_start(btn_browse, False, False, 0)
            
            # List area (scrolled)
            list_frame = Gtk.Frame(label="Channels (select 1)")
            main.pack_start(list_frame, True, True, 0)
            
            scrolled = Gtk.ScrolledWindow()
            scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            list_frame.add(scrolled)
            
            self.listbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            scrolled.add_with_viewport(self.listbox)
            
            # Custom link
            custom_frame = Gtk.Frame(label="Custom link")
            main.pack_start(custom_frame, False, False, 0)
            
            cf = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            custom_frame.add(cf)
            
            self.entry_custom = Gtk.Entry()
            cf.pack_start(self.entry_custom, True, True, 0)
            
            btn_custom = Gtk.Button(label="Record Custom")
            btn_custom.connect("clicked", lambda w: self.on_record_custom())
            cf.pack_start(btn_custom, False, False, 0)
            
            # Status label
            self.status_label = Gtk.Label(label="Ready")
            main.pack_start(self.status_label, False, False, 6)
            
            self.window.show_all()
        
        def _browse_directory(self):
            dialog = Gtk.FileChooserDialog(
                title="Select destination directory",
                parent=self.window,
                action=Gtk.FileChooserAction.SELECT_FOLDER
            )
            dialog.add_buttons(
                Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                Gtk.STOCK_OPEN, Gtk.ResponseType.OK
            )
            
            response = dialog.run()
            if response == Gtk.ResponseType.OK:
                selected = dialog.get_filename()
                self.entry_output_dir.set_text(selected)
            
            dialog.destroy()
        
        def set_status(self, text):
            GLib.idle_add(self.status_label.set_text, text)
        
        def on_search_changed(self):
            self.populate_list(self.search_entry.get_text())
        
        def load_links(self):
            try:
                # Read from local channels.json file
                if not os.path.exists(CHANNELS_FILE):
                    self.set_status(f"Error: channels.json file not found at {CHANNELS_FILE}")
                    return
                
                with open(CHANNELS_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                if not isinstance(data, list):
                    raise ValueError("JSON is not a list")
                
                # Convert to the expected format
                self.links = []
                for item in data:
                    channel = item.get("channel", "")
                    link = item.get("link", "")
                    
                    # Remove acestream:// prefix if present for storage
                    hash_id = link.replace("acestream://", "") if link else ""
                    
                    # Store in the format expected by the rest of the code
                    self.links.append({
                        "channel": channel,
                        "link": hash_id,
                        "work": True  # Assume all channels work
                    })
                
                GLib.idle_add(self.populate_list, self.search_entry.get_text())
                self.set_status(f"{len(self.links)} channels loaded from local file")
            except Exception as e:
                self.set_status(f"Error loading channels: {e}")
        
        def populate_list(self, filter_text):
            # clear children
            for child in self.listbox.get_children():
                self.listbox.remove(child)
            
            ft = (filter_text or "").strip().lower()
            first_radio = None
            
            for idx, item in enumerate(self.links):
                channel = item.get("channel", f"channel_{idx}")
                link = item.get("link", "")
                works = bool(item.get("work", False))
                
                if ft:
                    if ft not in channel.lower() and ft not in (link or "").lower():
                        continue
                
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                self.listbox.pack_start(row, False, False, 2)
                
                # Create radiobutton with correct group
                if first_radio is None:
                    rb = Gtk.RadioButton.new_with_label_from_widget(None, "")
                    first_radio = rb
                else:
                    rb = Gtk.RadioButton.new_with_label_from_widget(first_radio, "")
                
                # Mark if previously selected
                if idx == self.selected_index:
                    rb.set_active(True)
                
                row.pack_start(rb, False, False, 0)
                
                lbl_text = f"{channel}   [{link}]"
                lbl = Gtk.Label(label=lbl_text, xalign=0)
                
                if not works:
                    lbl.set_markup(f"<span foreground='red'>{GLib.markup_escape_text(lbl_text)}</span>")
                
                row.pack_start(lbl, True, True, 0)
                
                # set radio toggled callback to update selected_index
                def on_toggle(rb_button, idx=idx):
                    if rb_button.get_active():
                        self.selected_index = idx
                
                rb.connect("toggled", on_toggle)
            
            self.listbox.show_all()
        
        def on_record_selected(self):
            if self.selected_index == -1:
                self.set_status("No channel selected.")
                return
            
            try:
                item = self.links[self.selected_index]
            except Exception:
                self.set_status("Invalid selection")
                return
            
            link = item.get("link", "")
            channel = item.get("channel", "Unknown")
            
            if not link:
                self.set_status("Invalid hash")
                return
            
            # Update output directory
            self.output_dir = self.entry_output_dir.get_text()
            if not os.path.exists(self.output_dir):
                try:
                    os.makedirs(self.output_dir, exist_ok=True)
                except Exception as e:
                    self.set_status(f"Error creating directory: {e}")
                    return
            
            minutes = int(self.spin_minutes.get_value_as_int())
            self.set_status(f"Starting recording: {channel}")
            self._set_ui_sensitive(False)
            self.stop_flag = False
            
            thread = threading.Thread(
                target=self._record_sequence, 
                args=([(channel, link)], minutes, self.shutdown_chk.get_active()), 
                daemon=True
            )
            thread.start()
        
        def on_record_custom(self):
            val = self.entry_custom.get_text().strip()
            if not val:
                self.set_status("Enter hash or acestream://hash")
                return
            
            if val.startswith("acestream://"):
                hashid = val.replace("acestream://", "").strip()
            else:
                hashid = val
            
            # Update output directory
            self.output_dir = self.entry_output_dir.get_text()
            if not os.path.exists(self.output_dir):
                try:
                    os.makedirs(self.output_dir, exist_ok=True)
                except Exception as e:
                    self.set_status(f"Error creating directory: {e}")
                    return
            
            minutes = int(self.spin_minutes.get_value_as_int())
            self.set_status("Starting recording: Custom")
            self._set_ui_sensitive(False)
            self.stop_flag = False
            
            thread = threading.Thread(
                target=self._record_sequence, 
                args=([("Custom", hashid)], minutes, self.shutdown_chk.get_active()), 
                daemon=True
            )
            thread.start()
        
        def _record_sequence(self, sequence, minutes, shutdown_after):
            ensure_engine()
            all_success = True
            
            for display_name, hashid in sequence:
                if self.stop_flag:
                    all_success = False
                    break
                
                GLib.idle_add(self.set_status, f"Recording: {display_name}")
                ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                fname = safe_name(display_name) if display_name else hashid[:12]
                output_ts = os.path.join(self.output_dir, f"acestream_{fname}_{ts}.ts")
                
                ok = self._record_one_proc(hashid, output_ts, minutes)
                
                if not ok:
                    all_success = False
                else:
                    # convert
                    if FFMPEG_BIN:
                        GLib.idle_add(self.set_status, f"Converting {fname} -> mp4")
                        mp4_path = os.path.splitext(output_ts)[0] + ".mp4"
                        try:
                            conv_cmd = [FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "error", 
                                       "-i", output_ts, "-c", "copy", mp4_path]
                            conv_proc = subprocess.run(conv_cmd, stdout=subprocess.PIPE, 
                                                      stderr=subprocess.PIPE, text=True)
                            if conv_proc.returncode != 0:
                                all_success = False
                        except Exception:
                            all_success = False
                
                time.sleep(0.6)
            
            GLib.idle_add(self._set_ui_sensitive, True)
            
            if shutdown_after and all_success and not self.stop_flag:
                GLib.idle_add(self.set_status, "Waiting 30 seconds before shutdown...")
                for i in range(30, 0, -1):
                    if self.stop_flag:
                        GLib.idle_add(self.set_status, "Shutdown cancelled")
                        return
                    GLib.idle_add(self.set_status, f"Shutting down in {i} seconds... (stop to cancel)")
                    time.sleep(1)
                
                try:
                    subprocess.Popen(["systemctl", "poweroff"])
                except Exception:
                    GLib.idle_add(self.set_status, "Finished (error shutting down)")
            else:
                if self.stop_flag:
                    GLib.idle_add(self.set_status, "Stopped by user")
                else:
                    GLib.idle_add(self.set_status, "Finished")
        
        def _record_one_proc(self, hashid, output_ts, minutes):
            cmd = [PLAYER, f"acestream://{hashid}", "--vo=null", "--quiet", f"--stream-record={output_ts}"]
            
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, 
                                       preexec_fn=os.setsid)
            except FileNotFoundError:
                GLib.idle_add(self.set_status, "Error: player not found")
                GLib.idle_add(lambda: messagebox_dialog("Error", f"Could not find '{PLAYER}' in PATH"))
                return False
            except Exception:
                return False
            
            self.current_proc = proc
            try:
                self.current_pg = os.getpgid(proc.pid)
            except Exception:
                self.current_pg = None
            
            total = minutes * 60
            start = time.time()
            
            while True:
                if self.stop_flag:
                    break
                if proc.poll() is not None:
                    break
                if (time.time() - start) >= total:
                    break
                time.sleep(0.4)
            
            if proc.poll() is None:
                try:
                    if self.current_pg is not None:
                        os.killpg(self.current_pg, signal.SIGTERM)
                    else:
                        proc.terminate()
                    time.sleep(1.2)
                    if proc.poll() is None:
                        if self.current_pg is not None:
                            os.killpg(self.current_pg, signal.SIGKILL)
                        else:
                            proc.kill()
                except Exception:
                    pass
            
            self.current_proc = None
            self.current_pg = None
            
            if not os.path.exists(output_ts) or os.path.getsize(output_ts) == 0:
                GLib.idle_add(lambda: messagebox_dialog("Warning", 
                    f"Output file may be empty or not created:\n{output_ts}"))
                return False
            
            return True
        
        def stop_recording(self):
            self.stop_flag = True
            if self.current_proc and self.current_proc.poll() is None:
                try:
                    if self.current_pg is not None:
                        os.killpg(self.current_pg, signal.SIGTERM)
                    else:
                        self.current_proc.terminate()
                    time.sleep(0.8)
                    if self.current_proc.poll() is None:
                        if self.current_pg is not None:
                            os.killpg(self.current_pg, signal.SIGKILL)
                        else:
                            self.current_proc.kill()
                except Exception:
                    pass
            self._set_ui_sensitive(True)
            GLib.idle_add(self.set_status, "Stopped by user")
        
        def _set_ui_sensitive(self, sensitive):
            def set_state():
                self.search_entry.set_sensitive(sensitive)
                self.spin_minutes.set_sensitive(sensitive)
                self.entry_custom.set_sensitive(sensitive)
                self.entry_output_dir.set_sensitive(sensitive)
                self.shutdown_chk.set_sensitive(sensitive)
                for child in self.listbox.get_children():
                    for w in child.get_children():
                        if isinstance(w, Gtk.RadioButton):
                            w.set_sensitive(sensitive)
            GLib.idle_add(set_state)
    
    # small helper for GTK messagebox (using Gtk.Dialog)
    def messagebox_dialog(title, text):
        dialog = Gtk.MessageDialog(flags=0, message_type=Gtk.MessageType.INFO,
                                   buttons=Gtk.ButtonsType.OK, text=title)
        dialog.format_secondary_text(text)
        dialog.run()
        dialog.destroy()
    
    def run_gtk():
        app = GTKApp()
        Gtk.main()

# ---------------- Ttkbootstrap / tkinter fallback ----------------
else:
    # Use ttkbootstrap if available for better look; else plain ttk
    try:
        import ttkbootstrap as tb
        from ttkbootstrap.constants import *
        TB_AVAILABLE = True
    except Exception:
        TB_AVAILABLE = False
        import tkinter as tk
        from tkinter import messagebox
    
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog
    
    class TTKApp:
        def __init__(self, root):
            self.root = root
            if TB_AVAILABLE:
                root.style = tb.Style("flatly")
            root.title("AceStream Recorder — modern")
            root.geometry("900x650")
            
            self.links = []
            self.displayed_indices = []
            self.selected_var = tk.IntVar(value=-1)
            self.current_proc = None
            self.current_pg = None
            self.stop_flag = False
            self.shutdown_after = tk.BooleanVar(value=False)
            self.output_dir = OUTPUT_DIR
            
            self._build_ui()
            self.load_links()
        
        def _build_ui(self):
            top = ttk.Frame(self.root)
            top.pack(fill=tk.X, padx=10, pady=8)
            
            refresh = ttk.Button(top, text="Refresh", command=self.load_links)
            refresh.pack(side=tk.LEFT)
            
            rec = ttk.Button(top, text="Record Selected", command=self.on_record_selected)
            rec.pack(side=tk.LEFT, padx=6)
            
            stop = ttk.Button(top, text="Stop Recording", command=self.stop_recording)
            stop.pack(side=tk.LEFT, padx=6)
            
            # search area
            search_frame = ttk.Frame(self.root)
            search_frame.pack(fill=tk.X, padx=10)
            
            ttk.Label(search_frame, text="Search:").pack(side=tk.LEFT, padx=(0,6))
            self.search_entry = ttk.Entry(search_frame)
            self.search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
            self.search_entry.bind("<KeyRelease>", lambda e: self.populate_list(self.search_entry.get()))
            
            ttk.Label(search_frame, text="Minutes:").pack(side=tk.LEFT, padx=(6,4))
            self.entry_minutes = ttk.Entry(search_frame, width=6)
            self.entry_minutes.insert(0, "60")
            self.entry_minutes.pack(side=tk.LEFT)
            
            ttk.Checkbutton(search_frame, text="Shutdown at end (30s wait)", 
                           variable=self.shutdown_after).pack(side=tk.LEFT, padx=(8,0))
            
            # Output directory area
            dir_frame = ttk.Frame(self.root)
            dir_frame.pack(fill=tk.X, padx=10, pady=6)
            
            ttk.Label(dir_frame, text="Destination directory:").pack(side=tk.LEFT, padx=(0,6))
            self.entry_output_dir = ttk.Entry(dir_frame)
            self.entry_output_dir.insert(0, OUTPUT_DIR)
            self.entry_output_dir.pack(side=tk.LEFT, fill=tk.X, expand=True)
            
            ttk.Button(dir_frame, text="Browse...", 
                      command=self._browse_directory).pack(side=tk.LEFT, padx=(6,0))
            
            # list area
            list_frame = ttk.Labelframe(self.root, text="Channels (select 1)")
            list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=8)
            
            self.canvas = tk.Canvas(list_frame)
            self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            
            vbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.canvas.yview)
            vbar.pack(side=tk.RIGHT, fill=tk.Y)
            self.canvas.configure(yscrollcommand=vbar.set)
            
            self.inner = ttk.Frame(self.canvas)
            self.inner_id = self.canvas.create_window((0,0), window=self.inner, anchor="nw")
            self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
            
            self.canvas.bind("<Enter>", lambda e: self._bind_mousewheel())
            self.canvas.bind("<Leave>", lambda e: self._unbind_mousewheel())
            
            # custom link
            custom_frame = ttk.Labelframe(self.root, text="Custom link")
            custom_frame.pack(fill=tk.X, padx=10, pady=(0,8))
            
            self.entry_custom = ttk.Entry(custom_frame)
            self.entry_custom.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6, pady=6)
            
            ttk.Button(custom_frame, text="Record Custom", 
                      command=self.on_record_custom).pack(side=tk.LEFT, padx=6, pady=6)
            
            self.status_lbl = ttk.Label(self.root, text="Ready")
            self.status_lbl.pack(fill=tk.X, padx=10, pady=(0,8))
        
        def _browse_directory(self):
            selected = filedialog.askdirectory(
                title="Select destination directory",
                initialdir=self.entry_output_dir.get()
            )
            if selected:
                self.entry_output_dir.delete(0, tk.END)
                self.entry_output_dir.insert(0, selected)
        
        def _bind_mousewheel(self):
            self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)
            self.canvas.bind_all("<Button-4>", self._on_mousewheel)
            self.canvas.bind_all("<Button-5>", self._on_mousewheel)
        
        def _unbind_mousewheel(self):
            self.canvas.unbind_all("<MouseWheel>")
            self.canvas.unbind_all("<Button-4>")
            self.canvas.unbind_all("<Button-5>")
        
        def _on_mousewheel(self, event):
            if event.num == 4:
                self.canvas.yview_scroll(-1, "units")
            elif event.num == 5:
                self.canvas.yview_scroll(1, "units")
            else:
                delta = int(-1*(event.delta/120))
                self.canvas.yview_scroll(delta, "units")
        
        def set_status(self, txt):
            self.status_lbl.config(text=txt)
        
        def load_links(self):
            for w in self.inner.winfo_children():
                w.destroy()
            
            try:
                # Read from local channels.json file
                if not os.path.exists(CHANNELS_FILE):
                    messagebox.showerror("Error", f"channels.json file not found at {CHANNELS_FILE}")
                    self.set_status("Error: channels.json file not found")
                    return
                
                with open(CHANNELS_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                # Convert to the expected format
                self.links = []
                for item in data:
                    channel = item.get("channel", "")
                    link = item.get("link", "")
                    
                    # Remove acestream:// prefix if present for storage
                    hash_id = link.replace("acestream://", "") if link else ""
                    
                    # Store in the format expected by the rest of the code
                    self.links.append({
                        "channel": channel,
                        "link": hash_id,
                        "work": True  # Assume all channels work
                    })
                
            except Exception as e:
                messagebox.showerror("Error", f"Could not load {CHANNELS_FILE}:\n{e}")
                self.set_status("Error loading channels")
                return
            
            self.populate_list("")
            self.set_status(f"{len(self.links)} channels loaded from local file")
        
        def populate_list(self, filter_text):
            for w in self.inner.winfo_children():
                w.destroy()
            
            ft = (filter_text or "").strip().lower()
            
            for idx, item in enumerate(self.links):
                channel = item.get("channel", f"channel_{idx}")
                link = item.get("link", "")
                works = bool(item.get("work", False))
                
                if ft and ft not in channel.lower() and ft not in (link or "").lower():
                    continue
                
                row = ttk.Frame(self.inner)
                row.pack(fill=tk.X, padx=6, pady=3)
                
                rb = ttk.Radiobutton(row, variable=self.selected_var, value=idx)
                rb.pack(side=tk.LEFT)
                
                text = f"{channel}   [{link}]"
                lbl = ttk.Label(row, text=text)
                lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)
                
                if not works:
                    lbl.config(foreground="red")
        
        def on_record_selected(self):
            sel = self.selected_var.get()
            if sel == -1:
                messagebox.showinfo("Info", "No channel selected")
                return
            
            item = self.links[sel]
            link = item.get("link", "")
            channel = item.get("channel", "")
            
            if not link:
                messagebox.showerror("Error", "Invalid hash")
                return
            
            # Update output directory
            self.output_dir = self.entry_output_dir.get()
            if not os.path.exists(self.output_dir):
                try:
                    os.makedirs(self.output_dir, exist_ok=True)
                except Exception as e:
                    messagebox.showerror("Error", f"Error creating directory: {e}")
                    return
            
            try:
                minutes = int(self.entry_minutes.get())
            except Exception:
                messagebox.showerror("Error", "Invalid minutes")
                return
            
            self.set_status(f"Starting recording: {channel}")
            self._set_ui_state(False)
            self.stop_flag = False
            
            threading.Thread(
                target=self._record_sequence, 
                args=([(channel, link)], minutes, self.shutdown_after.get()), 
                daemon=True
            ).start()
        
        def on_record_custom(self):
            val = self.entry_custom.get().strip()
            if not val:
                messagebox.showinfo("Info", "Enter hash or link")
                return
            
            if val.startswith("acestream://"):
                hashid = val.replace("acestream://", "").strip()
            else:
                hashid = val
            
            # Update output directory
            self.output_dir = self.entry_output_dir.get()
            if not os.path.exists(self.output_dir):
                try:
                    os.makedirs(self.output_dir, exist_ok=True)
                except Exception as e:
                    messagebox.showerror("Error", f"Error creating directory: {e}")
                    return
            
            try:
                minutes = int(self.entry_minutes.get())
            except Exception:
                messagebox.showerror("Error", "Invalid minutes")
                return
            
            self.set_status("Starting recording: Custom")
            self._set_ui_state(False)
            self.stop_flag = False
            
            threading.Thread(
                target=self._record_sequence, 
                args=([("Custom", hashid)], minutes, self.shutdown_after.get()), 
                daemon=True
            ).start()
        
        def _record_sequence(self, sequence, minutes, shutdown_after):
            ensure_engine()
            all_success = True
            
            for display_name, hashid in sequence:
                if self.stop_flag:
                    all_success = False
                    break
                
                self.set_status(f"Recording: {display_name}")
                ts = datetime.now().strftime("%Y-%m-d_%H-%M-%S")
                fname = safe_name(display_name) if display_name else hashid[:12]
                output_ts = os.path.join(self.output_dir, f"acestream_{fname}_{ts}.ts")
                
                ok = self._record_one_proc(hashid, output_ts, minutes)
                
                if not ok:
                    all_success = False
                else:
                    # conversion
                    if FFMPEG_BIN:
                        self.set_status(f"Converting {fname} -> mp4")
                        mp4_path = os.path.splitext(output_ts)[0] + ".mp4"
                        try:
                            conv_cmd = [FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "error", 
                                       "-i", output_ts, "-c", "copy", mp4_path]
                            conv_proc = subprocess.run(conv_cmd, stdout=subprocess.PIPE, 
                                                      stderr=subprocess.PIPE, text=True)
                            if conv_proc.returncode != 0:
                                all_success = False
                        except Exception:
                            all_success = False
                    else:
                        all_success = False
                
                time.sleep(0.6)
            
            self._set_ui_state(True)
            
            if shutdown_after and all_success and not self.stop_flag:
                self.set_status("Waiting 30 seconds before shutdown...")
                for i in range(30, 0, -1):
                    if self.stop_flag:
                        self.set_status("Shutdown cancelled")
                        return
                    self.set_status(f"Shutting down in {i} seconds... (stop to cancel)")
                    time.sleep(1)
                
                try:
                    subprocess.Popen(["systemctl", "poweroff"])
                except Exception:
                    self.set_status("Finished (error shutting down)")
            else:
                if self.stop_flag:
                    self.set_status("Stopped by user")
                else:
                    self.set_status("Finished")
        
        def _record_one_proc(self, hashid, output_ts, minutes):
            cmd = [PLAYER, f"acestream://{hashid}", "--vo=null", "--quiet", f"--stream-record={output_ts}"]
            
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, 
                                       preexec_fn=os.setsid)
            except FileNotFoundError:
                messagebox.showerror("Error", f"Could not find '{PLAYER}' in PATH.")
                self._set_ui_state(True)
                self.set_status("Error: player not found")
                return False
            except Exception:
                return False
            
            self.current_proc = proc
            try:
                self.current_pg = os.getpgid(proc.pid)
            except Exception:
                self.current_pg = None
            
            total = minutes * 60
            start = time.time()
            
            while True:
                if self.stop_flag:
                    break
                if proc.poll() is not None:
                    break
                if (time.time() - start) >= total:
                    break
                time.sleep(0.4)
            
            if proc.poll() is None:
                try:
                    if self.current_pg is not None:
                        os.killpg(self.current_pg, signal.SIGTERM)
                    else:
                        proc.terminate()
                    time.sleep(1.2)
                    if proc.poll() is None:
                        if self.current_pg is not None:
                            os.killpg(self.current_pg, signal.SIGKILL)
                        else:
                            proc.kill()
                except Exception:
                    pass
            
            self.current_proc = None
            self.current_pg = None
            
            if not os.path.exists(output_ts) or os.path.getsize(output_ts) == 0:
                messagebox.showwarning("Warning", f"File may be empty or not created:\n{output_ts}")
                return False
            
            return True
        
        def stop_recording(self):
            self.stop_flag = True
            if self.current_proc and self.current_proc.poll() is None:
                try:
                    if self.current_pg is not None:
                        os.killpg(self.current_pg, signal.SIGTERM)
                    else:
                        self.current_proc.terminate()
                    time.sleep(0.8)
                    if self.current_proc.poll() is None:
                        if self.current_pg is not None:
                            os.killpg(self.current_pg, signal.SIGKILL)
                        else:
                            self.current_proc.kill()
                except Exception:
                    pass
            self._set_ui_state(True)
            self.set_status("Stopped by user")
        
        def _set_ui_state(self, enabled):
            state = "normal" if enabled else "disabled"
            try:
                self.search_entry.config(state=state)
                self.entry_minutes.config(state=state)
                self.entry_custom.config(state=state)
                self.entry_output_dir.config(state=state)
            except Exception:
                pass
            
            for child in self.inner.winfo_children():
                for w in child.winfo_children():
                    try:
                        w.config(state=state)
                    except Exception:
                        pass
    
    def run_ttk():
        if TB_AVAILABLE:
            root = tb.Window(themename="flatly")
        else:
            root = tk.Tk()
        app = TTKApp(root)
        root.mainloop()

# ---------------- Main entry ----------------
def main():
    if USE_GTK:
        run_gtk()
    else:
        run_ttk()

if __name__ == "__main__":
    main()
