#!/usr/bin/env python3

import sys
import os
import platform
import subprocess
import shutil
import time
import signal
from pathlib import Path
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QFileDialog, QComboBox, QSpinBox,
    QCheckBox, QLineEdit, QProgressBar, QMessageBox, QPlainTextEdit
)
from PyQt6.QtGui import QFont
from PyQt6.QtCore import QThread, pyqtSignal

class CommandRunner(QThread):
    finished = pyqtSignal(bool, str)
    progress = pyqtSignal(str)

    def __init__(self, conda_env, command):
        super().__init__()
        self.conda_env = conda_env
        self.command = command
        self.is_windows = platform.system() == "Windows"
        self.should_terminate = False
        self.process = None
        self.child_pid = None

    def _find_magic_pdf_process(self):
        """Find the magic-pdf process using psutil"""
        try:
            import psutil
            parent = psutil.Process(self.process.pid)
            # Get all children of the shell process
            children = parent.children(recursive=True)
            # Look for magic-pdf in the command line
            for child in children:
                try:
                    cmdline = " ".join(child.cmdline()).lower()
                    if 'magic-pdf' in cmdline:
                        return child.pid
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            return None
        except Exception as e:
            self.progress.emit(f"Error finding magic-pdf process: {str(e)}")
            return None

    def run(self):
        try:
            if self.is_windows:
                activate_cmd = f"call conda activate {self.conda_env} && "
            else:
                activate_cmd = f"source activate {self.conda_env} && "

            full_cmd = activate_cmd + self.command

            # Create process
            if self.is_windows:
                CREATE_NEW_PROCESS_GROUP = 0x00000200
                self.process = subprocess.Popen(
                    full_cmd,
                    shell=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    creationflags=CREATE_NEW_PROCESS_GROUP
                )
            else:
                self.process = subprocess.Popen(
                    full_cmd,
                    shell=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    preexec_fn=os.setsid
                )

            # Find the magic-pdf process PID after a short delay
            time.sleep(1)
            self.child_pid = self._find_magic_pdf_process()
            if self.child_pid:
                self.progress.emit(f"Found magic-pdf process: {self.child_pid}")
            else:
                self.progress.emit("Warning: Could not find magic-pdf process")

            while True:
                if self.should_terminate:
                    self._terminate_process()
                    self.finished.emit(False, "Process canceled by user")
                    return

                if self.process.poll() is not None:
                    break

                line = self.process.stdout.readline()
                if line:
                    self.progress.emit(line.strip())
                else:
                    time.sleep(0.1)

            returncode = self.process.wait()
            if returncode == 0:
                self.finished.emit(True, "Success!")
            else:
                stderr_output = self.process.stderr.read()
                if stderr_output:
                    for err_line in stderr_output.splitlines():
                        self.progress.emit("[stderr]: " + err_line)
                self.finished.emit(False, "Error encountered. See log for details.")

        except Exception as e:
            self.finished.emit(False, f"Error: {str(e)}")
        finally:
            self._cleanup()

    def stop(self):
        """Request the process to stop."""
        self.should_terminate = True

    def _terminate_process(self):
        """Terminate specifically the magic-pdf process using psutil."""
        try:
            import psutil
            if self.child_pid:
                try:
                    process = psutil.Process(self.child_pid)
                    # Get children before terminating parent
                    children = process.children(recursive=True)

                    # Terminate the main process
                    process.terminate()

                    # Wait for termination
                    try:
                        process.wait(timeout=3)
                    except psutil.TimeoutExpired:
                        process.kill()

                    # Terminate any remaining children
                    for child in children:
                        try:
                            child.terminate()
                            try:
                                child.wait(timeout=1)
                            except psutil.TimeoutExpired:
                                child.kill()
                        except psutil.NoSuchProcess:
                            pass

                except psutil.NoSuchProcess:
                    self.progress.emit("Process already terminated")
                except Exception as e:
                    self.progress.emit(f"Error terminating magic-pdf process: {str(e)}")

            # Cleanup the shell process if it's still running
            if self.process and self.process.poll() is None:
                self.process.terminate()
                time.sleep(0.5)
                if self.process.poll() is None:
                    self.process.kill()

        except ImportError:
            self.progress.emit("psutil not installed. Using fallback termination method.")
            # Fallback to basic termination if psutil is not available
            if self.child_pid:
                try:
                    if self.is_windows:
                        subprocess.run(['taskkill', '/F', '/PID', str(self.child_pid)],
                                     capture_output=True)
                    else:
                        os.kill(self.child_pid, signal.SIGTERM)
                except Exception as e:
                    self.progress.emit(f"Error in fallback termination: {str(e)}")

    def _cleanup(self):
        """Clean up any remaining process resources."""
        if self.process:
            try:
                self.process.stdout.close()
                self.process.stderr.close()
            except:
                pass

class MinerUGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.conda_env = "MinerU"  # Name of conda environment
        self.cancel_requested = False
        self.init_ui()

    def init_ui(self):
        self.setWindowTitle('MinerU GUI')
        self.setGeometry(100, 100, 800, 600)

        # Create central widget and layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)

        # Input file/directory selection
        input_layout = QHBoxLayout()
        self.input_path = QLineEdit()
        input_btn = QPushButton('Select Input')
        input_btn.clicked.connect(self.select_input)
        input_layout.addWidget(QLabel('Input Path:'))
        input_layout.addWidget(self.input_path)
        input_layout.addWidget(input_btn)
        layout.addLayout(input_layout)

        # Output directory selection
        output_layout = QHBoxLayout()
        self.output_path = QLineEdit()
        output_btn = QPushButton('Select Output')
        output_btn.clicked.connect(self.select_output)
        output_layout.addWidget(QLabel('Output Directory:'))
        output_layout.addWidget(self.output_path)
        output_layout.addWidget(output_btn)
        layout.addLayout(output_layout)

        # Method selection
        method_layout = QHBoxLayout()
        self.method_combo = QComboBox()
        self.method_combo.addItems(['auto', 'ocr', 'txt'])
        method_layout.addWidget(QLabel('Method:'))
        method_layout.addWidget(self.method_combo)
        layout.addLayout(method_layout)

        # Language selection
        lang_layout = QHBoxLayout()
        self.lang_input = QLineEdit()
        self.lang_input.setPlaceholderText('e.g., en, ch, jp...')
        lang_layout.addWidget(QLabel('Language (optional):'))
        lang_layout.addWidget(self.lang_input)
        layout.addLayout(lang_layout)

        # Page range
        page_layout = QHBoxLayout()
        self.start_page = QSpinBox()
        self.end_page = QSpinBox()
        self.start_page.setMinimum(0)
        self.end_page.setMinimum(0)
        page_layout.addWidget(QLabel('Start Page:'))
        page_layout.addWidget(self.start_page)
        page_layout.addWidget(QLabel('End Page:'))
        page_layout.addWidget(self.end_page)
        layout.addLayout(page_layout)

        # Debug mode
        debug_layout = QHBoxLayout()
        self.debug_check = QCheckBox('Debug Mode')
        debug_layout.addWidget(self.debug_check)
        layout.addLayout(debug_layout)

        # Progress bar and status
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        layout.addWidget(self.progress_bar)

        self.status_label = QLabel('Ready')
        status_label_font = self.status_label.font()
        status_label_font.setBold(True)
        self.status_label.setFont(status_label_font)
        layout.addWidget(self.status_label)

        # Add a read-only text widget for output logs
        self.output_log = QPlainTextEdit()
        self.output_log.setReadOnly(True)
        self.output_log.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        layout.addWidget(self.output_log)

        # Process and Cancel Buttons
        btn_layout = QHBoxLayout()
        self.process_btn = QPushButton('Process PDF')
        self.process_btn.clicked.connect(self.process_pdf)
        self.cancel_btn = QPushButton('Cancel')
        self.cancel_btn.clicked.connect(self.cancel_process)
        self.cancel_btn.setEnabled(False)
        btn_layout.addWidget(self.process_btn)
        btn_layout.addWidget(self.cancel_btn)
        layout.addLayout(btn_layout)

    def select_input(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select PDF File", "", "PDF Files (*.pdf)"
        )
        if path:
            self.input_path.setText(path)

    def select_output(self):
        path = QFileDialog.getExistingDirectory(
            self, "Select Output Directory"
        )
        if path:
            self.output_path.setText(path)

    def process_pdf(self):
        if not self.input_path.text() or not self.output_path.text():
            QMessageBox.warning(self, "Error", "Please select input and output paths")
            return

        # Determine the output directory that will be created by MinerU
        pdf_name = os.path.splitext(os.path.basename(self.input_path.text()))[0]
        output_dir = self.output_path.text()
        md_dir = os.path.join(output_dir, pdf_name)

        # Check if this directory already exists and is non-empty (implying previous output)
        if os.path.exists(md_dir) and os.listdir(md_dir):
            reply = QMessageBox.question(
                self,
                "Overwrite Existing Output",
                f"Output directory '{md_dir}' already exists and may contain previous results.\nDo you want to overwrite it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.No:
                # User does not want to overwrite, so cancel the process
                return
            else:
                # If overwriting, remove old directory first
                try:
                    shutil.rmtree(md_dir)
                except Exception as e:
                    QMessageBox.warning(
                        self,
                        "Error",
                        f"Failed to remove existing directory '{md_dir}'. {str(e)}"
                    )
                    return

        # Construct command
        cmd = f'magic-pdf -p "{self.input_path.text()}" -o "{self.output_path.text()}" -m {self.method_combo.currentText()}'

        if self.lang_input.text():
            cmd += f' -l {self.lang_input.text()}'

        if self.start_page.value() > 0:
            cmd += f' -s {self.start_page.value()}'

        if self.end_page.value() > 0:
            cmd += f' -e {self.end_page.value()}'

        if self.debug_check.isChecked():
            cmd += ' -d True'

        # Disable UI during processing
        self.process_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.progress_bar.setMaximum(0)
        self.status_label.setText('Processing...')
        self.output_log.clear()
        self.cancel_requested = False

        # Run command
        self.runner = CommandRunner(self.conda_env, cmd)
        self.runner.progress.connect(self.update_progress)
        self.runner.finished.connect(self.process_finished)
        self.runner.start()

    def cancel_process(self):
        if hasattr(self, 'runner') and self.runner.isRunning():
            self.cancel_requested = True
            self.runner.stop()
            self.status_label.setText("Cancelling...")

    def update_progress(self, message):
        # Append new lines of output to the text box
        self.output_log.appendPlainText(message)

    def process_finished(self, success, message):
        self.progress_bar.setMaximum(100)
        self.process_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)

        if success:
            self.status_label.setText("Success")
        else:
            if self.cancel_requested:
                self.status_label.setText("Canceled")
            else:
                self.status_label.setText("Error: " + message)

        # If canceled, remove the partially created directory
        if self.cancel_requested:
            pdf_name = os.path.splitext(os.path.basename(self.input_path.text()))[0]
            output_dir = self.output_path.text()
            md_dir = os.path.join(output_dir, pdf_name)
            if os.path.exists(md_dir):
                try:
                    shutil.rmtree(md_dir)
                    self.output_log.appendPlainText(f"Cleaned up directory: {md_dir}")
                except Exception as e:
                    self.output_log.appendPlainText(f"Failed to remove directory {md_dir}: {str(e)}")

def main():
    app = QApplication(sys.argv)
    window = MinerUGUI()
    window.show()
    sys.exit(app.exec())

if __name__ == '__main__':
    main()
