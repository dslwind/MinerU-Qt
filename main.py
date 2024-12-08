#!/usr/bin/env python3

import psutil
import sys
import os
import json
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
from PyQt6.QtCore import QThread, QLocale, pyqtSignal, QUrl

class TranslationManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(TranslationManager, cls).__new__(cls)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        self.translations = {}
        self.current_language = "en"  # Default to English
        self._load_translations()

    def _load_translations(self):
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            translation_file = os.path.join(script_dir, "translations.json")
            with open(translation_file, 'r', encoding='utf-8') as f:
                self.translations = json.load(f)
        except Exception as e:
            print(f"Error loading translations: {e}")
            self.translations = {}

    def set_language(self, lang):
        if lang in ["en", "zh"]:
            self.current_language = lang

    def get_text(self, category, key, **kwargs):
        try:
            if category in self.translations and key in self.translations[category]:
                text = self.translations[category][key][self.current_language]
                if kwargs:
                    return text.format(**kwargs)
                return text
        except Exception as e:
            print(f"Translation error for {category}.{key}: {e}")
        return f"{category}.{key}"

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
        self.tm = TranslationManager()

    def _find_magic_pdf_process(self):
        try:
            import psutil
            parent = psutil.Process(self.process.pid)
            children = parent.children(recursive=True)
            for child in children:
                try:
                    cmdline = " ".join(child.cmdline()).lower()
                    if 'magic-pdf' in cmdline:
                        return child.pid
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            return None
        except Exception as e:
            self.progress.emit(self.tm.get_text("process_messages", "find_process_error", error=str(e)))
            return None

    def run(self):
        try:
            if self.is_windows:
                activate_cmd = f"call conda activate {self.conda_env} && "
            else:
                activate_cmd = f"source activate {self.conda_env} && "

            full_cmd = activate_cmd + self.command

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

            time.sleep(1)
            self.child_pid = self._find_magic_pdf_process()
            if self.child_pid:
                self.progress.emit(self.tm.get_text("process_messages", "found_process", pid=self.child_pid))
            else:
                self.progress.emit(self.tm.get_text("process_messages", "process_warning"))

            # Main loop to read the process output
            while True:
                if self.should_terminate:
                    # Process termination is now done in stop(), but we ensure here too.
                    self._terminate_process()
                    self.finished.emit(False, self.tm.get_text("process_messages", "user_cancel"))
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
                self.finished.emit(True, self.tm.get_text("process_messages", "process_success"))
            else:
                stderr_output = self.process.stderr.read()
                if stderr_output:
                    for err_line in stderr_output.splitlines():
                        self.progress.emit(self.tm.get_text("process_messages", "stderr_prefix") + err_line)
                self.finished.emit(False, self.tm.get_text("process_messages", "process_error"))

        except Exception as e:
            self.finished.emit(False, self.tm.get_text("messages", "process_error", msg=str(e)))
        finally:
            self._cleanup()

    def stop(self):
        # Immediately signal that termination is requested
        self.should_terminate = True
        # Immediately attempt to terminate the process to make cancel behavior stronger
        self._terminate_process()

    def _terminate_process(self):
        try:
            import psutil
            if self.child_pid:
                try:
                    # Get process and all children
                    parent = psutil.Process(self.child_pid)
                    children = parent.children(recursive=True)

                    # Kill all child processes first
                    for child in children:
                        try:
                            child.kill()
                        except psutil.NoSuchProcess:
                            pass

                    # Kill parent process
                    parent.kill()

                except psutil.NoSuchProcess:
                    self.progress.emit(self.tm.get_text("process_messages", "already_terminated"))
                except Exception as e:
                    self.progress.emit(self.tm.get_text("process_messages", "termination_error", error=str(e)))

            # Kill the shell process if it still exists
            if self.process and self.process.poll() is None:
                self.process.kill()

        except ImportError:
            self.progress.emit(self.tm.get_text("process_messages", "psutil_missing"))
            if self.child_pid:
                try:
                    if self.is_windows:
                        subprocess.run(['taskkill', '/F', '/T', '/PID', str(self.child_pid)],
                                     capture_output=True)
                    else:
                        os.killpg(os.getpgid(self.child_pid), signal.SIGKILL)
                except Exception as e:
                    self.progress.emit(self.tm.get_text("process_messages", "fallback_error", error=str(e)))

    def _cleanup(self):
        if self.process:
            try:
                self.process.stdout.close()
                self.process.stderr.close()
            except:
                pass

class MinerUGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.conda_env = "MinerU"
        self.cancel_requested = False
        self.tm = TranslationManager()

        # Set language based on system locale
        system_lang = QLocale.system().name()
        self.tm.set_language("zh" if system_lang.startswith("zh") else "en")

        self.init_ui()

    def init_ui(self):
        self.setWindowTitle(self.tm.get_text("window", "title"))

        self.setGeometry(100, 100, 800, 600)

        # Create main layout as horizontal split
        main_layout = QHBoxLayout()

        # Left panel for existing controls
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        # Move all existing widgets to left_layout

        # Input file/directory selection
        input_layout = QHBoxLayout()
        self.input_path = QLineEdit()
        input_btn = QPushButton(self.tm.get_text("buttons", "select_pdf"))
        input_btn.clicked.connect(self.select_pdf)
        input_layout.addWidget(QLabel(self.tm.get_text("labels", "input_path")))
        input_layout.addWidget(self.input_path)
        input_layout.addWidget(input_btn)
        left_layout.addLayout(input_layout)

        # Output directory selection
        output_layout = QHBoxLayout()
        self.output_path = QLineEdit()
        output_btn = QPushButton(self.tm.get_text("buttons", "select_output"))
        output_btn.clicked.connect(self.select_output)
        output_layout.addWidget(QLabel(self.tm.get_text("labels", "output_dir")))
        output_layout.addWidget(self.output_path)
        output_layout.addWidget(output_btn)
        left_layout.addLayout(output_layout)

        # Method selection
        method_layout = QHBoxLayout()
        self.method_combo = QComboBox()
        self.method_combo.addItems(['auto', 'ocr', 'txt'])
        method_layout.addWidget(QLabel(self.tm.get_text("labels", "method")))
        method_layout.addWidget(self.method_combo)
        left_layout.addLayout(method_layout)

        # Language selection
        lang_layout = QHBoxLayout()
        self.lang_input = QLineEdit()
        self.lang_input.setPlaceholderText(self.tm.get_text("placeholders", "language_input"))
        lang_layout.addWidget(QLabel(self.tm.get_text("labels", "language")))
        lang_layout.addWidget(self.lang_input)
        left_layout.addLayout(lang_layout)

        # Page range
        page_layout = QHBoxLayout()
        self.start_page = QSpinBox()
        self.end_page = QSpinBox()
        self.start_page.setMinimum(0)
        self.end_page.setMinimum(0)
        page_layout.addWidget(QLabel(self.tm.get_text("labels", "start_page")))
        page_layout.addWidget(self.start_page)
        page_layout.addWidget(QLabel(self.tm.get_text("labels", "end_page")))
        page_layout.addWidget(self.end_page)
        left_layout.addLayout(page_layout)

        # Debug mode
        debug_layout = QHBoxLayout()
        self.debug_check = QCheckBox(self.tm.get_text("labels", "debug_mode"))
        debug_layout.addWidget(self.debug_check)
        left_layout.addLayout(debug_layout)

        # Progress bar and status
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        left_layout.addWidget(self.progress_bar)

        self.status_label = QLabel(self.tm.get_text("status", "ready"))
        status_label_font = self.status_label.font()
        status_label_font.setBold(True)
        self.status_label.setFont(status_label_font)
        left_layout.addWidget(self.status_label)

        # Output log
        self.output_log = QPlainTextEdit()
        self.output_log.setReadOnly(True)
        self.output_log.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        left_layout.addWidget(self.output_log)

        # Process and Cancel Buttons
        btn_layout = QHBoxLayout()
        self.process_btn = QPushButton(self.tm.get_text("buttons", "process_pdf"))
        self.process_btn.clicked.connect(self.process_pdf)
        self.cancel_btn = QPushButton(self.tm.get_text("buttons", "cancel"))
        self.cancel_btn.clicked.connect(self.cancel_process)
        self.cancel_btn.setEnabled(False)
        btn_layout.addWidget(self.process_btn)
        btn_layout.addWidget(self.cancel_btn)
        left_layout.addLayout(btn_layout)


        # Right panel for markdown preview (hidden initially)
        self.preview_panel = QPlainTextEdit()
        self.preview_panel.setReadOnly(True)
        self.preview_panel.hide()  # Hidden by default

        # Add panels to main layout
        main_layout.addWidget(left_panel)
        main_layout.addWidget(self.preview_panel, stretch=1)

        # Set main layout
        central_widget = QWidget()
        central_widget.setLayout(main_layout)
        self.setCentralWidget(central_widget)

    def select_pdf(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            self.tm.get_text("dialogs", "select_pdf"),
            "",
            self.tm.get_text("dialogs", "pdf_filter")
        )
        if path:
            self.input_path.setText(path)
            self.output_log.setPlainText("")
            self.preview_panel.setPlainText("")
            self.preview_panel.hide()
            self.status_label.setText(self.tm.get_text("status", "ready"))

    def select_output(self):
        path = QFileDialog.getExistingDirectory(
            self,
            self.tm.get_text("dialogs", "select_output_dir")
        )
        if path:
            self.output_path.setText(path)

    def process_pdf(self):
        if not self.input_path.text() or not self.output_path.text():
            QMessageBox.warning(
                self,
                self.tm.get_text("messages", "error"),
                self.tm.get_text("messages", "select_paths")
            )
            return

        pdf_name = os.path.splitext(os.path.basename(self.input_path.text()))[0]
        output_dir = self.output_path.text()
        md_dir = os.path.join(output_dir, pdf_name)

        if os.path.exists(md_dir) and os.listdir(md_dir):
            reply = QMessageBox.question(
                self,
                self.tm.get_text("messages", "overwrite_title"),
                self.tm.get_text("messages", "overwrite_message", dir=md_dir),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes
            )
            if reply == QMessageBox.StandardButton.No:
                return

        cmd = f'magic-pdf -p "{self.input_path.text()}" -o "{self.output_path.text()}" -m {self.method_combo.currentText()}'

        if self.lang_input.text():
            cmd += f' -l {self.lang_input.text()}'

        if self.start_page.value() > 0:
            cmd += f' -s {self.start_page.value()}'

        if self.end_page.value() > 0:
            cmd += f' -e {self.end_page.value()}'

        if self.debug_check.isChecked():
            cmd += ' -d True'

        self.process_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.progress_bar.setMaximum(0)
        self.status_label.setText(self.tm.get_text("status", "processing"))
        self.output_log.clear()
        self.cancel_requested = False

        self.runner = CommandRunner(self.conda_env, cmd)
        self.runner.progress.connect(self.update_progress)
        self.runner.finished.connect(self.process_finished)
        self.runner.start()

    def cancel_process(self):
        if hasattr(self, 'runner') and self.runner.isRunning():
            self.cancel_requested = True
            self.runner.stop()
            self.status_label.setText(self.tm.get_text("status", "cancelling"))

    def update_progress(self, message):
        self.output_log.appendPlainText(message)

    def process_finished(self, success, message):
        self.progress_bar.setMaximum(100)
        self.process_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)

        if success:
            self.status_label.setText(self.tm.get_text("status", "success"))
            pdf_name = os.path.splitext(os.path.basename(self.input_path.text()))[0]
            md_path = os.path.join(self.output_path.text(), pdf_name, self.method_combo.currentText(), pdf_name + ".md")
            self.output_log.appendPlainText(
                self.tm.get_text("messages", "successful_md_path", md_path=md_path)
            )
            # Show markdown preview after successful conversion
            try:
                if os.path.exists(md_path):
                    with open(md_path, 'r', encoding='utf-8') as f:
                        markdown_content = f.read()
                        self.preview_panel.setPlainText(markdown_content)
                        self.preview_panel.show()  # Show the preview panel
            except Exception as e:
                self.output_log.appendPlainText(
                    self.tm.get_text("messages", "preview_error", error=str(e))
                )
        else:
            if self.cancel_requested:
                self.status_label.setText(self.tm.get_text("status", "cancelled"))
                self.output_log.appendPlainText(
                    self.tm.get_text("messages", "cancel_success")
                )
                # Ask user about cleanup
                pdf_name = os.path.splitext(os.path.basename(self.input_path.text()))[0]
                output_dir = self.output_path.text()
                md_dir = os.path.join(output_dir, pdf_name)
                if os.path.exists(md_dir):
                    reply = QMessageBox.question(
                        self,
                        self.tm.get_text("messages", "cancel_title"),
                        self.tm.get_text("messages", "cancel_message", md_dir=md_dir),
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.Yes
                    )
                    if reply == QMessageBox.StandardButton.Yes:
                        try:
                            shutil.rmtree(md_dir)
                            self.output_log.appendPlainText(
                                self.tm.get_text("messages", "cleanup_dir", dir=md_dir)
                            )
                        except Exception as e:
                            self.output_log.appendPlainText(
                                self.tm.get_text("messages", "cleanup_error", dir=md_dir, error=str(e))
                            )
            else:
                self.status_label.setText("Error: " + message)
def main():
    app = QApplication(sys.argv)
    window = MinerUGUI()
    window.show()
    sys.exit(app.exec())

if __name__ == '__main__':
    main()
