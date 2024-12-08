#!/usr/bin/env python3

import sys
import os
import json
import shutil
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QFileDialog, QComboBox, QSpinBox,
    QCheckBox, QLineEdit, QProgressBar, QMessageBox, QPlainTextEdit
)
from PyQt6.QtCore import QThread, QLocale, pyqtSignal
from magic_pdf.data.data_reader_writer import FileBasedDataWriter, FileBasedDataReader
from magic_pdf.config.make_content_config import DropMode, MakeMode
from magic_pdf.pipe.UNIPipe import UNIPipe
from magic_pdf.pipe.OCRPipe import OCRPipe
from magic_pdf.pipe.TXTPipe import TXTPipe
from loguru import logger
from detectron2.utils.logger import setup_logger
import logging

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

class SignalHandler(logging.Handler):
    """Custom handler to forward standard logging messages to Qt signal"""
    def __init__(self, signal_callback):
        super().__init__()
        self.signal_callback = signal_callback

    def emit(self, record):
        try:
            msg = self.format(record)
            self.signal_callback(msg)
        except Exception:
            self.handleError(record)

class CommandRunner(QThread):
    finished = pyqtSignal(bool, str)
    progress = pyqtSignal(str)

    def __init__(self, pdf_path, output_path, method, lang, start_page, end_page, debug):
        super().__init__()
        self.pdf_path = pdf_path
        self.output_path = output_path
        self.method = method
        self.lang = lang
        self.start_page = start_page
        self.end_page = end_page
        self.debug = debug
        self.tm = TranslationManager()
        # Setup logging handlers
        # 1. Loguru sink
        self.logger_id = logger.add(self._log_sink, level="INFO")

        # 2. Standard logging handler
        self.log_handler = SignalHandler(self._log_sink)
        self.log_handler.setFormatter(logging.Formatter('%(message)s'))
        self.log_handler.setLevel(logging.INFO)
        logging.getLogger().addHandler(self.log_handler)
        self.original_log_level = logging.getLogger().getEffectiveLevel()
        logging.getLogger().setLevel(logging.INFO)

        # 3. Setup detectron2 logger with signal handler
        self.detectron_logger = setup_logger(
            output=None,  # No file output needed
            distributed_rank=0,
            name="detectron2",
            color=False  # Disable colors for GUI output
        )
        self.detectron_logger.addHandler(self.log_handler)

    def _log_sink(self, message):
        """Common sink for both loguru and standard logging"""
        # For loguru messages
        if hasattr(message, 'record'):
            self.progress.emit(message.record["message"])
        # For standard logging messages
        else:
            self.progress.emit(str(message))

    def _cleanup_logging(self):
        """Clean up all logging handlers"""
        try:
            # Remove loguru sink
            logger.remove(self.logger_id)

            # Remove standard logging handler and restore original level
            root_logger = logging.getLogger()
            root_logger.removeHandler(self.log_handler)
            root_logger.setLevel(self.original_log_level)

            # Remove handler from detectron2 logger
            self.detectron_logger.removeHandler(self.log_handler)

        except Exception as e:
            print(f"Error cleaning up logging: {e}")

    def run(self):
        self.progress.emit(self.tm.get_text("process_messages", "start_process"))
        try:
            # Dynamically choose directories and method-based pipe
            pdf_file_name = os.path.basename(self.pdf_path)
            pdf_name_no_ext = os.path.splitext(pdf_file_name)[0]

            local_image_dir = os.path.join(self.output_path, pdf_name_no_ext, self.method, "images")
            local_md_dir = os.path.join(self.output_path, pdf_name_no_ext, self.method)

            os.makedirs(local_image_dir, exist_ok=True)
            os.makedirs(local_md_dir, exist_ok=True)

            image_writer = FileBasedDataWriter(local_image_dir)
            md_writer = FileBasedDataWriter(local_md_dir)
            reader = FileBasedDataReader("")

            self.progress.emit(self.tm.get_text("process_messages", "reading_pdf"))
            pdf_bytes = reader.read(self.pdf_path)

            self.end_page = None if self.end_page == 0 else self.end_page

            # Determine which pipeline to use based on method
            if self.method == "auto":
                pipe = UNIPipe(
                    pdf_bytes,
                    jso_useful_key = {
                        '_pdf_type': '',
                        "model_list": []
                    },
                    image_writer=image_writer,
                    lang=self.lang,
                    is_debug=self.debug,
                    start_page_id=self.start_page,
                    end_page_id=self.end_page
                )
            elif self.method == "ocr":
                pipe = OCRPipe(
                    pdf_bytes,
                    image_writer=image_writer,
                    lang=self.lang,
                    is_debug=self.debug,
                    start_page_id=self.start_page,
                    end_page_id=self.end_page
                )
            elif self.method == "txt":
                pipe = TXTPipe(
                    pdf_bytes,
                    image_writer=image_writer,
                    lang=self.lang,
                    is_debug=self.debug,
                    start_page_id=self.start_page,
                    end_page_id=self.end_page
                )

            self.progress.emit(self.tm.get_text("process_messages", "classifying"))
            pipe.pipe_classify()
            self.progress.emit(self.tm.get_text("process_messages", "analyzing"))
            pipe.pipe_analyze()
            self.progress.emit(self.tm.get_text("process_messages", "parsing"))
            pipe.pipe_parse()
            self.progress.emit(self.tm.get_text("process_messages", "making_markdown"))
            md_content = pipe.pipe_mk_markdown(
                os.path.basename(local_image_dir), drop_mode=DropMode.NONE, md_make_mode=MakeMode.MM_MD
            )
            # Write the markdown file
            md_filename = f"{pdf_name_no_ext}.md"
            if isinstance(md_content, list):
                md_content_str = "\n".join(md_content)
            else:
                md_content_str = md_content

            md_writer.write_string(md_filename, md_content_str)

            # If we made it this far, it's successful
            self.finished.emit(True, self.tm.get_text("process_messages", "process_success"))

        except Exception as e:
            self.progress.emit(self.tm.get_text("process_messages", "stderr_prefix") + str(e))
            self.finished.emit(False, self.tm.get_text("messages", "process_error", msg=str(e)))
        finally:
            # Clean up logging handlers
            self._cleanup_logging()


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

        pdf_path = self.input_path.text()
        method = self.method_combo.currentText()
        lang = self.lang_input.text().strip()
        start_page = self.start_page.value()
        end_page = self.end_page.value()
        debug = self.debug_check.isChecked()

        self.process_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.progress_bar.setMaximum(0)
        self.status_label.setText(self.tm.get_text("status", "processing"))
        self.output_log.clear()
        self.cancel_requested = False

        # Pass parameters directly to CommandRunner
        self.runner = CommandRunner(pdf_path, output_dir, method, lang, start_page, end_page, debug)
        self.runner.progress.connect(self.update_progress)
        self.runner.finished.connect(self.process_finished)
        self.runner.start()

    def cancel_process(self):
        if hasattr(self, 'runner') and self.runner.isRunning():
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
            self.output_log.appendPlainText(self.tm.get_text("process_messages", "user_cancelled"))
            self.close()

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
            self.status_label.setText("Error: " + message)

def main():
    app = QApplication(sys.argv)
    try:
        window = MinerUGUI()
        window.show()
        return app.exec()
    except Exception as e:
        print(f"Error initializing application: {e}")
        return 1

if __name__ == '__main__':
    sys.exit(main())
