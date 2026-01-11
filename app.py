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

import win32print
import win32api
import win32con

def get_base_path():
    """ Get absolute path to resource, works for dev and for PyInstaller """
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
        self.root.title("PDF Auto-Sorter")
        self.root.geometry("600x400")

        self.observer = None
        self.is_running = False

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
                self.run_pdf_workflow(file_path)
        except queue.Empty:
            pass

        # Schedule this function to run again in 100ms
        self.root.after(100, self.process_queue)

    def run_pdf_workflow(self, file_path):
        filename = os.path.basename(file_path)
        self.log(f"Detected: {filename}")

        if not self.wait_for_file_ready(file_path):
            self.log(f"Error: File locked {filename}", "error")
            return

        try:
            # 1. Parse
            metadata = parse_pdf_data(file_path)

            # 2. Print
            self.log("... Sending to printer")
            # Run printing in background
            thread = threading.Thread(
                target=self._print_and_move,
                args=(file_path, metadata, filename),
                daemon=True
            )
            thread.start()

        except Exception as e:
            self.log(f"Failed: {e}", "error")
            move_file(file_path, ERROR_FOLDER, filename)
            self.log_area.tag_config("error", foreground="red")

    def _print_and_move(self, file_path, metadata, filename):
        try:
            temp_pdf = shutil.copy2(file_path, TEMP_FOLDER)
            print_pdf(temp_pdf)

            # Send success back to GUI thread
            self.root.after(0, lambda: self._on_print_success(file_path, metadata))

        except Exception as e:
            self.root.after(0, lambda err=e: self._on_print_failure(file_path, filename, err))

    def _on_print_success(self, file_path, metadata):
        self.log("Print job queued successfully", "success")
        new_name = generate_filename(metadata)
        move_file(file_path, PROCESSED_FOLDER, new_name)
        self.log(f"Moved to processed folder + {new_name}", "success")

    def _on_print_failure(self, file_path, filename, error):
        self.log(f"Failed: {error}", "error")
        move_file(file_path, ERROR_FOLDER, filename)
        self.log(f"Moved to error folder + {filename}", "error")


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

def print_pdf(file_path, timeout=30):
    """
    Reliably prints a PDF on Windows using native APIs.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(file_path)

    # 1. Get default printer
    printer_name = win32print.GetDefaultPrinter()
    if not printer_name:
        raise RuntimeError("No default printer configured")

    # 2. Open printer to check status
    printer_handle = win32print.OpenPrinter(printer_name)
    try:
        printer_info = win32print.GetPrinter(printer_handle, 2)
        status = printer_info["Status"]

        if status & win32print.PRINTER_STATUS_OFFLINE:
            raise RuntimeError("Printer is OFFLINE")
        if status & win32print.PRINTER_STATUS_ERROR:
            raise RuntimeError("Printer is in ERROR state")
        if status & win32print.PRINTER_STATUS_PAPER_OUT:
            raise RuntimeError("Printer is OUT OF PAPER")

        # 3. Send print command
        win32api.ShellExecute(0, "print", file_path, None, ".", win32con.SW_HIDE)

        # 4. Wait for job to appear in queue (The Receipt)
        start_time = time.time()
        job_detected = False
        while time.time() - start_time < timeout:
            jobs = win32print.EnumJobs(printer_handle, 0, -1, 1)
            if jobs:
                job_detected = True
                break
            time.sleep(0.5)

        if not job_detected:
            # But usually, this indicates the PDF reader failed to send.
            raise RuntimeError("Print job was not created (Timeout)")

        return True
    finally:
        win32print.ClosePrinter(printer_handle)
#
def parse_pdf_data(file_path):
    """
    Extracts text and applies regex to find Company, Date, and Invoice Number.
    """
    text = ""
    company_name = "Unknown"

    with pdfplumber.open(file_path) as pdf:
        if not pdf.pages:
            raise ValueError("PDF has no pages.")

        page = pdf.pages[0]
        text = page.extract_text()

        # Handle Scanned PDFs (Images)
        if not text:
            raise ValueError("No text found. PDF might be a scanned image.")

        # Extract Company Name (Top Right Quadrant Logic)
        width, height = page.width, page.height
        # Crop: Right 50%, Top 40%
        header_box = (width * 0.5, 0, width, height * 0.4)
        header_text = page.crop(header_box).extract_text()

        if header_text:
            lines = [l.strip() for l in header_text.splitlines() if l.strip()]
            if lines:
                company_name = lines[0]  # Assumes first line in top right is company

    # Regex Extraction
    # Matches: "Factuur 123", "Invoice: 123", "No. 123"
    inv_match = re.search(r"(?:Factuur|Faktuur|Invoice|Nr|No\.?)\s*[:.]?\s*(\d+)", text, re.IGNORECASE)

    # Matches: "19-12-2025", "19/12/2025", "19.12.2025"
    date_match = re.search(r"(?:Datum|Date)\s*[:.]?\s*(\d{2}[-./]\d{2}[-./]\d{4})", text, re.IGNORECASE)

    if not inv_match or not date_match:
        raise ValueError(f"Could not find Invoice Number or Date in text.")

    return {
        "company": company_name,
        "invoice_num": inv_match.group(1),
        "date": date_match.group(1)
    }

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