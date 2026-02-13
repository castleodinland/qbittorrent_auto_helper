import os
import subprocess
import math
import sys
import re
import shutil
import json
import re
import datetime
from pathlib import Path

from urllib import request, parse
# 尝试导入 requests，如果没有则提示安装
try:
    import requests
except ImportError:
    print("正在尝试自动安装必要的上传库 requests...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests"])
    import requests


PRO_VER = "v2.0.0"

# 任务字典：{ "视频目录路径": "中文副标题" }
TASKS = {
    "/home/pt_main/docker_upload_s/nba/NBA RS 2026 Washington Wizards vs New York Knicks 11 02 720pEN60fps FDSN",
    "/home/pt_main/docker_upload_s/nba/NBA RS 2026 Atlana Hawks vs Charlotte Hornets 11 02 720pEN60fps FDSN",
    "/home/pt_main/docker_upload_s/nba/NBA RS 2026 Oklahoma City Thunder vs Phoenix Suns 11 02 720pEN60fps FDSN",
}
OUTPUT_BASE_DIR = "/home/pt_main/docker_upload_s/nba/"

# ================= 配置区域 =================
# 参数 1: 你的 PT 站 Announce URL (Passkey)
# ANNOUNCE_URL = "https://tracker.qingwapt.com/announce.php"
# ANNOUNCE_URL = "https://rousi.pro/tracker/1d3ba4125577007e0d8c4b1d2527375a/announce"
ANNOUNCE_URL = " https://t.ubits.club/announce.php"

# 参数 2: 需要做种的完整目录路径 (末尾不要带斜杠)
# TARGET_DIR = "D:\pt_main\group_of_video\HHQuietSheMightHearU"

# --- 新增功能配置 ---
# 截图画质 (1-31, 1最好, 31最差, 建议 3-5 保持在 500k 左右)
SCREENSHOT_QUALITY = 3

# 是否启用 Pixhost 上传功能
# ENABLE_UPLOAD = False
ENABLE_UPLOAD = True
# ===========================================


# --- 新增球队对战翻译功能 ---
# ===========================================

def translate_nba_info(raw_string):
    """
    将原始 NBA 比赛信息字符串翻译为中文格式。
    支持大小写不敏感匹配，并能自动适配有无冗余后缀的情况。
    """

    # --- 1. 配置区域 ---
    
    # 比赛类型对照表 (Key 统一小写)
    event_types = {
        "rs": "常规赛",
        "playoff": "季后赛",
        "all-star": "全明星",
        "finals": "总决赛",
        "preseason": "季前赛",
        "in-season tournament": "季中锦标赛"
    }

    # 球队名称对照表 (Key 统一小写)
    team_map = {
        "atlanta hawks": "亚特兰大老鹰",
        "boston celtics": "波士顿凯尔特人",
        "brooklyn nets": "布鲁克林篮网",
        "charlotte hornets": "夏洛特黄蜂",
        "chicago bulls": "芝加哥公牛",
        "cleveland cavaliers": "克利夫兰骑士",
        "detroit pistons": "底特律活塞",
        "indiana pacers": "印第安纳步行者",
        "miami heat": "迈阿密热火",
        "milwaukee bucks": "密尔沃基雄鹿",
        "new york knicks": "纽约尼克斯",
        "orlando magic": "奥兰多魔术",
        "philadelphia 76ers": "费城76人",
        "toronto raptors": "多伦多猛龙",
        "washington wizards": "华盛顿奇才",
        "dallas mavericks": "达拉斯独行侠",
        "denver nuggets": "丹佛掘金",
        "golden state warriors": "金州勇士",
        "houston rockets": "休斯顿火箭",
        "los angeles clippers": "洛杉矶快船",
        "los angeles lakers": "洛杉矶湖人",
        "memphis grizzlies": "孟菲斯灰熊",
        "minnesota timberwolves": "明尼苏达森林狼",
        "new orleans pelicans": "新奥尔良鹈鹕",
        "oklahoma city thunder": "俄克拉荷马城雷霆",
        "phoenix suns": "菲尼克斯太阳",
        "portland trail blazers": "波特兰开拓者",
        "sacramento kings": "萨克拉门托国王",
        "san antonio spurs": "圣安东尼奥马刺",
        "utah jazz": "犹他爵士",
        
        "atlana hawks": "亚特兰大老鹰",
    }

    source_suffix = "英文解说 转自sportscult"

    # --- 2. 解析逻辑 ---

    parts = raw_string.split()
    if len(parts) < 8:
        return f"Error: String too short -> {raw_string}"

    # 1. NBA 前缀
    nba_prefix = parts[0]
    
    # 2. 比赛类型 (不区分大小写)
    event_cn = event_types.get(parts[1].lower(), parts[1])
    
    # 3. 年份
    year = parts[2]
    
    # 4. 定位 "vs" 和 "画质" 锚点
    try:
        vs_idx = -1
        for i, p in enumerate(parts):
            if p.lower() == "vs":
                vs_idx = i
                break
        
        # 定位画质单词 (包含 p, fps 或以数字开头带 p 的特征)
        quality_idx = -1
        for i in range(len(parts)-1, vs_idx, -1):
            if re.search(r'\d{3,4}p', parts[i].lower()):
                quality_idx = i
                break
        
        if vs_idx == -1 or quality_idx == -1:
            return f"Error: Format error (vs/quality) -> {raw_string}"

        # 5. 提取日期：画质前面的两个单词分别是 月 和 日
        month = parts[quality_idx - 2]
        day = parts[quality_idx - 1]
        quality = parts[quality_idx]

        # 6. 提取队名：
        # 队 A 是在年份 (index 2) 之后到 vs (vs_idx) 之前
        team_a_raw = " ".join(parts[3:vs_idx])
        # 队 B 是在 vs 之后到月份 (quality_idx - 2) 之前
        team_b_raw = " ".join(parts[vs_idx + 1 : quality_idx - 2])

        # 7. 查表 (不区分大小写)
        team_a_cn = team_map.get(team_a_raw.lower(), team_a_raw)
        team_b_cn = team_map.get(team_b_raw.lower(), team_b_raw)

        return f"{nba_prefix} {event_cn} {year}-{month}-{day}比赛日 {team_a_cn} vs {team_b_cn} {quality} {source_suffix}"

    except Exception as e:
        return f"Error: {str(e)} during parsing '{raw_string}'"
# ===========================================

def write_custom_log(file_path, message, mode='a'):
    """
    在指定文件中写入 UTC+8 时间戳和自定义字符串。
    
    参数:
    file_path (str): 目标文件的路径
    message (str): 想要写入的自定义字符串
    mode (str): 写入模式，'a' 为追加（默认），'w' 为覆盖
    """
    try:
        # 1. 获取当前 UTC 时间
        utc_now = datetime.datetime.now(datetime.timezone.utc)
        
        # 2. 转换为 UTC+8 (北京时间)
        # 将之前的 -8 改为 +8
        utc_plus_8 = utc_now + datetime.timedelta(hours=8)
        
        # 3. 格式化时间戳 (例如: 2026-02-12 21:15:30)
        timestamp = utc_plus_8.strftime('%Y-%m-%d %H:%M:%S')
        
        # 4. 组合最终写入的内容
        log_entry = f"[{timestamp} UTC+8] {message}\n"
        
        # 5. 执行文件写入
        # 默认模式 'a' 即为追加写入
        with open(file_path, mode, encoding='utf-8') as f:
            f.write(log_entry)
            
        print(f"成功写入到: {file_path}")
        
    except Exception as e:
        print(f"发生错误: {e}")
        

def get_dir_size(path):
    """计算目录总大小 (Bytes)"""
    total_size = 0
    for dirpath, dirnames, filenames in os.walk(path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            if os.path.exists(fp):
                total_size += os.path.getsize(fp)
    return total_size

def get_optimal_piece_size(total_bytes):
    """根据总大小返回 mktorrent 的 -l 参数"""
    # 1GB = 1073741824 bytes
    gb_size = total_bytes / (1024**3)
    if gb_size < 10: return 21    # 2MB
    if gb_size < 50: return 22    # 4MB
    if gb_size < 150: return 23   # 8MB
    return 24                      # 16MB

def run_command(cmd, log_file=None):
    """执行系统命令并将结果写入日志"""
    print(f"正在执行: {' '.join(cmd)}")
    try:
        with open(log_file, "w") if log_file else sys.stdout as f:
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            for line in process.stdout:
                print(line, end="")
                if log_file:
                    f.write(line)
            process.wait()
            return process.returncode
    except Exception as e:
        print(f"发生异常: {e}")
        return 1

def get_video_duration(video_file):
    """获取视频总时长（秒）- 增强版"""
    # 尝试两种方式获取时长：1. 容器层(format) 2. 流层(stream)
    methods = [
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", video_file],
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=duration", "-of", "default=noprint_wrappers=1:nokey=1", video_file]
    ]
    
    for cmd in methods:
        try:
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
            output = result.stdout.strip()
            if output and output != 'N/A':
                return float(output)
        except Exception:
            continue
            
    # 如果以上都失败，尝试解析详细信息（最后的保底手段）
    try:
        cmd = ["ffmpeg", "-i", video_file]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        # ffmpeg -i 的信息输出在 stderr 中
        match = re.search(r"Duration:\s(\d+):(\d+):(\d+\.\d+)", result.stderr)
        if match:
            hours, minutes, seconds = match.groups()
            return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except:
        pass

    return 0

def upload_to_pixhost(file_path):
    """
    根据 PiXhost API 手册更新的上传函数
    API Endpoint: https://api.pixhost.to/images
	根据 PiXhost API 手册上传图片并返回 BBCode 格式字符串(使用原图直连)
    """
    url = "https://api.pixhost.to/images"
    try:
        files = {
            'img': (os.path.basename(file_path), open(file_path, 'rb'), 'image/jpeg')
        }
        data = {
            'content_type': '0',   # 0 为全年龄 (FS), 1 为 NSFW
            'max_th_size': '420'   # 缩略图尺寸 (150-500)
        }
        # 手册要求 Accept: application/json
        headers = {
            'Accept': 'application/json',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        
        response = requests.post(url, files=files, data=data, headers=headers, timeout=60)
        
        if response.status_code == 200:
            resp_json = response.json()
            show_url = resp_json.get('show_url')
            th_url = resp_json.get('th_url')
            
            if show_url and th_url:
                # 转换 th_url 为原图直连直连 img_url
                # 示例 th_url: https://t2.pixhost.to/thumbs/5653/693689299_image.png
                # 示例 img_url: https://img2.pixhost.to/images/5653/693689299_image.png
                img_url = th_url.replace('https://t', 'https://img').replace('/thumbs/', '/images/')
                
                # 返回格式: [url=展示页][img]原图直连[/img][/url]
                return f"[url={show_url}][img]{img_url}[/img][/url]"
            else:
                print(f"  API 返回字段缺失: {resp_json}")
        else:
            print(f"  HTTP 错误 {response.status_code}: {response.text}")
            
    except Exception as e:
        print(f"  上传抛出异常: {e}")
    return None

def capture_screenshots(video_file, output_dir, folder_name, count=4):
    """从视频中平均截取指定数量的图片，存放在固定目录"""
    duration = get_video_duration(video_file)
    if duration <= 0:
        print(f"错误: 无法通过 ffprobe/ffmpeg 获取视频时长。请检查文件是否损坏或 ffprobe 是否安装。")
        return []

    print(f"检测到视频时长: {duration:.2f}s，准备截取 {count} 张图 (画质等级: {SCREENSHOT_QUALITY})...")
    
    # 清空文件夹
    if os.path.exists(output_dir):
        print(f"正在清空旧截图目录: {output_dir}")
        for filename in os.listdir(output_dir):
            file_path = os.path.join(output_dir, filename)
            try:
                if os.path.isfile(file_path) or os.path.islink(file_path):
                    os.unlink(file_path)
            except Exception as e:
                print(f'无法删除 {file_path}. 原因: {e}')
    else:
        os.makedirs(output_dir)

    screenshots = []
    upload_urls = []
    
    # 避开片头片尾 10%，在中间 80% 范围内截取
    start_point = duration * 0.1
    end_point = duration * 0.9
    interval = (end_point - start_point) / (count - 1) if count > 1 else 0
    
    for i in range(count):
        timestamp = start_point + (i * interval)
        out_name = f"{folder_name}_{i+1}.jpg"
        out_path = os.path.join(output_dir, out_name)
        
        # 使用 -ss 置前加快定位速度，使用 -qscale:v 控制画质
        cmd = [
            "ffmpeg", "-y", "-ss", str(round(timestamp, 2)), 
            "-i", video_file, 
            "-frames:v", "1", 
            "-qscale:v", str(SCREENSHOT_QUALITY), 
            out_path
        ]
        
        # 静默执行截图
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if result.returncode == 0:
            print(f"  已生成截图 {i+1}: {out_name}")
            screenshots.append(out_path)
            
            if ENABLE_UPLOAD:
                print(f"    正在上传到 Pixhost...")
                up_url = upload_to_pixhost(out_path)
                if up_url:
                    upload_urls.append(up_url)
                    print(f"    上传成功: {up_url}")
            
    return upload_urls


def capture_tile_screenshot(video_file, output_dir, folder_name):
    """
    生成九宫格截图 (3x3)，并在顶部白边区域添加详细的视频元数据信息。
    参数: video_file (视频路径), output_dir (输出目录), folder_name (文件夹名用于命名)
    """
    duration = get_video_duration(video_file)
    if duration <= 0:
        print(f"错误: 无法获取时长，无法生成九宫格。")
        return None

    # 使用 ffprobe 获取详细的视频元数据
    try:
        # 获取视频流信息
        cmd_video = [
            "ffprobe", "-v", "error", 
            "-select_streams", "v:0", 
            "-show_entries", "stream=width,height,codec_name,bit_rate,r_frame_rate,pix_fmt", 
            "-show_entries", "format=duration,size,bit_rate,filename",
            "-of", "json", 
            video_file
        ]
        video_res = subprocess.run(cmd_video, capture_output=True, text=True)
        video_data = json.loads(video_res.stdout)
        
        # 获取音频流信息
        cmd_audio = [
            "ffprobe", "-v", "error", 
            "-select_streams", "a:0", 
            "-show_entries", "stream=codec_name,sample_rate,channels,bit_rate", 
            "-of", "json", 
            video_file
        ]
        audio_res = subprocess.run(cmd_audio, capture_output=True, text=True)
        audio_data = json.loads(audio_res.stdout)
        
        # 解析数据
        v_stream = video_data.get('streams', [{}])[0]
        v_format = video_data.get('format', {})
        a_stream = audio_data.get('streams', [{}])[0] if audio_data.get('streams') else {}
        
        # 文件信息
        file_name = os.path.basename(video_file)
        file_size = int(v_format.get('size', 0)) / (1024 * 1024)  # MB
        
        # 视频信息
        v_codec = v_stream.get('codec_name', 'unknown').upper()
        width = v_stream.get('width', 0)
        height = v_stream.get('height', 0)
        resolution = f"{width}x{height}"
        
        # 帧率处理
        fps_str = v_stream.get('r_frame_rate', '0/1')
        try:
            num, den = map(int, fps_str.split('/'))
            fps = round(num / den, 2) if den != 0 else 0
        except:
            fps = 0
        
        # 视频比特率
        v_bitrate = int(v_stream.get('bit_rate', 0)) / 1000 if v_stream.get('bit_rate') else 0
        if v_bitrate == 0:
            # 如果流中没有比特率，尝试从格式中获取
            total_bitrate = int(v_format.get('bit_rate', 0)) / 1000
            v_bitrate = int(total_bitrate) if total_bitrate > 0 else 0
        
        # 音频信息
        a_codec = a_stream.get('codec_name', 'N/A').upper() if a_stream else 'N/A'
        a_sample_rate = int(a_stream.get('sample_rate', 0)) / 1000 if a_stream.get('sample_rate') else 0
        a_channels = a_stream.get('channels', 0) if a_stream else 0
        a_bitrate = int(a_stream.get('bit_rate', 0)) / 1000 if a_stream.get('bit_rate') else 0
        
        # 时长格式化
        duration_str = f"{int(duration // 60)}:{int(duration % 60):02d}"
        
        # 构建信息文本（4行）
        info_lines = [
            f"File: {file_name}",
            f"Size: {file_size:.1f} MB | Duration: {duration_str}",
            f"Video: {v_codec} | {resolution} | {fps} fps | {int(v_bitrate)} kbps",
            f"Audio: {a_codec} | {a_sample_rate:.1f} kHz | {a_channels} ch | {int(a_bitrate)} kbps"
        ]
        
        print(f"  元数据获取成功:")
        for line in info_lines:
            print(f"    {line}")
        
    except Exception as e:
        print(f"  警告: 获取元数据失败: {e}")
        import traceback
        traceback.print_exc()
        info_lines = [
            f"File: {os.path.basename(video_file)}",
            "Metadata unavailable",
            "",
            ""
        ]

    out_name = f"{folder_name}_thumb_9x.jpg"
    out_path = os.path.join(output_dir, out_name)
    
    # 计算9个均匀分布的时间点
    start_offset = duration * 0.05
    end_offset = duration * 0.95
    usable_duration = end_offset - start_offset
    interval = usable_duration / 8
    
    timestamps = [start_offset + i * interval for i in range(9)]
    
    # 创建临时目录存放单张截图
    import tempfile
    temp_dir = tempfile.mkdtemp()
    temp_files = []
    
    print(f"正在生成九宫格预览图: {out_name}...")
    
    # 逐个截取关键帧
    for idx, ts in enumerate(timestamps):
        temp_file = os.path.join(temp_dir, f"tile_{idx}.jpg")
        temp_files.append(temp_file)
        
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(ts),
            "-i", video_file,
            "-vframes", "1",
            "-vf", "scale=640:-1",
            "-qscale:v", str(SCREENSHOT_QUALITY),
            temp_file
        ]
        
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if result.returncode != 0:
            print(f"  警告: 截图 {idx} 失败")
    
    # 创建临时文本文件存储信息（避免特殊字符转义问题）
    text_file = os.path.join(temp_dir, "info.txt")
    with open(text_file, 'w', encoding='utf-8') as f:
        f.write('\n'.join(info_lines))
    
    # 使用 filter_complex 组合九宫格，并在顶部添加120px白边显示信息
    # textfile 参数避免了特殊字符转义问题
    filter_complex = (
        "[0:v][1:v][2:v]hstack=inputs=3[row1];"
        "[3:v][4:v][5:v]hstack=inputs=3[row2];"
        "[6:v][7:v][8:v]hstack=inputs=3[row3];"
        "[row1][row2][row3]vstack=inputs=3[grid];"
        "[grid]pad=iw:ih+120:0:120:white[padded];"
        f"[padded]drawtext=textfile='{text_file}':x=20:y=10:fontsize=20:fontcolor=black:line_spacing=8"
    )
    
    cmd = ["ffmpeg", "-y"]
    for temp_file in temp_files:
        cmd.extend(["-i", temp_file])
    
    cmd.extend([
        "-filter_complex", filter_complex,
        "-frames:v", "1",
        "-qscale:v", str(SCREENSHOT_QUALITY),
        out_path
    ])
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode != 0:
        print(f"  错误: 合并失败")
        print(f"  stderr: {result.stderr}")
    
    # 清理临时文件
    import shutil
    shutil.rmtree(temp_dir)
    
    if result.returncode == 0 and os.path.exists(out_path):
        print(f"  九宫格生成成功: {out_path}")
        if ENABLE_UPLOAD:
            print(f"    正在上传九宫格到 Pixhost...")
            up_url = upload_to_pixhost(out_path)
            return up_url
    else:
        print(f"  九宫格生成失败")
    
    return None


log_file = ""
def main_prosess(TARGET_DIR):
    if not os.path.exists(TARGET_DIR):
        print(f"错误: 目录 {TARGET_DIR} 不存在！")
        return
    
    target_path = Path(TARGET_DIR)
    if not target_path.exists():
        print(f"跳过：路径不存在 {TARGET_DIR}")
        return

    # 1. 准备路径和文件名
    # parent_dir = os.path.dirname(TARGET_DIR)
    # folder_name = os.path.basename(TARGET_DIR)

    folder_name = target_path.name
    subtitle = translate_nba_info(folder_name)
    write_custom_log(log_file, subtitle)
    
    # parent_dir = Path(OUTPUT_BASE_DIR) / f"Z-{folder_name}"
    parent_dir = Path(OUTPUT_BASE_DIR) / f"Z-{subtitle}"
    
    if os.path.exists(parent_dir):
        shutil.rmtree(parent_dir)
        print(f"目录 '{parent_dir}' 已删除。")
    
    # 创建新的空目录
    os.makedirs(parent_dir)  # 使用makedirs以防路径中有多级目录
    print(f"新的空目录 '{parent_dir}' 已创建。")

    print(f"parent_dir: {parent_dir}")
    print(f"folder_name: {folder_name}")
    # return None
    
    # 最终路径
    torrent_path = os.path.join(parent_dir, f"{folder_name}.torrent")
    # 临时路径
    tmp_torrent_path = os.path.join(parent_dir, f"{folder_name}.torrent.tmp")
    
    nfo_path = os.path.join(parent_dir, f"{folder_name}.nfo")
    log_path = os.path.join(parent_dir, "mktorrent_execution.log")
    url_log_path = os.path.join(parent_dir, "screenshots_urls.log")
    publish_path = os.path.join(parent_dir, "publish.json")
    
    # 修改点：固定存放在 parent_dir 下的 screenshots 文件夹
    screens_dir = os.path.join(parent_dir, "screenshots")
    
    print(f"目标目录: {TARGET_DIR}")
    
    # 2. 生成 NFO (寻找第一个视频文件)
    video_file = None
    for root, dirs, files in os.walk(TARGET_DIR):
        for f in files:
            if f.lower().endswith(('.mp4', '.mkv', '.avi', '.ts', '.m2ts')):
                video_file = os.path.join(root, f)
                break
        if video_file: break

    # 1. 生成 NFO
    print(f"\n[1/4] 正在生成 NFO 文件...")
    if video_file:
        # 使用 mediainfo -f 生成完整报告
        try:
            with open(nfo_path, "w") as nfo_f:
                # mi_res = subprocess.run(["mediainfo", "-f", video_file], stdout=nfo_f)
                mi_res = subprocess.run(["mediainfo", "-f", video_file], capture_output=True, text=True)
                mediainfo_text = mi_res.stdout
            print(f"成功: NFO 已生成至 {nfo_path}")
        except Exception as e:
            print(f"生成 NFO 失败: {e}")
    else:
        print("警告: 未在目录中找到视频文件，跳过 NFO 生成。")

    # return None

    # 2. 截图与上传
    print(f"\n[2/4] 正在处理视频截图与上传...")
    urls = []
    bbcode_list = []
    if video_file:
        if not os.path.exists(screens_dir):
            os.makedirs(screens_dir)
            print(f"创建固定截图目录: {screens_dir}")
        urls = capture_screenshots(video_file, screens_dir, folder_name, count=4)
        pic9_url = capture_tile_screenshot(video_file, screens_dir, folder_name)
        if(pic9_url):
            urls.append(pic9_url)
        if urls:
            bbcode_list = urls
            with open(url_log_path, "w") as f:
                f.write("\n".join(urls))
            print(f"所有截图链接已写入: {url_log_path}")
    else:
        print("未找到视频文件，跳过截图步骤。")
  
    # return None

    # 3. 计算 Piece Size 并制作种子
    print(f"\n[3/4] 正在制作种子文件...")
    total_bytes = get_dir_size(TARGET_DIR)
    piece_l = get_optimal_piece_size(total_bytes)
    
    # 构建 mktorrent 命令，先输出到 .tmp 文件
    mk_cmd = [
        "mktorrent", 
        "-v", 
        "-p", 
        "-l", str(piece_l), 
        "-a", ANNOUNCE_URL, 
        "-o", tmp_torrent_path, 
        TARGET_DIR
    ]
    
    ret = run_command(mk_cmd, log_path)
    
    if ret == 0:
        # 4. 命令成功执行后，将 .tmp 重命名为 .torrent
        try:
            if os.path.exists(torrent_path):
                os.remove(torrent_path) # 如果已存在旧种子则删除
            os.rename(tmp_torrent_path, torrent_path)
            
            print(f"\n[4/4] 制作完成！")
            print(f"种子文件: {torrent_path}")
            print(f"NFO 文件: {nfo_path}")
            print(f"截图目录: {screens_dir}")
            print(f"执行日志: {log_path}")
            print(f"\n提示: 请将 .torrent 和 .nfo 下载到本地上传至 PT 站。")
        except Exception as e:
            print(f"\n[!] 重命名临时文件失败: {e}")
    else:
        print(f"\n[!] 制作失败，请查看日志: {log_path}")
        if os.path.exists(tmp_torrent_path):
            print(f"清理临时文件: {tmp_torrent_path}")
            os.remove(tmp_torrent_path)

    bbcode_thanks = f"[quote=castle][color=DarkRed][font=Comic Sans MS][size=6]转自sportscult，感谢原创作者[/size][/font][/color][/quote]\n"
    bbcode_main_pic = f"[url=https://pixhost.to/show/5653/693689299_b13c7363-09da-4c33-bfd6-0cb93c894a2e.png][img]https://img2.pixhost.to/images/5653/693689299_b13c7363-09da-4c33-bfd6-0cb93c894a2e.png[/img][/url]\n\n"
    publish_data = {
        "title": folder_name,
        "subtitle": subtitle,
        "mediainfo": mediainfo_text,
        "description": bbcode_thanks + f"[color=Navy][font=Trebuchet MS][size=4]" + subtitle + "\n\n" + bbcode_main_pic + "\n".join(bbcode_list),
        "tags": {
            "cat": "407", # NBA/Sports
            "codec": "7",
            "standard": "3",
            "medium": "4" # WebDL
        }
    }
    with open(publish_path, "w", encoding="utf-8") as f:
        json.dump(publish_data, f, ensure_ascii=False, indent=4)
    
    


if __name__ == "__main__":
    print(f"--- 启动 PT 制作自动化流程 {PRO_VER} ---")
    
    log_file = os.path.join(OUTPUT_BASE_DIR, "log.txt")
    write_custom_log(log_file, "Begin to process")
    
    if not os.path.exists(OUTPUT_BASE_DIR): os.makedirs(OUTPUT_BASE_DIR)
    for path in TASKS:
        main_prosess(path)
    
    write_custom_log(log_file, "finish process")