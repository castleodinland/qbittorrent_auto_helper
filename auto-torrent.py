import os
import time
import shutil
import hashlib
import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler
from qbittorrentapi import exceptions
from datetime import datetime, timedelta

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
QB_SAVE_PATH = '/downloads' 
LOCAL_PATH = '.' 

# 磁盘空间预留 (GB)
DISK_RESERVE_GB = 2.0

# 新增下载任务的标签
TORRENT_TAG = 'auto-add'

# 日志文件名 (已更新到 v4.9)
LOG_FILENAME = 'auto-torrent-v4.9.log'

# --- v4.8/v4.9 Tracker 优先级配置 ---
# 从高到低排列，不在此列表中的 Tracker 视为最低优先级
TRACKER_PRIORITY_LIST = [
    'ourbits.club',
    'tracker.m-team.cc'
]

# --- 时区配置 ---
# 日志时间显示时区 (小时): 默认 UTC+8 (北京时间)
LOG_TIMEZONE_HOURS = 8 

# 基础时间间隔配置 (秒)
WAIT_DOWNLOAD_CHECK = 60       # 检查下载是否完成的间隔
WAIT_DISK_SPACE = 60           # 磁盘不足时的重试间隔 (速度低时)
WAIT_NO_TORRENT = 120          # 没有新种子时的重试间隔
WAIT_AFTER_ADD = 5             # 添加种子后的缓冲时间

# 维护与死锁检测配置
INTERVAL_STALLED_CHECK = 1800  # (30分钟) 检查死任务(Stalled)的间隔
DURATION_DISK_DEADLOCK = 300   # (10分钟) 连续磁盘不足触发重启的时间阈值

# --- v4.7 新增: 死任务误杀保护期 ---
STALLED_CHECK_GRACE_PERIOD_MINUTES = 10 

# --- 上传速度检测配置 ---
UPLOAD_SPEED_THRESHOLD_KB = 500  # (KB/s) 上传速度阈值 (平均值)
WAIT_UPLOAD_CHECK = 300          # (5分钟) 上传速度高时的等待间隔
UPLOAD_SAMPLE_DURATION = 30      # (30秒) 速度检测的采样时长

# --- 动态下载超时配置 ---
TIMEOUT_GB_PER_HOUR = 12 

# --- v4.2/v4.3 多阶段早期慢速淘汰配置 (Fail Fast) ---
EARLY_CHECK_ENABLE = True
EARLY_CHECK_POINTS = [
    (0.2, 0.15), (0.4, 0.35), (0.6, 0.55), (0.8, 0.75)
]

# --- Kickstart 批量配置 ---
KICKSTART_BATCH_SIZE = 5

# ==========================================
# 全局状态
# ==========================================

ACTIVE_DOWNLOAD_TRACKER = {
    'hash': None, 
    'start_time': None, 
    'name': None, 
    'timeout_seconds': 0.0, 
    'checked_points': set()
}

KICKSTART_MULTIPLIER = 1

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
    """解析本地 .torrent 文件，返回 hash, size 和 tracker_url"""
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
            
        # 提取第一个 Tracker URL (用于优先级匹配)
        tracker_url = ""
        if b'announce-list' in decoded:
            tracker_url = decoded[b'announce-list'][0][0].decode('utf-8', errors='ignore')
        elif b'announce' in decoded:
            tracker_url = decoded[b'announce'].decode('utf-8', errors='ignore')
            
        return info_hash, total_size, tracker_url
    except Exception:
        return None, 0, ""

def get_tracker_priority(tracker_url):
    """计算 Tracker 优先级，数值越小优先级越高"""
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
    whitelist_dirs = ['torrent-lib', '.git', '__pycache__']
    whitelist_files = [LOG_FILENAME, 'auto-torrent-v4.9.py'] 
    lib_path_abs = Path(TORRENT_LIB_PATH).absolute()

    if not target_dir.exists(): return
    for item in target_dir.iterdir():
        try:
            if item.absolute() == lib_path_abs: continue
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

def cleanup_slow_torrent(client, t_hash, t_name):
    try:
        client.torrents_delete(torrent_hashes=t_hash, delete_files=True)
        logger.info(f"已删除慢速任务: {t_name}")
    except Exception: pass
        
    lib_path = Path(TORRENT_LIB_PATH)
    for t_file in lib_path.glob('*.torrent'):
        file_hash, _, _ = get_torrent_info_from_file(t_file)
        if file_hash == t_hash:
            try:
                new_path = safe_rename_with_suffix(t_file, ".slow")
                logger.warning(f"标记为慢速: {new_path.name}")
            except Exception: pass
            break

def process_stalled_tasks(client):
    logger.info("开始检查 Stalled (死) 任务...")
    try:
        stalled_torrents = client.torrents_info(status_filter='stalled_downloading')
        if not stalled_torrents: return

        lib_path = Path(TORRENT_LIB_PATH)
        local_torrents_map = {} 
        for t_file in lib_path.glob('*.torrent'):
            t_hash, _, _ = get_torrent_info_from_file(t_file)
            if t_hash: local_torrents_map[t_hash] = t_file

        current_time = time.time()
        grace_period_seconds = STALLED_CHECK_GRACE_PERIOD_MINUTES * 60

        for t in stalled_torrents:
            if current_time - t.added_on < grace_period_seconds:
                continue
            logger.warning(f"清理死任务: {t.name}")
            if t.hash in local_torrents_map:
                try: safe_rename_with_suffix(local_torrents_map[t.hash], ".dead")
                except Exception: pass
            client.torrents_delete(torrent_hashes=t.hash, delete_files=True)
    except Exception as e:
        logger.error(f"处理 Stalled 任务出错: {e}")

def count_unadded_torrents(client):
    try:
        remote_hashes = {t.hash.lower() for t in client.torrents_info()}
        count = 0
        for t_file in Path(TORRENT_LIB_PATH).glob('*.torrent'):
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
        downloading = client.torrents_info(tag=TORRENT_TAG, filter='downloading')
        current_hash = downloading[0].hash if downloading else None
        if current_hash:
            if current_hash == ACTIVE_DOWNLOAD_TRACKER['hash']: return
            t_info = downloading[0]
            size_gb = t_info.total_size / (1024**3)
            timeout_seconds = (max(size_gb, 10.0) / TIMEOUT_GB_PER_HOUR) * 3600
            ACTIVE_DOWNLOAD_TRACKER = {
                'hash': current_hash, 'start_time': t_info.added_on if t_info.added_on > 0 else time.time(),
                'name': t_info.name, 'timeout_seconds': timeout_seconds, 'checked_points': set()
            }
            logger.info(f"开始跟踪新任务: {t_info.name[:30]}... 超时设定: {format_seconds_to_ddhhmm(timeout_seconds)}")
        else:
            ACTIVE_DOWNLOAD_TRACKER = {'hash': None, 'start_time': None, 'name': None, 'timeout_seconds': 0.0, 'checked_points': set()}
    except Exception: pass

def check_for_timeout_and_delete(client):
    global ACTIVE_DOWNLOAD_TRACKER
    if not ACTIVE_DOWNLOAD_TRACKER['hash']: return False
    
    # 获取实时任务状态进行早期淘汰检测
    try:
        t_list = client.torrents_info(torrent_hashes=ACTIVE_DOWNLOAD_TRACKER['hash'])
        if not t_list: return False
        t = t_list[0]
        
        elapsed = time.time() - ACTIVE_DOWNLOAD_TRACKER['start_time']
        
        # 1. 动态超时逻辑
        if elapsed > ACTIVE_DOWNLOAD_TRACKER['timeout_seconds']:
            logger.warning(f"任务超时 ({format_seconds_to_ddhhmm(elapsed)} > {format_seconds_to_ddhhmm(ACTIVE_DOWNLOAD_TRACKER['timeout_seconds'])})")
            cleanup_slow_torrent(client, ACTIVE_DOWNLOAD_TRACKER['hash'], ACTIVE_DOWNLOAD_TRACKER['name'])
            ACTIVE_DOWNLOAD_TRACKER['hash'] = None
            return True
            
        # 2. 早期慢速淘汰逻辑 (Fail Fast)
        if EARLY_CHECK_ENABLE:
            for time_pct, progress_min in EARLY_CHECK_POINTS:
                if time_pct not in ACTIVE_DOWNLOAD_TRACKER['checked_points']:
                    if elapsed > (ACTIVE_DOWNLOAD_TRACKER['timeout_seconds'] * time_pct):
                        if t.progress < progress_min:
                            logger.warning(f"早期淘汰: 运行 {time_pct*100:.0f}% 时间但进度仅 {t.progress*100:.1f}% (要求 {progress_min*100:.1f}%)")
                            cleanup_slow_torrent(client, t.hash, t.name)
                            ACTIVE_DOWNLOAD_TRACKER['hash'] = None
                            return True
                        ACTIVE_DOWNLOAD_TRACKER['checked_points'].add(time_pct)
    except Exception: pass
    return False

def kickstart_seeding_tasks(client):
    global KICKSTART_MULTIPLIER
    logger.warning(f"触发 Kickstart (第 {KICKSTART_MULTIPLIER} 轮)")
    try:
        completed = [t for t in client.torrents_info(tag=TORRENT_TAG) if t.progress >= 1.0]
        if not completed: return
        completed.sort(key=lambda t: t.completion_on, reverse=True)
        hashes = [t.hash for t in completed[:KICKSTART_BATCH_SIZE * KICKSTART_MULTIPLIER]]
        client.torrents_pause(torrent_hashes=hashes)
        time.sleep(5)
        client.torrents_resume(torrent_hashes=hashes)
        client.torrents_reannounce(torrent_hashes=hashes)
        KICKSTART_MULTIPLIER += 1
    except Exception: pass

# ==========================================
# 主逻辑循环
# ==========================================

def main():
    global KICKSTART_MULTIPLIER
    logger.info("脚本服务 v4.9 已启动 (完整日志记录与 Tracker 优先级功能共存)...")
    Path(TORRENT_LIB_PATH).mkdir(parents=True, exist_ok=True)
    client, last_stalled_check_time, disk_full_start_time = None, 0, None

    while True:
        try:
            if client is None:
                client = get_qb_client()
                check_and_update_active_download(client)

            current_time = time.time()
            if current_time - last_stalled_check_time > INTERVAL_STALLED_CHECK:
                process_stalled_tasks(client)
                last_stalled_check_time = time.time()

            # --- 步骤 1: 检查活跃下载并打印详细日志 (完全恢复 v4.7 风格) ---
            while has_unfinished_downloads(client):
                check_and_update_active_download(client)
                if check_for_timeout_and_delete(client): continue
                
                # 获取详细进度信息用于打印
                try:
                    downloading = client.torrents_info(tag=TORRENT_TAG, filter='downloading')
                    if downloading:
                        t = downloading[0]
                        progress_display = f"{t.progress*100:.2f}%"
                        elapsed_seconds = time.time() - ACTIVE_DOWNLOAD_TRACKER['start_time']
                        elapsed_display = format_seconds_to_ddhhmm(elapsed_seconds)
                        eta_display = format_seconds_to_ddhhmm(t.eta) if t.eta < 8640000 else "无限"
                        
                        remaining_timeout = ACTIVE_DOWNLOAD_TRACKER['timeout_seconds'] - elapsed_seconds
                        timeout_remaining_display = format_seconds_to_ddhhmm(max(0, remaining_timeout))
                        count_msg = count_unadded_torrents(client)
                        
                        log_message = (
                            f"当前仍有未完成的下载任务... [待添加种子: {count_msg} 个] "
                            f"[进度: {progress_display}] [耗时: {elapsed_display}] "
                            f"[ETA: {eta_display}] [超时剩余: {timeout_remaining_display}]"
                        )
                        logger.info(log_message)
                except Exception:
                    logger.info(f"等待下载完成... [待处理: {count_unadded_torrents(client)}]")
                
                time.sleep(WAIT_DOWNLOAD_CHECK)

            # --- 步骤 2: 做种保护检测 ---
            while True:
                avg_speed = measure_average_upload_speed(client, duration=UPLOAD_SAMPLE_DURATION)
                if avg_speed > UPLOAD_SPEED_THRESHOLD_KB:
                    logger.info(f"高速上传中 ({avg_speed:.1f} KB/s)，继续做种...")
                    time.sleep(WAIT_UPLOAD_CHECK)
                else: break 
            
            cleanup_files()

            # --- 步骤 3: 优先级种子选择与添加 ---
            torrent_added = False
            while not torrent_added:
                remote_hashes = {t.hash.lower() for t in client.torrents_info()}
                lib_path = Path(TORRENT_LIB_PATH)
                
                all_candidates = []
                for t_file in lib_path.glob('*.torrent'):
                    if any(x in t_file.name for x in ['.slow', '.dead']): continue
                    t_hash, t_size, t_url = get_torrent_info_from_file(t_file)
                    if not t_hash or t_hash in remote_hashes: continue
                    
                    priority = get_tracker_priority(t_url)
                    all_candidates.append({
                        'path': t_file, 'hash': t_hash, 'size': t_size, 
                        'priority': priority, 'url': t_url
                    })

                if not all_candidates:
                    logger.info(f"无新种子，等待 {WAIT_NO_TORRENT} 秒...")
                    disk_full_start_time = None
                    KICKSTART_MULTIPLIER = 1
                    time.sleep(WAIT_NO_TORRENT)
                    break

                # 按优先级排序 (数值越小优先级越高)
                all_candidates.sort(key=lambda x: x['priority'])

                selected_candidate = None
                display_needed_size = 0
                free_bytes_log = 0

                # 寻找最高优先级的可下载种子
                for cand in all_candidates:
                    space_ok, free_bytes = check_disk_space(cand['size'])
                    free_bytes_log = free_bytes
                    if space_ok:
                        selected_candidate = cand
                        # 只有在匹配到列表中的 Tracker 时才特殊提醒
                        if cand['priority'] < len(TRACKER_PRIORITY_LIST):
                            logger.info(f"【高优先级种子】匹配 Tracker 服务器: {cand['url']}")
                        break
                    else:
                        if display_needed_size == 0: display_needed_size = cand['size']

                if selected_candidate:
                    disk_full_start_time = None
                    KICKSTART_MULTIPLIER = 1
                    logger.info(f"添加种子: {selected_candidate['path'].name} (大小: {selected_candidate['size']/(1024**3):.2f} GB, 优先级: {selected_candidate['priority']})")
                    try:
                        with open(selected_candidate['path'], 'rb') as f:
                            client.torrents_add(torrent_files=f, save_path=QB_SAVE_PATH, tags=TORRENT_TAG)
                        if verify_torrent_added(client, selected_candidate['hash']):
                            torrent_added = True
                            time.sleep(WAIT_AFTER_ADD)
                    except Exception as e:
                        logger.error(f"添加失败: {e}")
                else:
                    # 磁盘空间不足时的处理
                    avg_speed = measure_average_upload_speed(client, duration=UPLOAD_SAMPLE_DURATION)
                    if avg_speed > UPLOAD_SPEED_THRESHOLD_KB:
                        disk_full_start_time = None 
                        logger.info(f"磁盘不足，但检测到高速上传 ({avg_speed:.1f} KB/s)，等待做种...")
                        time.sleep(WAIT_UPLOAD_CHECK)
                    else:
                        if disk_full_start_time is None: disk_full_start_time = time.time()
                        elapsed = time.time() - disk_full_start_time
                        remaining = DURATION_DISK_DEADLOCK - elapsed
                        
                        logger.warning(f"磁盘空间不足! 需要: {(display_needed_size / 1024**3):.2f} GB, 剩余: {(free_bytes_log / 1024**3):.2f} GB. "
                                       f"Kickstart 倒计时: {max(0, remaining):.0f} 秒 (当前倍数: {KICKSTART_MULTIPLIER})")
                        
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