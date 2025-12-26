import os
import time
import shutil
import hashlib
import logging
import sys
import socket
import struct
import random
import threading
from pathlib import Path
from logging.handlers import RotatingFileHandler
from qbittorrentapi import exceptions
from datetime import datetime, timedelta

import random

# 尝试导入必要的库
try:
    import qbittorrentapi
    import bencodepy
except ImportError:
    print("错误: 缺少必要的库。请运行: pip install qbittorrent-api bencodepy")
    sys.exit(1)

# ==========================================
# 配置区域
# ==========================================

# qBittorrent 连接配置
QB_HOST = '127.0.0.1'
QB_PORT = 8084
QB_USERNAME = 'castle'
QB_PASSWORD = 'zxcvbnm123'

# 路径配置
TORRENT_LIB_PATH = './torrent-lib'
TORRENT_KEEP_PATH = './torrent-keep'  # v4.13 新增: 保种任务目录
QB_SAVE_PATH = '/downloads' 
LOCAL_PATH = '.' 

# 磁盘空间预留 (GB)
DISK_RESERVE_GB = 2.0

# 任务标签
TORRENT_TAG = 'auto-add'
KEEP_TAG = 'keep'  # v4.13 新增: 保种标签

# 日志文件名 (已更新到 v4.13)
LOG_FILENAME = 'auto-torrent-v5.1.log'

# --- Tracker 优先级配置 ---
TRACKER_PRIORITY_LIST = [
    'ourbits.club',
    'tracker.m-team.cc',
    'tracker.hhanclub.top'
]

# --- 时区配置 ---
LOG_TIMEZONE_HOURS = 8 

# 基础时间间隔配置 (秒)
WAIT_DOWNLOAD_CHECK = 60       # 检查下载是否完成的间隔
WAIT_DISK_SPACE = 60           # 磁盘不足时的重试间隔
WAIT_NO_TORRENT = 120          # 没有新种子时的重试间隔
WAIT_AFTER_ADD = 5             # 添加种子后的缓冲时间

# 维护与死锁检测配置
DURATION_DISK_DEADLOCK = 60   # (10分钟) 连续磁盘不足触发重启的时间阈值

# --- 死任务判定时间 (秒) ---
STALLED_DEAD_CHECK_SECONDS = 300      # 5分钟 (标准任务)
STALLED_DEAD_KEEP_SECONDS = 900       # 15分钟 (Keep任务)

# --- 上传速度检测配置 ---
UPLOAD_SPEED_THRESHOLD_KB = 500  # (KB/s) 上传速度阈值
WAIT_UPLOAD_CHECK = 300          # (5分钟) 上传速度高时的等待间隔
UPLOAD_SAMPLE_DURATION = 30      # (30秒) 速度检测的采样时长

# --- 动态下载超时配置 (仅限标准任务) ---
TIMEOUT_GB_PER_HOUR = 12 

# --- 早期慢速淘汰配置 (仅限标准任务) ---
EARLY_CHECK_ENABLE = True
EARLY_CHECK_POINTS = [
    (0.2, 0.15), (0.4, 0.35), (0.6, 0.55), (0.8, 0.75)
]

# --- Kickstart 批量配置 ---
KICKSTART_BATCH_SIZE = 10

# ==========================================
# 全局状态
# ==========================================

ACTIVE_DOWNLOAD_TRACKER = {
    'hash': None, 
    'start_time': None, 
    'name': None, 
    'timeout_seconds': 0.0, 
    'checked_points': set(),
    'is_keep': False  # v4.13 新增: 是否为保种任务
}

KICKSTART_MULTIPLIER = 0
CURRENT_SIM_SPEED_KB = 0

# ==========================================
# 日志配置
# ==========================================

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

TZ_OFFSET_SECONDS = LOG_TIMEZONE_HOURS * 3600
def time_zone_converter(timestamp):
    dt_utc = datetime.utcfromtimestamp(timestamp)
    dt_local = dt_utc + timedelta(seconds=TZ_OFFSET_SECONDS)
    return dt_local.timetuple()

if logger.hasHandlers():
    logger.handlers.clear()

try:
    file_handler = RotatingFileHandler(
        LOG_FILENAME, 
        maxBytes=5*1024*1024, 
        backupCount=3, 
        encoding='utf-8'
    )
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    file_handler.setFormatter(formatter)
    file_handler.formatter.converter = time_zone_converter 
    logger.addHandler(file_handler)
except Exception as e:
    print(f"无法创建日志文件: {e}")
    sys.exit(1)

stream_handler = logging.StreamHandler()
stream_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
stream_handler.setFormatter(stream_formatter)
stream_handler.formatter.converter = time_zone_converter 
logger.addHandler(stream_handler)

# ==========================================
# 核心功能函数
# ==========================================

def get_qb_client():
    conn_info = dict(host=QB_HOST, port=QB_PORT, username=QB_USERNAME, password=QB_PASSWORD)
    qbt_client = qbittorrentapi.Client(**conn_info)
    try:
        qbt_client.auth_log_in()
        return qbt_client
    except Exception as e:
        raise Exception(f"连接 qBittorrent 失败: {e}")

def get_torrent_info_from_file(file_path):
    try:
        decoded = bencodepy.decode_from_file(file_path)
        info = decoded[b'info']
        info_bencoded = bencodepy.encode(info)
        info_hash = hashlib.sha1(info_bencoded).hexdigest().lower()
        
        total_size = 0
        if b'files' in info:
            for file_entry in info[b'files']:
                total_size += file_entry[b'length']
        else:
            total_size = info[b'length']
            
        tracker_url = ""
        if b'announce-list' in decoded:
            tracker_url = decoded[b'announce-list'][0][0].decode('utf-8', errors='ignore')
        elif b'announce' in decoded:
            tracker_url = decoded[b'announce'].decode('utf-8', errors='ignore')
            
        return info_hash, total_size, tracker_url
    except Exception:
        return None, 0, ""

def get_tracker_priority(tracker_url):
    if not tracker_url:
        return len(TRACKER_PRIORITY_LIST)
    for index, domain in enumerate(TRACKER_PRIORITY_LIST):
        if domain.lower() in tracker_url.lower():
            return index
    return len(TRACKER_PRIORITY_LIST)

def has_unfinished_downloads(client):
    try:
        all_torrents = client.torrents_info(filter='downloading')
        return len(all_torrents) > 0
    except Exception:
        raise Exception("无法获取种子列表，连接可能已断开")

def verify_torrent_added(client, torrent_hash):
    start_time = time.time()
    while time.time() - start_time < 10: 
        try:
            torrents = client.torrents_info(torrent_hashes=torrent_hash)
            if torrents:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False

def measure_average_upload_speed(client, duration=30):
    samples = []
    start_time = time.time()
    while time.time() - start_time < duration:
        try:
            info = client.transfer_info()
            speed_kb = info.up_info_speed / 1024
            samples.append(speed_kb)
        except Exception: pass
        time.sleep(2)
    return sum(samples) / len(samples) if samples else 0

def cleanup_files():
    target_dir = Path(LOCAL_PATH).absolute()
    whitelist_extensions = ['.py', '.sh', '.log', '.go']
    whitelist_dirs = ['torrent-lib', 'torrent-keep', '.git', '__pycache__']
    whitelist_files = [LOG_FILENAME, 'auto-torrent-v4.13.py'] 
    
    if not target_dir.exists(): return
    for item in target_dir.iterdir():
        try:
            if item.is_dir() and item.name in whitelist_dirs: continue
            if item.is_file() and (item.suffix in whitelist_extensions or item.name in whitelist_files): continue
            if item.is_file() and item.name.endswith(('.torrent.slow', '.torrent.dead')): continue
            if item.is_file() or item.is_symlink(): os.remove(item)
            elif item.is_dir(): shutil.rmtree(item)
        except Exception: pass

def check_disk_space(required_bytes):
    try:
        usage = shutil.disk_usage(LOCAL_PATH)
        free_bytes = usage.free
        reserve_bytes = DISK_RESERVE_GB * 1024 * 1024 * 1024
        return free_bytes > (required_bytes + reserve_bytes), free_bytes
    except Exception:
        return False, 0

def safe_rename_with_suffix(src_path, suffix):
    base_new_name = src_path.name + suffix
    new_path = src_path.with_name(base_new_name)
    counter = 1
    while new_path.exists():
        new_path = src_path.with_name(f"{base_new_name}.{counter}")
        counter += 1
    src_path.rename(new_path)
    return new_path

def cleanup_slow_torrent(client, t_hash, t_name, is_dead=False):
    suffix = ".dead" if is_dead else ".slow"
    log_label = "死任务(零进度)" if is_dead else "慢速任务"
    
    try:
        client.torrents_delete(torrent_hashes=t_hash, delete_files=True)
        logger.info(f"已删除{log_label}: {t_name}")
    except Exception: pass
        
    # v4.13: 扫描两个目录
    for lib_dir in [TORRENT_LIB_PATH, TORRENT_KEEP_PATH]:
        lib_path = Path(lib_dir)
        if not lib_path.exists(): continue
        for t_file in lib_path.glob('*.torrent'):
            file_hash, _, _ = get_torrent_info_from_file(t_file)
            if file_hash == t_hash:
                try:
                    new_path = safe_rename_with_suffix(t_file, suffix)
                    logger.warning(f"标记为{suffix[1:]}: {new_path.name}")
                except Exception: pass
                return

def count_unadded_torrents(client, target_path=TORRENT_LIB_PATH):
    try:
        remote_hashes = {t.hash.lower() for t in client.torrents_info()}
        count = 0
        path_obj = Path(target_path)
        if not path_obj.exists(): return 0
        for t_file in path_obj.glob('*.torrent'):
            if any(x in t_file.name for x in ['.slow', '.dead']): continue
            t_hash, _, _ = get_torrent_info_from_file(t_file)
            if t_hash and t_hash not in remote_hashes: count += 1
        return count
    except Exception: return -1

def format_seconds_to_ddhhmm(seconds):
    if seconds is None or seconds < 0: return "N/A"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    d, h = divmod(h, 24)
    if d > 0:
        return f"{d:02d}d{h:02d}h{m:02d}m"
    return f"{h:02d}h{m:02d}m{s:02d}s"

def check_and_update_active_download(client):
    global ACTIVE_DOWNLOAD_TRACKER
    try:
        # 扫描所有带 auto-add 或 keep 标签的任务
        downloading = client.torrents_info(filter='downloading')
        # 优先选取当前正在跟踪的 hash，如果没有则选第一个符合标签的
        target_task = None
        for t in downloading:
            tags = t.tags.split(', ') if t.tags else []
            if TORRENT_TAG in tags or KEEP_TAG in tags:
                target_task = t
                break
        
        current_hash = target_task.hash if target_task else None
        if current_hash:
            if current_hash == ACTIVE_DOWNLOAD_TRACKER['hash']: return
            t_info = target_task
            size_gb = t_info.total_size / (1024**3)
            is_keep = KEEP_TAG in (t_info.tags.split(', ') if t_info.tags else [])
            
            # v4.13: Keep 任务不设置动态超时逻辑，此处仅为日志占位
            timeout_seconds = (max(size_gb, 10.0) / TIMEOUT_GB_PER_HOUR) * 3600 if not is_keep else 0.0
            
            ACTIVE_DOWNLOAD_TRACKER = {
                'hash': current_hash, 'start_time': t_info.added_on if t_info.added_on > 0 else time.time(),
                'name': t_info.name, 'timeout_seconds': timeout_seconds, 'checked_points': set(),
                'is_keep': is_keep
            }
            timeout_log = format_seconds_to_ddhhmm(timeout_seconds) if not is_keep else "无限制 (Keep)"
            logger.info(f"开始跟踪新任务: {t_info.name[:30]}... 超时设定: {timeout_log}")
        else:
            ACTIVE_DOWNLOAD_TRACKER = {'hash': None, 'start_time': None, 'name': None, 'timeout_seconds': 0.0, 'checked_points': set(), 'is_keep': False}
    except Exception: pass

def check_for_timeout_and_delete(client):
    global ACTIVE_DOWNLOAD_TRACKER
    if not ACTIVE_DOWNLOAD_TRACKER['hash']: return False
    
    try:
        t_list = client.torrents_info(torrent_hashes=ACTIVE_DOWNLOAD_TRACKER['hash'])
        if not t_list: return False
        t = t_list[0]
        elapsed = time.time() - ACTIVE_DOWNLOAD_TRACKER['start_time']
        
        # --- v4.13 Stalled (零进度) 检测 ---
        # Keep 任务 15 分钟，标准任务 5 分钟
        stalled_threshold = STALLED_DEAD_KEEP_SECONDS if ACTIVE_DOWNLOAD_TRACKER['is_keep'] else STALLED_DEAD_CHECK_SECONDS
        if elapsed > stalled_threshold and t.progress <= 0:
            logger.warning(f"触发死任务判定({'Keep' if ACTIVE_DOWNLOAD_TRACKER['is_keep'] else '标准'}): 运行 {format_seconds_to_ddhhmm(elapsed)} 但进度 0%")
            cleanup_slow_torrent(client, t.hash, t.name, is_dead=True)
            ACTIVE_DOWNLOAD_TRACKER['hash'] = None
            return True

        # v4.13 Keep 任务不执行以下淘汰逻辑
        if ACTIVE_DOWNLOAD_TRACKER['is_keep']:
            return False

        # 1. 标准任务: 动态超时
        if elapsed > ACTIVE_DOWNLOAD_TRACKER['timeout_seconds']:
            logger.warning(f"任务超时 ({format_seconds_to_ddhhmm(elapsed)} > {format_seconds_to_ddhhmm(ACTIVE_DOWNLOAD_TRACKER['timeout_seconds'])})")
            cleanup_slow_torrent(client, ACTIVE_DOWNLOAD_TRACKER['hash'], ACTIVE_DOWNLOAD_TRACKER['name'])
            ACTIVE_DOWNLOAD_TRACKER['hash'] = None
            return True
            
        # 2. 标准任务: 早期慢速淘汰 (Fail Fast)
        if EARLY_CHECK_ENABLE:
            for time_pct, progress_min in EARLY_CHECK_POINTS:
                if time_pct not in ACTIVE_DOWNLOAD_TRACKER['checked_points']:
                    if elapsed > (ACTIVE_DOWNLOAD_TRACKER['timeout_seconds'] * time_pct):
                        if t.progress < progress_min:
                            logger.warning(f"触发淘汰: 运行 {time_pct*100:.0f}% 时间但进度仅 {t.progress*100:.1f}%")
                            cleanup_slow_torrent(client, t.hash, t.name)
                            ACTIVE_DOWNLOAD_TRACKER['hash'] = None
                            return True
                        ACTIVE_DOWNLOAD_TRACKER['checked_points'].add(time_pct)
    except Exception: pass
    return False

def kickstart_seeding_tasks(client):
    global KICKSTART_MULTIPLIER
    logger.warning(f"触发 Kickstart 分段滚动重置 (起始偏移: {KICKSTART_MULTIPLIER * KICKSTART_BATCH_SIZE})")
    try:
        # 获取所有已完成的 auto-add 或 keep 任务
        completed = [t for t in client.torrents_info() if t.progress >= 1.0]
        valid_completed = []
        for t in completed:
            ts = t.tags.split(', ') if t.tags else []
            if TORRENT_TAG in ts or KEEP_TAG in ts:
                valid_completed.append(t)
        
        if not valid_completed: 
            logger.info("无相关已完成任务可重置。")
            return
        
        valid_completed.sort(key=lambda t: t.completion_on, reverse=True)
        total_completed = len(valid_completed)
        
        start_idx = KICKSTART_MULTIPLIER * KICKSTART_BATCH_SIZE
        if start_idx >= total_completed:
            start_idx = 0
            KICKSTART_MULTIPLIER = 0
            
        end_idx = start_idx + KICKSTART_BATCH_SIZE
        target_torrents = valid_completed[start_idx:end_idx]
        
        if not target_torrents:
            KICKSTART_MULTIPLIER = 0 
            return

        hashes = [t.hash for t in target_torrents]
        client.torrents_pause(torrent_hashes=hashes)
        time.sleep(10)
        client.torrents_resume(torrent_hashes=hashes)
        client.torrents_reannounce(torrent_hashes=hashes)
        KICKSTART_MULTIPLIER += 1
    except Exception as e:
        logger.error(f"Kickstart 执行出错: {e}")


# ==========================================
# 模拟连接核心逻辑
# ==========================================

def generate_random_peer_id():
    chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-_.!~*()"
    suffix = "".join(random.choice(chars) for _ in range(12))
    return "-qB4630-" + suffix

def connect_peer_with_stop_py(peer_addr, info_hash_hex, stop_event, error_flag):
    global CURRENT_SIM_SPEED_KB
    
    if ']:' in peer_addr: 
        ip = peer_addr.split(']:')[0][1:]
        port = int(peer_addr.split(']:')[1])
    elif ':' in peer_addr: 
        parts = peer_addr.split(':')
        ip = parts[0]
        port = int(parts[1])
    else:
        logger.error(f"[模拟连接] 地址格式无效: {peer_addr}")
        error_flag.set()
        return

    info_hash_bytes = bytes.fromhex(info_hash_hex)
    peer_id = generate_random_peer_id().encode()
    
    logger.info(f"[模拟连接] 尝试连接至 {ip}:{port}...")
    try:
        with socket.create_connection((ip, port), timeout=10) as sock:
            handshake = b'\x13BitTorrent protocol' + b'\x00' * 8 + info_hash_bytes + peer_id
            sock.sendall(handshake)
            resp = sock.recv(68)
            if len(resp) < 68:
                logger.error("[模拟连接] 握手失败")
                error_flag.set()
                return
            
            sock.sendall(struct.pack(">IB", 1, 2))
            downloaded_bytes = 0
            last_time = time.time()
            
            while not stop_event.is_set():
                payload = struct.pack(">IBIII", 13, 6, 0, 0, 16384)
                sock.sendall(payload)
                sock.settimeout(5)
                try:
                    data = sock.recv(16384 + 13)
                    if not data:
                        logger.error("[模拟连接] Peer 断连")
                        error_flag.set()
                        break
                    downloaded_bytes += len(data)
                except socket.timeout:
                    pass
                
                now = time.time()
                if now - last_time >= 1.0:
                    CURRENT_SIM_SPEED_KB = int((downloaded_bytes / 1024) / (now - last_time))
                    downloaded_bytes = 0
                    last_time = now
    except Exception as e:
        logger.error(f"[模拟连接] 错误: {e}")
        error_flag.set()


def run_simulation_process(client, t_hash, t_name):
    global CURRENT_SIM_SPEED_KB
    try:
        prefs = client.app_preferences()
        listen_port = prefs.get('listen_port')
        peer_addr = f"{QB_HOST}:{listen_port}"
        logger.info(f"[模拟激活] 任务 {t_name} 已完成。地址: {peer_addr}")
    except: return

    target_ratio = round(random.uniform(0.5, 2.0), 2)
    logger.info(f"[模拟激活] 目标分享率: {target_ratio}")
    
    stop_event = threading.Event()
    error_flag = threading.Event()
    sim_thread = threading.Thread(target=connect_peer_with_stop_py, args=(peer_addr, t_hash, stop_event, error_flag))
    sim_thread.daemon = True
    sim_thread.start()
    
    last_log_time = time.time()
    try:
        while True:
            if error_flag.is_set(): break
            try:
                t_list = client.torrents_info(torrent_hashes=t_hash)
                if not t_list: break
                current_ratio = t_list[0].ratio
            except: break
            
            if time.time() - last_log_time >= 30:
                logger.info(f"[模拟运行中] 速度: {CURRENT_SIM_SPEED_KB}KB/s, 分享率: {current_ratio:.3f}/{target_ratio}")
                last_log_time = time.time()
                
            if current_ratio >= target_ratio:
                logger.info(f"[模拟完成] 达到分享率: {current_ratio:.3f}")
                break
            time.sleep(5)
    finally:
        stop_event.set()
        sim_thread.join(timeout=5)
        CURRENT_SIM_SPEED_KB = 0

# ==========================================
# 主逻辑循环
# ==========================================

def main():
    global KICKSTART_MULTIPLIER
    logger.info("脚本服务 v5.1 已启动 (支持 Keep 目录/标签，两阶段扫描逻辑，模拟连接流程)...")
    Path(TORRENT_LIB_PATH).mkdir(parents=True, exist_ok=True)
    Path(TORRENT_KEEP_PATH).mkdir(parents=True, exist_ok=True)
    client, disk_full_start_time = None, None

    while True:
        try:
            if client is None:
                client = get_qb_client()
                check_and_update_active_download(client)
            
            #castle 获取当前正在下载的任务列表，当前搞一个空的
            castle_dling_hash = None

            # --- 步骤 1: 下载状态监控 ---
            while has_unfinished_downloads(client):
                check_and_update_active_download(client)
                if check_for_timeout_and_delete(client): continue

                #肯定不为空，为模拟连接做准备
                tt_list = client.torrents_info(filter='downloading')
                castle_dling_hash = tt_list[0].hash

                
                try:
                    downloading = client.torrents_info(filter='downloading')
                    t = None
                    for task in downloading:
                        ts = task.tags.split(', ') if task.tags else []
                        if TORRENT_TAG in ts or KEEP_TAG in ts:
                            t = task
                            break
                    
                    if t:
                        elapsed_seconds = time.time() - ACTIVE_DOWNLOAD_TRACKER['start_time']
                        eta_display = format_seconds_to_ddhhmm(t.eta) if t.eta < 8640000 else "无限"
                        
                        if ACTIVE_DOWNLOAD_TRACKER['is_keep']:
                            timeout_info = "[Keep模式: 无限期]"
                        else:
                            rem = max(0, ACTIVE_DOWNLOAD_TRACKER['timeout_seconds'] - elapsed_seconds)
                            timeout_info = f"[超时剩余: {format_seconds_to_ddhhmm(rem)}]"
                        
                        count_lib = count_unadded_torrents(client, TORRENT_LIB_PATH)
                        count_keep = count_unadded_torrents(client, TORRENT_KEEP_PATH)
                        
                        logger.info(f"下载中... [Lib:{count_lib}/Keep:{count_keep}] "
                                    f"[进度: {t.progress*100:.2f}%] [耗时: {format_seconds_to_ddhhmm(elapsed_seconds)}] "
                                    f"[ETA: {eta_display}] {timeout_info}")
                except Exception: pass
                time.sleep(WAIT_DOWNLOAD_CHECK)

            if castle_dling_hash:    #caslte模拟连接测试代码块
                t_list = client.torrents_info(torrent_hashes=castle_dling_hash)
                t = t_list[0]
                if t and t.state in ['uploading', 'stalledUP', 'queuedUP', 'forcedUP']:
                    logger.info(f"castle dump 当前下载完成的任务: {t.hash} | {t.name}")
                    run_simulation_process(client, castle_dling_hash, t.name)
                else:
                    logger.info(f"castle dump 之前在下载但此刻已经被删除或非做种：{t.hash} | {t.name}")
            else:
                logger.info(f"castle dump 没有下载完成的任务")


            # --- 步骤 2: 做种保护 ---
            while True:
                avg_speed = measure_average_upload_speed(client, duration=UPLOAD_SAMPLE_DURATION)
                if avg_speed > UPLOAD_SPEED_THRESHOLD_KB:
                    logger.info(f"高速上传中 ({avg_speed:.1f} KB/s)，继续做种...")
                    time.sleep(WAIT_UPLOAD_CHECK)
                else: break 
            
            cleanup_files()

            # --- 步骤 3: 扫描与添加逻辑 (优先扫描 Lib) ---
            torrent_added = False
            while not torrent_added:
                remote_hashes = {t.hash.lower() for t in client.torrents_info()}
                
                # v4.13: 决定当前扫描的目录和标签
                lib_torrents_count = count_unadded_torrents(client, TORRENT_LIB_PATH)
                if lib_torrents_count > 0:
                    current_scan_path = TORRENT_LIB_PATH
                    current_add_tag = TORRENT_TAG
                    is_scanning_keep = False
                else:
                    current_scan_path = TORRENT_KEEP_PATH
                    current_add_tag = KEEP_TAG
                    is_scanning_keep = True

                all_candidates = []
                path_obj = Path(current_scan_path)
                if path_obj.exists():
                    for t_file in path_obj.glob('*.torrent'):
                        if any(x in t_file.name for x in ['.slow', '.dead']): continue
                        t_hash, t_size, t_url = get_torrent_info_from_file(t_file)
                        if not t_hash or t_hash in remote_hashes: continue
                        priority = get_tracker_priority(t_url)
                        all_candidates.append({
                            'path': t_file, 'hash': t_hash, 'size': t_size, 
                            'priority': priority, 'url': t_url
                        })

                if not all_candidates:
                    logger.info(f"无新种子(Lib/Keep均空)，等待 {WAIT_NO_TORRENT} 秒...")
                    disk_full_start_time = None
                    KICKSTART_MULTIPLIER = 0 
                    time.sleep(WAIT_NO_TORRENT)
                    break

                all_candidates.sort(key=lambda x: x['priority'])
                selected_candidate = None
                display_needed_size = 0
                free_bytes_log = 0

                for cand in all_candidates:
                    space_ok, free_bytes = check_disk_space(cand['size'])
                    free_bytes_log = free_bytes
                    if space_ok:
                        selected_candidate = cand
                        break
                    else:
                        if display_needed_size == 0: display_needed_size = cand['size']

                if selected_candidate:
                    disk_full_start_time = None
                    KICKSTART_MULTIPLIER = 0 
                    logger.info(f"添加{'[Keep]' if is_scanning_keep else '[Lib]'}种子: {selected_candidate['path'].name} ({selected_candidate['size']/(1024**3):.2f} GB)")
                    try:
                        with open(selected_candidate['path'], 'rb') as f:
                            client.torrents_add(torrent_files=f, save_path=QB_SAVE_PATH, tags=current_add_tag)
                        if verify_torrent_added(client, selected_candidate['hash']):
                            torrent_added = True
                            time.sleep(WAIT_AFTER_ADD)
                    except Exception as e: logger.error(f"添加失败: {e}")
                else:
                    # 磁盘不足处理
                    avg_speed = measure_average_upload_speed(client, duration=UPLOAD_SAMPLE_DURATION)
                    if avg_speed > UPLOAD_SPEED_THRESHOLD_KB:
                        disk_full_start_time = None 
                        logger.info(f"磁盘不足，上传中，等待...")
                        time.sleep(WAIT_UPLOAD_CHECK)
                    else:
                        if disk_full_start_time is None: disk_full_start_time = time.time()
                        elapsed = time.time() - disk_full_start_time
                        remaining = DURATION_DISK_DEADLOCK - elapsed
                        
                        # v4.13: 即使是 Keep 任务磁盘不足，也会触发 Kickstart
                        # 但如果 Lib 还有种子，Kickstart 主要是为了给 Lib 腾空间
                        logger.warning(f"磁盘不足! 需要: {(display_needed_size / 1024**3):.2f} GB. Kickstart 倒计时: {max(0, remaining):.0f} 秒")
                        
                        if elapsed > DURATION_DISK_DEADLOCK:
                            kickstart_seeding_tasks(client)
                            disk_full_start_time = time.time() 
                        time.sleep(WAIT_DISK_SPACE)
            
            logger.info("进入下一轮循环...")

        except KeyboardInterrupt: sys.exit(0)
        except Exception as e:
            logger.error(f"运行错误: {e}")
            client = None
            time.sleep(10)

if __name__ == "__main__":
    main()