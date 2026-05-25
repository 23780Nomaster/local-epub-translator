"""llama-server lifecycle management and OpenAI-compatible translation client."""
import socket
import subprocess
import time
import signal
import os
import requests


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


class TranslationServer:
    def __init__(self, model_path: str, llama_bin_dir: str = "~/.local/llama-cpp"):
        self.model_path = os.path.expanduser(model_path)
        self.llama_bin_dir = os.path.expanduser(llama_bin_dir)
        self.port = _find_free_port()
        self._proc = None

    def start(self):
        server_bin = os.path.join(self.llama_bin_dir, "llama-server")
        lib_dir = self.llama_bin_dir
        env = os.environ.copy()
        env["LD_LIBRARY_PATH"] = lib_dir + ":" + env.get("LD_LIBRARY_PATH", "")

        self._proc = subprocess.Popen(
            [
                server_bin,
                "-m", self.model_path,
                "--port", str(self.port),
                "-ngl", "0",
                "--ctx-size", "4096",
                "--batch-size", "512",
                "--no-webui",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            env=env,
        )
        self._wait_ready()

    def _wait_ready(self, timeout: float = 120):
        url = f"http://127.0.0.1:{self.port}/health"
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._proc.poll() is not None:
                stderr = self._proc.stderr.read().decode(errors="replace") if self._proc.stderr else ""
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
