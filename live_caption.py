#!/usr/bin/env python3
"""
实时扬声器字幕 - Live Caption Overlay
========================================
捕获系统扬声器输出 → Whisper 实时语音识别 → 悬浮字幕显示

功能:
  - 监听系统扬声器/音频输出，实时转写字幕
  - 半透明悬浮窗显示，类似视频内置字幕效果
  - 鼠标拖动字幕窗口任意调整位置
  - 滚轮调节字号 | 右键菜单退出 | Esc 退出

使用:
  python live_caption.py
"""

import threading
import queue
import time
import sys
import os
import warnings
from datetime import datetime
import numpy as np
import tkinter as tk
from tkinter import font as tkfont, messagebox

# ── 抑制 soundcard 录制抖动警告（模型加载期间正常现象）───
warnings.filterwarnings("ignore", module=r"soundcard\..*")

# ── 模型下载目录（脚本所在目录下的 models 文件夹）─────────
os.environ["HF_HOME"] = os.path.join(os.path.dirname(
    os.path.abspath(__file__)), "models")
os.environ["HUGGINGFACE_HUB_CACHE"] = os.environ["HF_HOME"]
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"  # 国内镜像，不用翻墙

# ── 音频捕获 ──────────────────────────────────────────────
try:
    import soundcard as sc
    HAS_SOUNDCARD = True
except ImportError:
    HAS_SOUNDCARD = False

# ── 语音识别 ──────────────────────────────────────────────
try:
    from faster_whisper import WhisperModel
    HAS_WHISPER = True
except ImportError:
    HAS_WHISPER = False

# ── 配置 ──────────────────────────────────────────────────
SAMPLE_RATE = 16000               # 音频采样率
CHUNK_DURATION = 0.5              # 每次采集的音频长度（秒），越小响应越快
SILENCE_THRESHOLD = 0.008         # RMS 能量阈值，低于此值视为静音
SILENCE_GAP = 0.6                 # 连续静音多少秒后认为一句话结束（越小延迟越低）
MAX_SPEECH_DURATION = 5.0         # 单段语音最长秒数
MODEL_SIZE = "base"               # tiny(最快)/base(快)/small(均衡)/medium(准)/large(最准)
DEVICE = "cuda"                    # cpu 或 cuda
COMPUTE_TYPE = "float16"          # int8 (CPU) / float16 (GPU) / int8_float16
LANGUAGE = None                   # None=自动检测, "zh"=中文, "en"=英文

# ── UI 默认参数 ───────────────────────────────────────────
DEFAULT_FONT_SIZE = 28
DEFAULT_WIN_WIDTH = 900
DEFAULT_WIN_HEIGHT = 130
DEFAULT_BG_ALPHA = 0.75           # 窗口透明度 (0~1)


# ╔══════════════════════════════════════════════════════════╗
# ║              悬浮字幕窗口  (CaptionOverlay)              ║
# ╚══════════════════════════════════════════════════════════╝

class CaptionOverlay:
    """无边框、置顶、半透明的悬浮字幕窗口，可拖动。"""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Live Caption")

        # ── 窗口属性 ──
        self.root.overrideredirect(True)          # 无边框
        self.root.attributes('-topmost', True)    # 始终置顶
        self.root.attributes('-alpha', DEFAULT_BG_ALPHA)
        self.root.configure(bg='#1a1a1a')

        # ── 初始位置：屏幕底部居中 ──
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = (sw - DEFAULT_WIN_WIDTH) // 2
        y = sh - DEFAULT_WIN_HEIGHT - 60
        self.root.geometry(f"{DEFAULT_WIN_WIDTH}x{DEFAULT_WIN_HEIGHT}+{x}+{y}")

        # ── 字体 ──
        self.font_size = DEFAULT_FONT_SIZE
        self._font = tkfont.Font(
            family="Microsoft YaHei", size=self.font_size, weight="bold"
        )

        # ── 字幕标签（全窗口填充） ──
        self.label = tk.Label(
            self.root,
            text="⏳ 正在加载语音模型，请稍候...",
            font=self._font,
            fg="#aaaaaa",
            bg='#1a1a1a',
            wraplength=DEFAULT_WIN_WIDTH - 60,
            justify="center",
        )
        self.label.pack(expand=True, fill="both", padx=30, pady=15)

        # ── 拖动 ──
        self._drag_start_x = 0
        self._drag_start_y = 0
        for widget in (self.label, self.root):
            widget.bind("<Button-1>", self._on_drag_start)
            widget.bind("<B1-Motion>", self._on_drag_move)

        # ── 滚轮调字号 ──
        for widget in (self.label, self.root):
            widget.bind("<MouseWheel>", self._on_mousewheel)

        # ── 键盘 ──
        self.root.bind("<Escape>", lambda e: self.close())

        # ── 右键菜单 ──
        self._menu = tk.Menu(self.root, tearoff=0)
        self._menu.add_command(label="字号 +", command=self._increase_font)
        self._menu.add_command(label="字号 -", command=self._decrease_font)
        self._menu.add_separator()
        self._menu.add_command(label="退出 (Esc)", command=self.close)
        for widget in (self.label, self.root):
            widget.bind("<Button-3>", self._show_menu)

        self._running = True

    # ── 拖动 ──────────────────────────────────────────────

    def _on_drag_start(self, event):
        self._drag_start_x = event.x
        self._drag_start_y = event.y

    def _on_drag_move(self, event):
        dx = event.x - self._drag_start_x
        dy = event.y - self._drag_start_y
        x = self.root.winfo_x() + dx
        y = self.root.winfo_y() + dy
        self.root.geometry(f"+{x}+{y}")

    # ── 字号调节 ──────────────────────────────────────────

    def _on_mousewheel(self, event):
        delta = 1 if event.delta > 0 else -1
        new_size = self.font_size + delta
        if 12 <= new_size <= 80:
            self.font_size = new_size
            self._font.configure(size=self.font_size)

    def _increase_font(self):
        if self.font_size < 80:
            self.font_size += 2
            self._font.configure(size=self.font_size)

    def _decrease_font(self):
        if self.font_size > 12:
            self.font_size -= 2
            self._font.configure(size=self.font_size)

    # ── 右键菜单 ──────────────────────────────────────────

    def _show_menu(self, event):
        try:
            self._menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._menu.grab_release()

    # ── 公开方法 ──────────────────────────────────────────

    def set_text(self, text: str):
        """线程安全地更新字幕文本。"""
        if not self._running:
            return
        self.root.after(0, self._apply_text, text)

    def _apply_text(self, text: str):
        """必须在主线程调用。"""
        try:
            self.label.config(text=text, fg="white")
        except tk.TclError:
            pass

    def close(self):
        self._running = False
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def run(self):
        """阻塞运行 Tk 主循环。"""
        self.root.mainloop()


# ╔══════════════════════════════════════════════════════════╗
# ║              音频捕获  (AudioCapture)                   ║
# ╚══════════════════════════════════════════════════════════╝

class AudioCapture:
    """后台线程：持续从扬声器回采音频。"""

    def __init__(self, audio_queue: queue.Queue):
        self._q = audio_queue
        self._running = False
        self._thread = None

    def start(self):
        if not HAS_SOUNDCARD:
            print("[错误] 缺少 soundcard 库，请运行: pip install soundcard")
            self._q.put(None)
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

    def _loop(self):
        try:
            speaker = sc.default_speaker()
            # 通过 loopback 模式捕获扬声器输出
            mic = sc.get_microphone(speaker.name, include_loopback=True)
            chunk_size = int(SAMPLE_RATE * CHUNK_DURATION)
            print(f"[音频] 扬声器: {speaker.name}  |  采样率: {SAMPLE_RATE} Hz")
            with mic.recorder(samplerate=SAMPLE_RATE, channels=1) as rec:
                while self._running:
                    data = rec.record(numframes=chunk_size)
                    audio = data.flatten().astype(np.float32)
                    self._q.put(audio)
        except Exception as e:
            print(f"[音频] 捕获异常: {e}")
            self._q.put(None)


# ╔══════════════════════════════════════════════════════════╗
# ║           语音识别  (Transcriber)                       ║
# ╚══════════════════════════════════════════════════════════╝

class Transcriber:
    """后台线程：VAD + Whisper 实时转录，同时保存字幕到文件。"""

    def __init__(self, audio_queue: queue.Queue, on_text, save_path: str = None):
        self._q = audio_queue
        self._on_text = on_text
        self._save_path = save_path
        self._log_file = None
        self._running = False
        self._thread = None
        # VAD 状态
        self._buffer = []          # 当前正在说的话的音帧列表
        self._silence_count = 0    # 连续静音帧计数
        self._speaking = False

    def start(self):
        if not HAS_WHISPER:
            print("[错误] 缺少 faster-whisper 库，请运行: pip install faster-whisper")
            self._on_text("⚠️ 缺少 faster-whisper，请安装后重试")
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        if self._log_file:
            self._log_file.close()
            self._log_file = None

    @staticmethod
    def _is_speech(audio: np.ndarray) -> bool:
        return float(np.sqrt(np.mean(audio ** 2))) > SILENCE_THRESHOLD

    def _loop(self):
        # ── 打开字幕日志文件 ──
        if self._save_path:
            try:
                os.makedirs(os.path.dirname(self._save_path), exist_ok=True)
                self._log_file = open(self._save_path, "a", encoding="utf-8")
                self._log_file.write(
                    f"=== 字幕记录 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===\n\n"
                )
                self._log_file.flush()
                print(f"[记录] 字幕保存至: {self._save_path}")
            except Exception as e:
                print(f"[记录] 无法创建日志文件: {e}")

        print(f"[识别] 正在加载 Whisper 模型 '{MODEL_SIZE}'...")
        print("[识别] （首次运行会自动下载模型，约 1-2 GB，请稍候）")
        try:
            model = WhisperModel(MODEL_SIZE, device=DEVICE,
                                 compute_type=COMPUTE_TYPE)
        except Exception as e:
            print(f"[识别] 模型加载失败: {e}")
            self._on_text(f"❌ 模型加载失败: {e}")
            return
        print("[识别] 模型就绪，开始监听...")
        self._on_text("🎤 正在监听中...")

        silence_limit = int(SILENCE_GAP / CHUNK_DURATION)
        max_chunks = int(MAX_SPEECH_DURATION / CHUNK_DURATION)

        while self._running:
            try:
                audio = self._q.get(timeout=0.3)
            except queue.Empty:
                continue

            if audio is None:               # 错误信号
                break

            is_speech = self._is_speech(audio)

            if is_speech:
                if not self._speaking:
                    self._speaking = True
                    self._buffer.clear()
                self._buffer.append(audio)
                self._silence_count = 0
            else:
                if self._speaking:
                    self._buffer.append(audio)
                    self._silence_count += 1
                    if (self._silence_count >= silence_limit or
                            len(self._buffer) >= max_chunks):
                        self._transcribe(model)
                        self._speaking = False
                        self._buffer.clear()
                        self._silence_count = 0

        print("[识别] 线程已退出")

    def _transcribe(self, model):
        if len(self._buffer) < 3:
            return
        audio = np.concatenate(self._buffer)
        try:
            segments, _ = model.transcribe(
                audio,
                beam_size=5,
                language=LANGUAGE,
                vad_filter=True,
                vad_parameters=dict(
                    threshold=0.5,
                    min_speech_duration_ms=250,
                    min_silence_duration_ms=300,
                ),
            )
            text = " ".join(seg.text.strip() for seg in segments)
            if text.strip():
                ts = datetime.now().strftime("%H:%M:%S")
                print(f"[字幕] [{ts}] {text}")
                self._on_text(text)
                # ── 写入日志文件 ──
                if self._log_file:
                    self._log_file.write(f"[{ts}] {text}\n")
                    self._log_file.flush()
        except Exception as e:
            print(f"[识别] 转录错误: {e}")


# ╔══════════════════════════════════════════════════════════╗
# ║                    主入口  (main)                       ║
# ╚══════════════════════════════════════════════════════════╝

def check_dependencies():
    """检查关键依赖是否安装，缺少则给出提示。"""
    missing = []
    if not HAS_SOUNDCARD:
        missing.append("soundcard")
    if not HAS_WHISPER:
        missing.append("faster-whisper")
    if missing:
        print(f"[警告] 缺少以下库: {', '.join(missing)}")
        print(f"[提示] 请运行: pip install {' '.join(missing)}")
        return False
    return True


def main():
    # 字幕保存路径（脚本同目录，按日期命名）
    script_dir = os.path.dirname(os.path.abspath(__file__))
    save_path = os.path.join(
        script_dir, f"captions_{datetime.now().strftime('%Y%m%d')}.txt"
    )

    print("╔══════════════════════════════════════════════╗")
    print("║       实时扬声器字幕  Live Caption          ║")
    print("╠══════════════════════════════════════════════╣")
    print("║  拖动窗口 -> 调整字幕位置                   ║")
    print("║  滚轮     -> 调节字号                       ║")
    print("║  右键     -> 菜单（字号/退出）              ║")
    print("║  Esc      -> 退出                           ║")
    print("╠══════════════════════════════════════════════╣")
    print(f"║  字幕记录 -> {os.path.basename(save_path)}")
    print("╚══════════════════════════════════════════════╝")

    if not check_dependencies():
        input("按回车键退出...")
        sys.exit(1)

    q = queue.Queue()

    # 字幕窗口（主线程）
    overlay = CaptionOverlay()

    # 音频捕获 & 识别（后台线程）
    capture = AudioCapture(q)
    transcriber = Transcriber(q, overlay.set_text, save_path)

    capture.start()
    transcriber.start()

    try:
        overlay.run()
    except KeyboardInterrupt:
        pass
    finally:
        print("\n[系统] 正在退出...")
        capture.stop()
        transcriber.stop()
        print("[系统] 再见！")


if __name__ == "__main__":
    main()
