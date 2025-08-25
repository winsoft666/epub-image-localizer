import sys
import os
import logging
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
                             QPushButton, QLineEdit, QLabel, QTextEdit, QFileDialog, 
                             QMessageBox, QProgressBar)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
import zipfile
import shutil
from urllib.parse import urlparse
import requests
from io import BytesIO
import re
from bs4 import BeautifulSoup
import urllib.parse
import tempfile

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class ProcessThread(QThread):
    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int)
    finished_signal = pyqtSignal(bool, str)
    
    def __init__(self, epub_paths, output_dir, temp_dir_base):
        super().__init__()
        self.epub_paths = epub_paths
        self.output_dir = output_dir
        self.temp_dir_base = temp_dir_base
        
    def run(self):
        try:
            total_files = len(self.epub_paths)
            for i, epub_path in enumerate(self.epub_paths):
                self.log_signal.emit(f"开始处理EPUB文件 ({i+1}/{total_files}): {os.path.normpath(epub_path)}")
                success = self.process_epub(epub_path)
                if not success:
                    self.log_signal.emit(f"    处理文件 {os.path.normpath(epub_path)} 时出现错误")
            
            self.finished_signal.emit(True, "所有文件处理完成")
        except Exception as e:
            self.log_signal.emit(f"    处理出错: {str(e)}")
            self.finished_signal.emit(False, str(e))
    
    def process_epub(self, epub_path):
        # 创建临时目录，位于指定的临时目录基础路径下，按照指定格式命名
        epub_dir = os.path.dirname(epub_path)
        epub_name = os.path.splitext(os.path.basename(epub_path))[0]
        temp_dir = os.path.join(self.temp_dir_base, f"{epub_name}_image_localizer_temp")
        output_epub_dir = os.path.join(self.output_dir, "output")
        
        if not os.path.exists(temp_dir):
            os.makedirs(temp_dir)
        if not os.path.exists(output_epub_dir):
            os.makedirs(output_epub_dir)
            
        self.log_signal.emit("    解压EPUB文件...")
        self.progress_signal.emit(10)
        
        # 解压EPUB文件
        with zipfile.ZipFile(epub_path, 'r') as zip_ref:
            zip_ref.extractall(temp_dir)
            
        self.log_signal.emit("    EPUB文件解压完成")
        self.progress_signal.emit(30)
        
        # 查找并下载图片
            
        self.log_signal.emit("    查找并下载图片...")
        
        # 遍历解压后的文件，查找HTML文件
        html_files = []
        for root, dirs, files in os.walk(temp_dir):
            for file in files:
                if file.endswith(('.html', '.xhtml', '.htm')):
                    html_files.append(os.path.join(root, file))
        
        total_files = len(html_files)
        downloaded_count = 0
        error_count = 0
        error_messages = []
        total_handle_image_count = 0
        
        for i, file_path in enumerate(html_files):
            # self.log_signal.emit(f"处理文件: {os.path.normpath(file_path)}")
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                # 解析HTML并下载图片
                modified_content, handle_image_url_count, file_errors = self.download_images(content, file_path, temp_dir)
                total_handle_image_count += handle_image_url_count

                if handle_image_url_count > 0:
                    # 保存修改后的HTML
                    with open(file_path, 'w', encoding='utf-8') as f:
                        f.write(modified_content)
                
                # 统计错误
                if file_errors:
                    error_count += len(file_errors)
                    error_messages.extend(file_errors)
                    
                downloaded_count += 1
                progress = 30 + int((i + 1) / total_files * 40)
                self.progress_signal.emit(progress)
                
            except Exception as e:
                error_msg = f"处理文件 {os.path.normpath(file_path)} 时出错: {str(e)}"
                self.log_signal.emit(error_msg)
                error_messages.append(error_msg)
                error_count += 1
        
        self.log_signal.emit(f"    处理了 {total_files} 个HTML文件，共 {total_handle_image_count} 个图片")
        self.progress_signal.emit(70)
        
        # 输出错误汇总
        if error_messages:
            self.log_signal.emit(f"\n    处理过程中出现 {error_count} 个错误:")
            for i, error in enumerate(error_messages, 1):
                self.log_signal.emit(f"{i}. {error}")
        
        # 如果有错误，不打包EPUB文件
        if error_count > 0:
            self.progress_signal.emit(100)
            self.log_signal.emit("    由于处理过程中出现错误，跳过EPUB打包步骤")
            self.log_signal.emit(f"    临时文件保留在目录: {os.path.normpath(temp_dir)}")
            return False

        if total_handle_image_count == 0:
            self.progress_signal.emit(100)
            self.log_signal.emit("    没有处理图片，跳过EPUB打包步骤")
            self.log_signal.emit(f"    临时文件保留在目录: {os.path.normpath(temp_dir)}")
            return True

        # 重新打包EPUB
        epub_name = os.path.splitext(os.path.basename(epub_path))[0]
        output_epub_path = os.path.join(output_epub_dir, f"{epub_name}.epub")
        
        self.log_signal.emit("    重新打包EPUB文件...")
        with zipfile.ZipFile(output_epub_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(temp_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, temp_dir)
                    zipf.write(file_path, arcname)
                    
        self.progress_signal.emit(90)
        
        # 不删除临时文件
        self.log_signal.emit(f"    EPUB文件已保存到: {os.path.normpath(output_epub_path)}")
        self.log_signal.emit(f"    临时文件保留在目录: {os.path.normpath(temp_dir)}")
        self.progress_signal.emit(100)
        return True

    def download_images(self, html_content, html_path, temp_dir):
        """
        解析HTML内容，下载其中的图片并更新图片链接
        """
        soup = BeautifulSoup(html_content, 'html.parser')
        img_tags = soup.find_all('img')
        
        html_dir = os.path.dirname(html_path)
        errors = []
        handle_image_url_count = 0

        for img_tag in img_tags:
            src = img_tag.get('src')
            if not src:
                continue
                
            # 处理相对路径和绝对URL
            parsed_url = urllib.parse.urlparse(src)
            
            # 如果是网络图片，则下载
            if parsed_url.scheme in ('http', 'https'):
                try:
                    # 计算URL的MD5值作为文件名
                    import hashlib
                    url_hash = hashlib.md5(src.encode('utf-8')).hexdigest()
                    
                    # 确定文件扩展名
                    filename = os.path.basename(parsed_url.path)
                    _, ext = os.path.splitext(filename)
                    if not ext:
                        # 如果URL中没有扩展名，尝试从Content-Type获取
                        response = requests.head(src, timeout=30)
                        content_type = response.headers.get('content-type', '')
                        if 'jpeg' in content_type or 'jpg' in content_type:
                            ext = '.jpg'
                        elif 'png' in content_type:
                            ext = '.png'
                        elif 'gif' in content_type:
                            ext = '.gif'
                        else:
                            ext = '.jpg'  # 默认扩展名
                    
                    filename = url_hash + ext
                    # 使用系统本地格式的路径
                    image_path = os.path.normpath(os.path.join(html_dir, filename))
                    
                    # 如果图片已存在则跳过下载
                    if not os.path.exists(image_path):
                        response = requests.get(src, timeout=30)
                        response.raise_for_status()
                        
                        # 保存图片
                        with open(image_path, 'wb') as f:
                            f.write(response.content)
                        
                        # self.log_signal.emit(f"下载图片: {src} -> {filename}")
                    
                    # 更新HTML中的图片链接为相对路径
                    img_tag['src'] = filename
                    handle_image_url_count += 1

                except Exception as e:
                    error_msg = f"下载图片 {src} 失败: {str(e)}"
                    self.log_signal.emit(error_msg)
                    errors.append(error_msg)
        
        return str(soup), handle_image_url_count, errors

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.epub_paths = []
        self.output_dir = ""
        self.temp_dir = ""
        self.init_ui()
        
    def init_ui(self):
        self.setWindowTitle("EPUB 图像本地化工具")
        self.setGeometry(100, 100, 800, 600)
        
        # 创建中央部件
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # 创建布局
        main_layout = QVBoxLayout()
        central_widget.setLayout(main_layout)
        
        # 文件选择区域
        file_layout = QVBoxLayout()
        
        # EPUB文件选择
        epub_layout = QHBoxLayout()
        epub_label = QLabel("EPUB文件:")
        self.epub_line = QLineEdit()
        self.epub_line.setReadOnly(True)
        epub_button = QPushButton("浏览...")
        epub_button.clicked.connect(self.select_epub_files)
        
        epub_layout.addWidget(epub_label)
        epub_layout.addWidget(self.epub_line)
        epub_layout.addWidget(epub_button)
        file_layout.addLayout(epub_layout)
        
        # 输出目录选择
        output_layout = QHBoxLayout()
        output_label = QLabel("输出目录:")
        self.output_line = QLineEdit()
        self.output_line.setReadOnly(True)
        output_button = QPushButton("浏览...")
        output_button.clicked.connect(self.select_output_dir)
        
        output_layout.addWidget(output_label)
        output_layout.addWidget(self.output_line)
        output_layout.addWidget(output_button)
        file_layout.addLayout(output_layout)
        
        # 临时目录选择
        temp_layout = QHBoxLayout()
        temp_label = QLabel("临时目录:")
        self.temp_line = QLineEdit()
        self.temp_line.setReadOnly(True)
        temp_button = QPushButton("浏览...")
        temp_button.clicked.connect(self.select_temp_dir)
        
        # 设置默认临时目录为系统临时目录
        default_temp_dir = tempfile.gettempdir()
        self.temp_dir = default_temp_dir
        self.temp_line.setText(os.path.normpath(default_temp_dir))
        
        temp_layout.addWidget(temp_label)
        temp_layout.addWidget(self.temp_line)
        temp_layout.addWidget(temp_button)
        file_layout.addLayout(temp_layout)
        
        main_layout.addLayout(file_layout)
        
        # 按钮区域
        button_layout = QHBoxLayout()
        self.process_button = QPushButton("开始处理")
        self.process_button.clicked.connect(self.start_process)
        self.process_button.setEnabled(False)
        
        button_layout.addStretch()
        button_layout.addWidget(self.process_button)
        button_layout.addStretch()
        main_layout.addLayout(button_layout)
        
        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        main_layout.addWidget(self.progress_bar)
        
        # 日志输出区域
        log_label = QLabel("处理日志:")
        main_layout.addWidget(log_label)
        
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        main_layout.addWidget(self.log_text)
        
    # 修改为支持选择多个EPUB文件
    def select_epub_files(self):
        file_paths, _ = QFileDialog.getOpenFileNames(
            self, "选择EPUB文件", "", "EPUB Files (*.epub)")
        
        if file_paths:
            self.epub_paths = file_paths
            # 显示所有选择的文件名，以逗号分隔
            file_names = [os.path.basename(path) for path in file_paths]
            self.epub_line.setText(", ".join(file_names))
            self.check_start_enabled()
            
    def select_output_dir(self):
        dir_path = QFileDialog.getExistingDirectory(self, "选择输出目录")
        
        if dir_path:
            self.output_dir = dir_path
            # 使用系统本地格式的路径
            self.output_line.setText(os.path.normpath(dir_path))
            self.check_start_enabled()
            
    # 添加临时目录选择方法
    def select_temp_dir(self):
        dir_path = QFileDialog.getExistingDirectory(self, "选择临时目录")
        
        if dir_path:
            self.temp_dir = dir_path
            # 使用系统本地格式的路径
            self.temp_line.setText(os.path.normpath(dir_path))
            self.check_start_enabled()
            
    def check_start_enabled(self):
        self.process_button.setEnabled(
            bool(self.epub_paths) and bool(self.output_dir))
            
    def start_process(self):
        for epub_path in self.epub_paths:
            if not os.path.exists(epub_path):
                QMessageBox.warning(self, "错误", f"EPUB文件不存在: {epub_path}")
                return
            
        if not os.path.exists(self.output_dir):
            QMessageBox.warning(self, "错误", "输出目录不存在")
            return
            
        # 检查临时目录是否存在
        if not os.path.exists(self.temp_dir):
            QMessageBox.warning(self, "错误", "临时目录不存在")
            return
            
        # 禁用按钮，防止重复点击
        self.process_button.setEnabled(False)
        self.progress_bar.setValue(0)
        self.log_text.clear()
        
        # 启动处理线程，传递临时目录参数
        self.thread = ProcessThread(self.epub_paths, self.output_dir, self.temp_dir)
        self.thread.log_signal.connect(self.update_log)
        self.thread.progress_signal.connect(self.update_progress)
        self.thread.finished_signal.connect(self.process_finished)
        self.thread.start()
        
    def update_log(self, message):
        self.log_text.append(message)
        
    def update_progress(self, value):
        self.progress_bar.setValue(value)
        
    def process_finished(self, success, message):
        self.process_button.setEnabled(True)
        if success:
            QMessageBox.information(self, "完成", message)
        else:
            QMessageBox.critical(self, "错误", f"处理失败: {message}")

def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()