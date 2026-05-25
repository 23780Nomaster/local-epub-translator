# local-epub-translator

基于腾讯 Hy-MT2 模型翻译、Tesseract 本地 OCR 识别的离线 EPUB 翻译工具。全部代码由 DeepSeekV4-pro 撰写。

## 功能

- **文本翻译** — 直接提取 EPUB 中的文本块并翻译
- **扫描文档 OCR** — 自动检测图片型 EPUB，OCR 识别后翻译
- **断点续传** — Ctrl+C 中断后，重新运行自动从断点继续
- **Web 图形界面** — Flask 驱动的可视化操作界面，支持暂停/继续/硬件监控
- **智能过滤** — 自动跳过装饰性图片（分隔线、封面等）

## 安装

### 1. 系统依赖

```bash
# Fedora
sudo dnf install tesseract tesseract-langpack-chi-sim tesseract-langpack-eng

# Ubuntu/Debian
sudo apt install tesseract-ocr tesseract-ocr-chi-sim tesseract-ocr-eng
```

### 2. 下载翻译模型

```bash
mkdir -p ~/models
curl -L -o ~/models/Hy-MT2-1.8B-Q8_0.gguf \
  https://huggingface.co/tencent/Hy-MT2-1.8B-GGUF/resolve/main/Hy-MT2-1.8B-Q8_0.gguf
```

### 3. 安装 llama.cpp 服务器

```bash
# 编译安装 llama.cpp（或下载预编译二进制）
git clone https://github.com/ggerganov/llama.cpp.git
cd llama.cpp && make llama-server
mkdir -p ~/.local/llama-cpp
cp llama-server ~/.local/llama-cpp/
```

### 4. 安装 Python 依赖

```bash
pip install -r requirements.txt
```

## 使用

### 命令行

```bash
# 文本文档翻译
python translate.py book.epub --target-lang English

# 指定输出路径
python translate.py book.epub -o /path/to/output.epub

# 翻译为简体中文（默认）
python translate.py book.epub --target-lang "Simplified Chinese"
```

### Web 图形界面

```bash
python gui.py
# 打开浏览器访问 http://127.0.0.1:5050
```

界面功能：文件浏览选择、开始/暂停/恢复翻译、实时进度条、CPU/内存监控。

## 项目结构

```
epub-translator/
├── translate.py      # 命令行入口
├── gui.py            # Web 图形界面（Flask）
├── epub_handler.py   # EPUB 解析、文本/图片提取、翻译写入
├── llm_backend.py    # llama-server 管理 + OpenAI 兼容 API 客户端
├── ocr_backend.py    # Tesseract OCR 后端
├── checkpoint.py     # 断点续传状态管理
└── requirements.txt
```

## 依赖模型

| 组件 | 模型/引擎 | 说明 |
|------|----------|------|
| 翻译 | Hy-MT2-1.8B (GGUF) | 腾讯翻译模型，1.8B 参数，本地运行 |
| OCR | Tesseract 5.x | 开源 OCR 引擎，支持中英文 |
| 推理 | llama.cpp server | OpenAI 兼容 API 服务端 |

全部离线部署，无需云 API 密钥。
