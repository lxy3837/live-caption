# Live Caption - 实时扬声器字幕

> 傻逼中期实训，给一堆视频看，结果连个字幕都没有，听都听不清，看个锤子。

## 干啥用的

监听电脑扬声器的声音，实时转成字幕飘在屏幕上。就跟看视频开字幕一样，**任何声音都能出字幕** —— 网页视频、播放器、在线会议，只要是扬声器出声的统统能识别。

## 效果

- 半透明悬浮窗，置顶显示，不挡画面
- 鼠标随便拖，爱放哪放哪
- 滚轮调字号，想大就大想小就小
- 字幕自动保存到本地文件，回头还能翻

## 快速开始

### 1. 装依赖

```bash
pip install -r requirements.txt
```

### 2. 跑起来

```bash
python live_caption.py
```

首次运行会自动下载 Whisper 模型（约 1 GB），存在当前目录 `models/` 下。

## 操作

| 操作 | 说明 |
|------|------|
| 拖动窗口 | 移动字幕位置 |
| 鼠标滚轮 | 放大/缩小字号 |
| 右键 | 菜单（字号、退出） |
| Esc | 退出 |

## 原理

```
扬声器输出 → WASAPI Loopback 捕获 → VAD 语音检测 → Whisper 识别 → tkinter 悬浮窗显示
```

- 音频捕获：`soundcard` 库，WASAPI loopback 回采扬声器
- 语音识别：`faster-whisper`，默认 `small` 模型，CPU 可用
- 前端：`tkinter` 无边框透明窗口

## 配置

在 `live_caption.py` 顶部可改：

```python
MODEL_SIZE = "small"   # tiny/base/small/medium/large，越大越准越慢
DEVICE = "cpu"         # cpu 或 cuda
LANGUAGE = None        # None=自动检测, "zh"=中文, "en"=英文
```

## 文件结构

```
├── live_caption.py    # 主程序
├── requirements.txt   # 依赖
├── captions_*.txt     # 字幕记录（自动生成）
└── models/            # Whisper 模型（自动下载）
```

## 反馈 & 贡献

有问题、有建议、想吐槽？欢迎提 [Issue](https://github.com/lxy3837/live-caption/issues)，看到了会回。

## License

MIT
