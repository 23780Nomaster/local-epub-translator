"""llama-server lifecycle management and OpenAI-compatible translation client."""
import socket
import subprocess
import time
import signal
import os
import sys
import requests

IS_WINDOWS = sys.platform == "win32"

# Directory where the exe/binary lives (for bundled dependency detection)
_EXE_DIR = os.path.dirname(os.path.abspath(sys.executable))


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _server_binary_name() -> str:
    return "llama-server.exe" if IS_WINDOWS else "llama-server"


def _find_server_binary() -> str:
    """Find llama-server binary. Search order:
    1. Bundled alongside the exe (portable package)
    2. Bundled in bin/ subdirectory
    3. User install: ~/.local/llama-cpp/
    4. System PATH (bare binary name)
    """
    name = _server_binary_name()
    candidates = [
        os.path.join(_EXE_DIR, name),
        os.path.join(_EXE_DIR, "bin", name),
        os.path.join(os.path.expanduser("~"), ".local", "llama-cpp", name),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return name


class TranslationServer:
    def __init__(self, model_path: str, llama_bin_dir: str = None):
        self.model_path = os.path.expanduser(model_path)
        if llama_bin_dir is not None:
            self.server_bin = os.path.join(os.path.expanduser(llama_bin_dir), _server_binary_name())
        else:
            self.server_bin = _find_server_binary()
        self.port = _find_free_port()
        self._proc = None

    def start(self):
        bin_dir = os.path.dirname(self.server_bin)
        env = os.environ.copy()

        if IS_WINDOWS:
            env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")
        else:
            env["LD_LIBRARY_PATH"] = bin_dir + ":" + env.get("LD_LIBRARY_PATH", "")

        popen_kwargs = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.PIPE,
            "env": env,
        }
        if IS_WINDOWS:
            popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore

        self._proc = subprocess.Popen(
            [
                self.server_bin,
                "-m", self.model_path,
                "--port", str(self.port),
                "-ngl", "0",
                "--ctx-size", "4096",
                "--batch-size", "512",
                "--no-webui",
            ],
            **popen_kwargs,
        )
        self._wait_ready()

    def _wait_ready(self, timeout: float = 120):
        url = f"http://127.0.0.1:{self.port}/health"
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._proc.poll() is not None:
                stderr = ""
                if self._proc.stderr:
                    stderr = self._proc.stderr.read().decode(errors="replace")
                raise RuntimeError(f"llama-server exited early: {stderr}")
            try:
                r = requests.get(url, timeout=2)
                if r.status_code == 200:
                    return
            except (requests.ConnectionError, requests.Timeout):
                pass
            time.sleep(0.5)
        raise TimeoutError(f"llama-server did not become ready within {timeout}s")

    def stop(self):
        if self._proc:
            if IS_WINDOWS:
                self._proc.terminate()
            else:
                self._proc.send_signal(signal.SIGTERM)
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()

    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


class Translator:
    def __init__(self, server: TranslationServer, target_lang: str = "English"):
        self.server = server
        self.target_lang = target_lang

    def translate(self, text: str) -> str:
        prompt = (
            f"将以下文本翻译成{self.target_lang}，"
            f"注意只需要输出翻译后的结果，不要额外解释：\n\n{text}"
        )
        payload = {
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "top_p": 1.0,
            "max_tokens": 4096,
        }
        r = requests.post(
            f"{self.server.base_url()}/v1/chat/completions",
            json=payload,
            timeout=120,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
