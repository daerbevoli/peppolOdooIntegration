import os
import sys
import time
import shutil
import queue
import threading
import logging
import tkinter as tk
from tkinter import scrolledtext
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from dotenv import load_dotenv

from peppol import OdooClient



logging.basicConfig(
    filename="peppol.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

def load_env():
    if getattr(sys, 'frozen', False):
        base_path = os.path.dirname(sys.executable)
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))

    load_dotenv(os.path.join(base_path, ".env"))

load_env()



def get_base_path():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = get_base_path()
WATCH_FOLDER = os.path.join(BASE_DIR, "Factuur")
SENT_FOLDER = os.path.join(BASE_DIR, "Factuur_sent")
POSTED_FOLDER = os.path.join(BASE_DIR, "Factuur_not_sent")
ERROR_FOLDER = os.path.join(BASE_DIR, "Factuur_error")

# Queue for files detected by Watchdog
file_queue = queue.Queue()

class PDFHandler(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory or not event.src_path.lower().endswith('.pdf'):
            return
        file_queue.put(event.src_path)


def wait_for_file_ready(file_path, timeout=5):
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            # Try to rename the file to itself; this usually fails if file is open/locked
            os.rename(file_path, file_path)
            return True
        except OSError:
            time.sleep(0.5)
    return False


class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Peppol Odoo Integration")
        self.root.geometry("700x500")

        self.observer = None
        self.is_running = False

        self.URL = os.getenv("ODOO_URL")
        self.DB = os.getenv("ODOO_DB")
        self.USERNAME = os.getenv("ODOO_USERNAME")
        self.API_KEY = os.getenv("ODOO_API_KEY")

        # Connect to Odoo
        self.odoo = OdooClient(self.URL, self.DB, self.USERNAME, self.API_KEY)

        try:
            self.odoo.connect()
            self.log("Connected to Odoo successfully.", "success")
            logging.log(logging.INFO, "Connected to Odoo successfully.")
            self.log(f"URL = {self.URL}")
        except Exception as e:
            self.log(f"Odoo Connection Failed: {e}", "error")
            logging.log(logging.ERROR, "Odoo Connection Failed")

        # --- GUI COMPONENTS ---
        header_frame = tk.Frame(root)
        header_frame.pack(pady=10, fill=tk.X, padx=20)

        self.status_label = tk.Label(root, text="System: IDLE", fg="gray", font=("Arial", 12, "bold"))
        self.status_label.pack(pady=5)

        self.btn_toggle = tk.Button(header_frame, text="START Monitoring",
                                    command=self.toggle_monitoring,
                                    bg="#90ee90", font=("Arial", 10, "bold"), width=20)
        self.btn_toggle.pack(side=tk.RIGHT)

        tk.Label(root, text="Activity Log:", font=("Arial", 10)).pack(anchor="w", padx=20)
        self.log_area = scrolledtext.ScrolledText(root, width=80, height=20, state='disabled')
        self.log_area.pack(pady=10, padx=20)

        # Configure log tags
        self.log_area.tag_config("error", foreground="red")
        self.log_area.tag_config("success", foreground="green")

        # Ensure ALL folders exist
        for folder in [WATCH_FOLDER, SENT_FOLDER, POSTED_FOLDER, ERROR_FOLDER]:
            os.makedirs(folder, exist_ok=True)

        self.log(f"System Ready. Watching: {os.path.abspath(WATCH_FOLDER)}")

        # Start the queue checker loop
        self.root.after(250, self.check_queue)

        self.start_monitoring()

    def toggle_monitoring(self):
        if self.is_running:
            self.stop_monitoring()
        else:
            self.start_monitoring()

    def start_monitoring(self):
        if self.is_running: return

        self.scan_existing_files()

        self.observer = Observer()
        self.observer.schedule(PDFHandler(), WATCH_FOLDER, recursive=False)
        self.observer.start()

        self.is_running = True
        self.status_label.config(text="Status: RUNNING", fg="green")
        self.btn_toggle.config(text="STOP Monitoring", bg="#ffcccc")
        self.log(">>> Monitoring STARTED")
        logging.log(logging.INFO, "Monitoring started.")

    def stop_monitoring(self):
        if not self.is_running or not self.observer: return

        self.observer.stop()
        self.observer.join()
        self.observer = None

        self.is_running = False
        self.status_label.config(text="Status: STOPPED", fg="red")
        self.btn_toggle.config(text="START Monitoring", bg="#90ee90")
        self.log(">>> Monitoring STOPPED")
        logging.log(logging.INFO, "Monitoring stopped.")

    def log(self, message, tag=None):
        """
        Thread-safe logging.
        Uses root.after_idle to ensure the GUI update happens on the main thread.
        """
        def _update():
            self.log_area.config(state='normal')
            timestamp = time.strftime("%H:%M:%S")
            self.log_area.insert(tk.END, f"[{timestamp}] {message}\n", tag)
            self.log_area.see(tk.END)
            self.log_area.config(state='disabled')

        self.root.after_idle(_update)

    def scan_existing_files(self):
        self.log("Scanning for existing PDF files...")
        for filename in os.listdir(WATCH_FOLDER):
            if filename.lower().endswith(".pdf"):
                full_path = os.path.join(WATCH_FOLDER, filename)
                file_queue.put(full_path)

    def check_queue(self):
        """ Checks queue and spawns a worker thread for new files so GUI doesn't freeze """
        try:
            while True:
                file_path = file_queue.get_nowait()
                # Spawn a thread to handle the heavy lifting
                threading.Thread(target=self.process_invoice_worker, args=(file_path,), daemon=True).start()
        except queue.Empty:
            pass
        finally:
            self.root.after(250, self.check_queue)

    def process_invoice_worker(self, file_path):
        """ This runs in a background thread """
        filename = os.path.basename(file_path)
        self.log(f"Detected: {filename} - Waiting for file ready...")
        logging.log(logging.INFO, f"Detected: {filename} - Waiting for file ready.")

        if not wait_for_file_ready(file_path):
            self.log(f"Error: File locked or inaccessible {filename}", "error")
            logging.log(logging.ERROR, "File locked or inaccessible")
            move_file(file_path, ERROR_FOLDER, filename)  # Move aside so we don't retry forever
            return

        try:
            self.log(f"Processing: {filename}...")

            invoice_id, new_filename, invoice_message = self.odoo.create_post_invoice(file_path)
            self.log(invoice_message)
            success, peppol_message = self.odoo.send_peppol_verify(invoice_id)

            if success:
                move_file(file_path, SENT_FOLDER, new_filename)
                self.log(f"{peppol_message} {new_filename}", "success")
                logging.log(logging.INFO, f"{peppol_message}: {new_filename}")
            else:
                move_file(file_path, POSTED_FOLDER, new_filename)
                self.log(f"{peppol_message} {new_filename}", "error")
                logging.log(logging.ERROR, f"{peppol_message}: {new_filename}")

        except Exception as e:
            self.log(f"Error processing {filename}: {e}", "error")
            logging.log(logging.ERROR, f" exception error {filename}",)
            move_file(file_path, ERROR_FOLDER, filename)


# --- UTILS ---

def move_file(src_path, dest_folder, new_filename):
    """ Moves file with collision handling """
    if not os.path.exists(src_path): return  # File might have moved already

    if not os.path.exists(dest_folder): os.makedirs(dest_folder)
    dest_path = os.path.join(dest_folder, new_filename)

    base, ext = os.path.splitext(new_filename)
    counter = 1
    while os.path.exists(dest_path):
        dest_path = os.path.join(dest_folder, f"{base}_{counter}{ext}")
        counter += 1

    try:
        shutil.move(src_path, dest_path)
    except Exception as e:
        logging.log(logging.ERROR, f"{e}", "error")


if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)


    def on_closing():
        app.stop_monitoring()
        root.destroy()
        sys.exit(0)

    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()