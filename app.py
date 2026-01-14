import os
import sys
import time
import shutil
import re
import queue
import threading

import pdfplumber
import tkinter as tk
from tkinter import scrolledtext
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from peppol import connect, create_post_invoice


def get_base_path():
    """ Get absolute path to resource """
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        return os.path.dirname(os.path.abspath(__file__))

# Get the folder where this script is actually located
BASE_DIR = get_base_path()

# Define folders relative to the script location
WATCH_FOLDER = os.path.join(BASE_DIR, "Factuur")
PROCESSED_FOLDER = os.path.join(BASE_DIR, "Factuur_processed")
ERROR_FOLDER = os.path.join(BASE_DIR, "Factuur_errors")
TEMP_FOLDER = os.path.join(BASE_DIR, "Factuur_temp")

# A thread-safe queue to pass files from Watchdog to the GUI
file_queue = queue.Queue()

class PDFHandler(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory or not event.src_path.lower().endswith('.pdf'):
            return
        file_queue.put(event.src_path)

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Peppol Odoo Integration")
        self.root.geometry("600x400")

        self.observer = None
        self.is_running = False

        self.models, self.uid = connect()

        # -- GUI COMPONENTS ---

        # 1. Header Frame
        header_frame = tk.Frame(root)
        header_frame.pack(pady=10, fill=tk.X, padx=20)

        # Status Label
        self.status_label = tk.Label(root, text="System: IDLE", fg="green", font=("Arial", 12, "bold"))
        self.status_label.pack(pady=10)

        # Toggle Button
        self.btn_toggle = tk.Button(header_frame, text="START Monitoring",
                                    command=self.toggle_monitoring,
                                    bg="#90ee90", font=("Arial", 10, "bold"), width=20)
        self.btn_toggle.pack(side=tk.RIGHT)

        # Log Window
        tk.Label(root, text="Activity Log:", font=("Arial", 10)).pack(anchor="w", padx=20)
        self.log_area = scrolledtext.ScrolledText(root, width=70, height=15, state='disabled')
        self.log_area.pack(pady=10)

        # Ensure folders exist
        for folder in [PROCESSED_FOLDER, ERROR_FOLDER, TEMP_FOLDER]:
            os.makedirs(folder, exist_ok=True)

        self.log(f"System Ready. Target: {os.path.abspath(WATCH_FOLDER)}")
        self.root.after(100, self.process_queue)

        self.log_area.tag_config("error", foreground="red")

    def toggle_monitoring(self):
        """Switches between Start and Stop states"""
        if self.is_running:
            self.stop_monitoring()
        else:
            self.start_monitoring()

    def start_monitoring(self):
        if self.is_running: return

        # Create a FRESH observer every time we start
        self.observer = Observer()
        self.observer.schedule(PDFHandler(), WATCH_FOLDER, recursive=False)
        self.observer.start()

        self.is_running = True
        self.status_label.config(text="Status: RUNNING", fg="green")
        self.btn_toggle.config(text="STOP Monitoring", bg="#ffcccc")
        self.log(">>> Monitoring STARTED")

    def stop_monitoring(self):
        if not self.is_running or not self.observer: return

        self.observer.stop()
        self.observer.join()
        self.observer = None

        self.is_running = False
        self.status_label.config(text="Status: STOPPED", fg="red")
        self.btn_toggle.config(text="START Monitoring", bg="#90ee90")
        self.log(">>> Monitoring STOPPED")

    def log(self, message, tag=None):
        """Thread-safe logging to the text box"""
        self.log_area.config(state='normal')
        timestamp = time.strftime("%H:%M:%S")
        self.log_area.insert(tk.END, f"[{timestamp}] {message}\n", tag)
        self.log_area.see(tk.END)
        self.log_area.config(state='disabled')


    def process_queue(self):
        """
        This function runs on the MAIN thread every 100ms.
        It checks if Watchdog put anything in the queue.
        """
        try:
            # Check if there is a file in the queue (non-blocking)
            while True:
                file_path = file_queue.get_nowait()
                self.run_workflow(self.models, self.uid, file_path)
        except queue.Empty:
            pass

        # Schedule this function to run again in 100ms
        self.root.after(100, self.process_queue)

    def run_workflow(self, models, uid, file_path):
        filename = os.path.basename(file_path)
        self.log(f"Detected: {filename}")

        if not self.wait_for_file_ready(file_path):
            self.log(f"Error: File locked {filename}", "error")
            return

        try:
            # 1. Parse
            self.log("Parsing PDF and creating invoice...")
            invoice_created = create_post_invoice(models, uid, file_path)
            self.log("Invoice created in Odoo", "success")

            if (invoice_created):
                move_file(file_path, PROCESSED_FOLDER, filename)
            else:
                move_file(file_path, ERROR_FOLDER, filename)

        except Exception as e:
            self.log(f"Failed: {e}", "error")
            move_file(file_path, ERROR_FOLDER, filename)
            self.log_area.tag_config("error", foreground="red")


    # --- HELPER FUNCTION ---
    def wait_for_file_ready(self, file_path, timeout=5):
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                with open(file_path, 'ab'):
                    pass
                return True
            except OSError:
                time.sleep(0.5)
        return False


# --- LOGIC FUNCTIONS ---

def generate_filename(metadata):
    """
    Creates a safe filename: Company_YYYYMMDD_InvNum.pdf
    """
    # Clean company name
    safe_company = "".join([c for c in metadata['company'] if c.isalnum() or c in (' ', '')]).strip().replace(" ", "")

    # Clean date (Remove separators like / - .)
    safe_date = re.sub(r"[-./]", "", metadata['date'])

    return f"{safe_company}_{safe_date}_{metadata['invoice_num']}.pdf"


def move_file(src_path, dest_folder, new_filename):
    if not os.path.exists(dest_folder): os.makedirs(dest_folder)
    dest_path = os.path.join(dest_folder, new_filename)
    base, ext = os.path.splitext(new_filename)
    counter = 1
    while os.path.exists(dest_path):
        dest_path = os.path.join(dest_folder, f"{base}_{counter}{ext}")
        counter += 1
    shutil.move(src_path, dest_path)


if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)

    # Handle clean exit on window close
    def on_closing():
        app.stop_monitoring()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()