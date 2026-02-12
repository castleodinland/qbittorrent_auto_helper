import os
import subprocess
import math
import sys
import re
import shutil
import json
from urllib import request, parse
# 尝试导入 requests，如果没有则提示安装
try:
    import requests
except ImportError:
    print("正在尝试自动安装必要的上传库 requests...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests"])
    import requests

# ================= 配置区域 =================
# 参数 1: 你的 PT 站 Announce URL (Passkey)
# ANNOUNCE_URL = "https://tracker.qingwapt.com/announce.php"
# ANNOUNCE_URL = "https://rousi.pro/tracker/1d3ba4125577007e0d8c4b1d2527375a/announce"
ANNOUNCE_URL = " https://t.ubits.club/announce.php"

# 参数 2: 需要做种的完整目录路径 (末尾不要带斜杠)
TARGET_DIR = "/home/pt_main/sports/NBA RS 2026 San Antonio Spurs vs Los Angeles Lakers 10 021080p60_FSN-SAS"

# --- 新增功能配置 ---
# 截图画质 (1-31, 1最好, 31最差, 建议 3-5 保持在 500k 左右)
SCREENSHOT_QUALITY = 3

# 是否启用 Pixhost 上传功能
ENABLE_UPLOAD = True
# ===========================================

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

def main():
    if not os.path.exists(TARGET_DIR):
        print(f"错误: 目录 {TARGET_DIR} 不存在！")
        return

    # 1. 准备路径和文件名
    parent_dir = os.path.dirname(TARGET_DIR)
    folder_name = os.path.basename(TARGET_DIR)
    
    # 最终路径
    torrent_path = os.path.join(parent_dir, f"{folder_name}.torrent")
    # 临时路径
    tmp_torrent_path = os.path.join(parent_dir, f"{folder_name}.torrent.tmp")
    
    nfo_path = os.path.join(parent_dir, f"{folder_name}.nfo")
    log_path = os.path.join(parent_dir, "mktorrent_execution.log")
    url_log_path = os.path.join(parent_dir, "screenshots_urls.log")
    
    # 修改点：固定存放在 parent_dir 下的 screenshots 文件夹
    screens_dir = os.path.join(parent_dir, "screenshots")

    print(f"--- 启动 PT 制作自动化流程 ---")
    print(f"目标目录: {TARGET_DIR}")
    
    # 2. 生成 NFO (寻找第一个视频文件)
    print(f"\n[1/3] 正在生成 NFO 文件...")
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
                subprocess.run(["mediainfo", "-f", video_file], stdout=nfo_f)
            print(f"成功: NFO 已生成至 {nfo_path}")
        except Exception as e:
            print(f"生成 NFO 失败: {e}")
    else:
        print("警告: 未在目录中找到视频文件，跳过 NFO 生成。")

    # 2. 截图与上传
    print(f"\n[2/4] 正在处理视频截图与上传...")
    if video_file:
        if not os.path.exists(screens_dir):
            os.makedirs(screens_dir)
            print(f"创建固定截图目录: {screens_dir}")
        urls = capture_screenshots(video_file, screens_dir, folder_name, count=4)
        if urls:
            with open(url_log_path, "w") as f:
                f.write("\n".join(urls))
            print(f"所有截图链接已写入: {url_log_path}")
    else:
        print("未找到视频文件，跳过截图步骤。")

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

if __name__ == "__main__":
    main()