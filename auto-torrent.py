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

# 日志文件名 (已更新到 v4.0)
LOG_FILENAME = 'auto-torrent-v4.0.log'

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
DURATION_DISK_DEADLOCK = 600   # (10分钟) 连续磁盘不足触发重启的时间阈值

# --- 上传速度检测配置 ---
UPLOAD_SPEED_THRESHOLD_KB = 200  # (KB/s) 上传速度阈值 (平均值)
WAIT_UPLOAD_CHECK = 300          # (5分钟) 上传速度高时的等待间隔
UPLOAD_SAMPLE_DURATION = 30      # (30秒) 速度检测的采样时长

# --- 动态下载超时配置 ---
# 下载超时基准：每 10GB 给予 1 小时
TIMEOUT_GB_PER_HOUR = 15 

# --- Kickstart 批量配置 ---
KICKSTART_BATCH_SIZE = 5         # 每次触发 Kickstart 的基础数量 N

# ==========================================
# 全局状态
# ==========================================

# 跟踪当前唯一的正在下载的任务
ACTIVE_DOWNLOAD_TRACKER = {'hash': None, 'start_time': None, 'name': None, 'timeout_seconds': 0.0}

# Kickstart 倍增计数器
KICKSTART_MULTIPLIER = 1

# ==========================================
# 日志配置
# ==========================================

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# 自定义时区转换器 (UTC+X)
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
    """连接到qBittorrent客户端"""
    conn_info = dict(
        host=QB_HOST,
        port=QB_PORT,
        username=QB_USERNAME,
        password=QB_PASSWORD,
    )
    qbt_client = qbittorrentapi.Client(**conn_info)
    try:
        qbt_client.auth_log_in()
        logger.debug(f"成功连接到 qBittorrent: {qbt_client.app.version}")
        return qbt_client
    except Exception as e:
        raise Exception(f"连接 qBittorrent 失败: {e}")

def get_torrent_info_from_file(file_path):
    """解析本地 .torrent 文件"""
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
            
        return info_hash, total_size
    except Exception:
        return None, 0

def has_unfinished_downloads(client):
    """检查是否有未完成的任务 (包含 stalledDL)"""
    try:
        # 'downloading' filter covers: metaDL, allocating, downloading, queuedDL, forceDL, stalledDL, checkingDL
        all_torrents = client.torrents_info(filter='downloading')
    except Exception:
        raise Exception("无法获取种子列表，连接可能已断开")

    return len(all_torrents) > 0

def verify_torrent_added(client, torrent_hash):
    """轮询确认种子确实已添加到列表中"""
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
    """测量一段时间内的平均上传速度 (KB/s)"""
    samples = []
    start_time = time.time()
    
    while time.time() - start_time < duration:
        try:
            info = client.transfer_info()
            # up_info_speed 是 bytes/s
            speed_kb = info.up_info_speed / 1024
            samples.append(speed_kb)
        except Exception as e:
            logger.warning(f"采样获取速度失败: {e}")
        
        time.sleep(2) # 每2秒采样一次
    
    if not samples:
        return 0
        
    avg_speed = sum(samples) / len(samples)
    logger.debug(f"采样详情: 时长{duration}s, 样本{len(samples)}个, 平均{avg_speed:.1f}KB/s")
    return avg_speed

def cleanup_files():
    """清理目录"""
    target_dir = Path(LOCAL_PATH).absolute()
    logger.info(f"执行目录清理: {target_dir}")
    
    whitelist_extensions = ['.py', '.sh', '.log']
    whitelist_dirs = ['torrent-lib', '.git', '__pycache__']
    # 保护 v4.0 版本的脚本自身
    whitelist_files = [LOG_FILENAME, 'auto-torrent-v4.0.py'] 
    
    lib_path_abs = Path(TORRENT_LIB_PATH).absolute()

    if not target_dir.exists():
        return

    for item in target_dir.iterdir():
        try:
            if item.absolute() == lib_path_abs: continue
            if item.is_dir() and item.name in whitelist_dirs: continue
            if item.is_file() and item.suffix in whitelist_extensions: continue
            if item.is_file() and item.name in whitelist_files: continue
            
            # 跳过所有 .slow 或 .dead 标记的种子文件
            if item.is_file() and item.name.endswith(('.torrent.slow', '.torrent.dead')): continue

            if item.is_file() or item.is_symlink():
                os.remove(item)
                logger.info(f"已删除文件: {item.name}")
            elif item.is_dir():
                shutil.rmtree(item)
                logger.info(f"已删除目录: {item.name}")
        except Exception as e:
            logger.error(f"删除 {item.name} 失败: {e}")

def check_disk_space(required_bytes):
    """检查磁盘空间"""
    try:
        usage = shutil.disk_usage(LOCAL_PATH)
        free_bytes = usage.free
        reserve_bytes = DISK_RESERVE_GB * 1024 * 1024 * 1024
        if free_bytes > (required_bytes + reserve_bytes):
            return True, free_bytes
        return False, free_bytes
    except Exception as e:
        logger.error(f"检查磁盘空间失败: {e}")
        return False, 0

def cleanup_slow_torrent(client, t_hash, t_name):
    """删除慢速任务及其数据，并将种子文件标记为 .slow"""
    timeout_hours = ACTIVE_DOWNLOAD_TRACKER['timeout_seconds'] / 3600
    logger.warning(f"下载超时 ({timeout_hours:.2f}小时): 任务 {t_name} (Hash: {t_hash[:10]}) 未完成，开始清理...")
    
    try:
        client.torrents_delete(torrent_hashes=t_hash, delete_files=True)
        logger.info(f"已从 qBittorrent 删除慢速任务和数据: {t_name}")
    except Exception as e:
        logger.error(f"删除慢速任务 {t_name} 失败: {e}")
        
    lib_path = Path(TORRENT_LIB_PATH)
    found = False
    for t_file in lib_path.glob('*.torrent'):
        file_hash, _ = get_torrent_info_from_file(t_file)
        if file_hash == t_hash:
            new_name = t_file.with_name(t_file.name + ".slow")
            try:
                t_file.rename(new_name)
                logger.warning(f"已将慢速种子文件标记为: {new_name.name}")
                found = True
            except Exception as e:
                logger.error(f"重命名慢速种子文件 {t_file.name} 失败: {e}")
            break
            
    if not found:
        logger.warning(f"未在 {TORRENT_LIB_PATH} 中找到对应的种子文件进行标记。")

def process_stalled_tasks(client):
    """处理死任务：删除任务、文件并重命名种子"""
    logger.info("开始检查 Stalled (死) 任务...")
    try:
        stalled_torrents = client.torrents_info(status_filter='stalled_downloading')
        
        if not stalled_torrents:
            logger.info("未发现 Stalled 任务。")
            return

        lib_path = Path(TORRENT_LIB_PATH)
        local_torrents_map = {} 
        for t_file in lib_path.glob('*.torrent'):
            t_hash, _ = get_torrent_info_from_file(t_file)
            if t_hash:
                local_torrents_map[t_hash] = t_file

        for t in stalled_torrents:
            logger.warning(f"发现死任务: {t.name} (Hash: {t.hash[:10]})，准备清理...")
            
            if t.hash in local_torrents_map:
                src_file = local_torrents_map[t.hash]
                new_name = src_file.with_name(src_file.name + ".dead") 
                try:
                    src_file.rename(new_name)
                    logger.info(f"已标记种子文件为死档: {new_name.name}")
                except Exception as e:
                    logger.error(f"重命名种子文件失败: {e}")
            else:
                logger.warning("在 torrent-lib 中未找到对应的种子文件，跳过重命名。")

            try:
                client.torrents_delete(torrent_hashes=t.hash, delete_files=True)
                logger.info(f"已从 qBittorrent 删除任务和数据: {t.name}")
            except Exception as e:
                logger.error(f"删除任务失败: {e}")

    except Exception as e:
        logger.error(f"处理 Stalled 任务时出错: {e}")

def count_unadded_torrents(client):
    """统计本地库中尚未添加到客户端的种子数量"""
    try:
        remote_torrents = client.torrents_info()
        remote_hashes = {t.hash.lower() for t in remote_torrents}
        lib_path = Path(TORRENT_LIB_PATH)
        count = 0
        for t_file in lib_path.glob('*.torrent'):
            t_hash, _ = get_torrent_info_from_file(t_file)
            if t_hash and t_hash not in remote_hashes and not t_file.name.endswith(('.slow', '.dead')):
                count += 1
        return count
    except Exception as e:
        logger.warning(f"统计剩余种子失败: {e}")
        return -1

def format_seconds_to_ddhhmm(seconds):
    """将秒数转换为 ddhhmm 格式"""
    if seconds is None or seconds < 0:
        return "N/A"
        
    minutes, seconds = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    
    return f"{days:02d}d{hours:02d}h{minutes:02d}m"

def check_and_update_active_download(client):
    """检查当前活动的下载任务是否已改变或完成，并动态计算超时时间"""
    global ACTIVE_DOWNLOAD_TRACKER
    
    try:
        downloading_torrents = client.torrents_info(tag=TORRENT_TAG, filter='downloading')
        current_active_hash = downloading_torrents[0].hash if downloading_torrents else None
        
        if current_active_hash:
            current_active_name = downloading_torrents[0].name
            if current_active_hash == ACTIVE_DOWNLOAD_TRACKER['hash']:
                return
            else:
                if ACTIVE_DOWNLOAD_TRACKER['hash']:
                    timeout_hours_old = ACTIVE_DOWNLOAD_TRACKER['timeout_seconds'] / 3600
                    logger.info(f"下载任务已切换/完成。旧任务 {ACTIVE_DOWNLOAD_TRACKER['name'][:30]}... 停止计时 ({timeout_hours_old:.2f}小时)。")
                    
                torrent_info = client.torrents_info(torrent_hashes=current_active_hash)[0]
                total_size_gb = torrent_info.total_size / (1024**3)
                
                base_gb = max(total_size_gb, 10.0) 
                
                timeout_hours = base_gb / TIMEOUT_GB_PER_HOUR
                timeout_seconds = timeout_hours * 3600
                
                ACTIVE_DOWNLOAD_TRACKER = {
                    'hash': current_active_hash,
                    'start_time': time.time(),
                    'name': current_active_name,
                    'timeout_seconds': timeout_seconds 
                }
                
                timeout_str = format_seconds_to_ddhhmm(timeout_seconds)
                logger.info(f"新下载任务 {current_active_name[:30]}... 开始计时 (容量: {total_size_gb:.2f} GB, 超时: {timeout_str})。")

        else:
            if ACTIVE_DOWNLOAD_TRACKER['hash']:
                timeout_hours_old = ACTIVE_DOWNLOAD_TRACKER['timeout_seconds'] / 3600
                logger.info(f"下载任务已完成/停止。任务 {ACTIVE_DOWNLOAD_TRACKER['name'][:30]}... 停止计时 ({timeout_hours_old:.2f}小时)。")
            
            ACTIVE_DOWNLOAD_TRACKER = {'hash': None, 'start_time': None, 'name': None, 'timeout_seconds': 0.0}
            
    except Exception as e:
        logger.error(f"更新下载计时器状态失败: {e}")
        ACTIVE_DOWNLOAD_TRACKER = {'hash': None, 'start_time': None, 'name': None, 'timeout_seconds': 0.0}

def check_for_timeout_and_delete(client):
    """检查活动下载任务是否超时"""
    global ACTIVE_DOWNLOAD_TRACKER
    
    if ACTIVE_DOWNLOAD_TRACKER['hash']:
        elapsed = time.time() - ACTIVE_DOWNLOAD_TRACKER['start_time']
        timeout_seconds = ACTIVE_DOWNLOAD_TRACKER['timeout_seconds']
        
        if elapsed > timeout_seconds:
            t_hash = ACTIVE_DOWNLOAD_TRACKER['hash']
            t_name = ACTIVE_DOWNLOAD_TRACKER['name']
            
            logger.warning(f"任务 {t_name} 下载超时 ({format_seconds_to_ddhhmm(elapsed)})，开始执行清理...")
            cleanup_slow_torrent(client, t_hash, t_name)
            ACTIVE_DOWNLOAD_TRACKER = {'hash': None, 'start_time': None, 'name': None, 'timeout_seconds': 0.0}
            return True
        else:
            remaining = timeout_seconds - elapsed
            logger.debug(f"任务 {ACTIVE_DOWNLOAD_TRACKER['name'][:30]}... 剩余超时时间: {format_seconds_to_ddhhmm(remaining)}")
            
    return False

def initialize_download_tracker(client):
    """启动时初始化下载计时器"""
    check_and_update_active_download(client)

def kickstart_seeding_tasks(client):
    """(v3.11/v4.0) 按完成时间排序，批量重启最新的 N * Multiplier 个任务"""
    global KICKSTART_MULTIPLIER
    
    logger.warning(f">>> 触发 Kickstart 机制 (第 {KICKSTART_MULTIPLIER} 轮)：磁盘空间长期不足，尝试重启最新的 {KICKSTART_BATCH_SIZE * KICKSTART_MULTIPLIER} 个任务...")

    try:
        all_auto_tasks = client.torrents_info(tag=TORRENT_TAG)
        completed_tasks = [t for t in all_auto_tasks if t.progress >= 1.0]
        
        if not completed_tasks:
            logger.info("未找到已完成的任务，无法执行 Kickstart。")
            return

        completed_tasks.sort(key=lambda t: t.completion_on, reverse=True)
        target_count = KICKSTART_BATCH_SIZE * KICKSTART_MULTIPLIER
        target_batch = completed_tasks[:target_count]
        target_hashes = [t.hash for t in target_batch]
        
        if not target_hashes:
            return

        logger.info(f"选中了 {len(target_hashes)} 个最新任务进行重启 (最新任务: {target_batch[0].name[:20]}...)")

        client.torrents_pause(torrent_hashes=target_hashes)
        logger.info(f"已批量暂停 {len(target_hashes)} 个任务，等待 5 秒...")
        time.sleep(5)
        
        client.torrents_resume(torrent_hashes=target_hashes)
        logger.info(f"已批量恢复 {len(target_hashes)} 个任务，等待 5 秒...")
        time.sleep(5)
        
        client.torrents_reannounce(torrent_hashes=target_hashes)
        logger.info(f"已批量 Reannounce {len(target_hashes)} 个任务。")
        
        KICKSTART_MULTIPLIER += 1
        logger.info(f"Kickstart 完成。倍增计数器已升级为 {KICKSTART_MULTIPLIER}。")

    except Exception as e:
        logger.error(f"执行批量 Kickstart 失败: {e}")

# ==========================================
# 主逻辑循环
# ==========================================

def main():
    global KICKSTART_MULTIPLIER
    logger.info("脚本服务 v4.0 已启动...")
    logger.info(f"日志时间时区设置为: UTC+{LOG_TIMEZONE_HOURS}")
    Path(TORRENT_LIB_PATH).mkdir(parents=True, exist_ok=True)
    
    client = None
    
    # 计时器初始化
    last_stalled_check_time = 0
    disk_full_start_time = None 

    while True:
        try:
            # 0. 连接保活
            if client is None:
                logger.info("尝试连接 qBittorrent...")
                client = get_qb_client()
                logger.info("连接成功。")
                initialize_download_tracker(client)

            current_time = time.time()

            # -------------------------------------------------
            # 维护任务: 检查 Stalled
            # -------------------------------------------------
            if current_time - last_stalled_check_time > INTERVAL_STALLED_CHECK:
                process_stalled_tasks(client)
                last_stalled_check_time = time.time()

            # -------------------------------------------------
            # 步骤 1: 检查活跃下载
            # -------------------------------------------------
            while has_unfinished_downloads(client):
                
                check_and_update_active_download(client)
                
                if check_for_timeout_and_delete(client):
                    continue
                
                pending_count = count_unadded_torrents(client)
                count_msg = f"{pending_count}" if pending_count >= 0 else "未知"
                
                eta_display = "N/A"
                timeout_remaining_display = "N/A"
                
                if ACTIVE_DOWNLOAD_TRACKER['hash']:
                    try:
                        active_torrent = client.torrents_info(torrent_hashes=ACTIVE_DOWNLOAD_TRACKER['hash'])[0]
                        eta_seconds = active_torrent.eta
                        eta_display = format_seconds_to_ddhhmm(eta_seconds)
                    except Exception:
                        pass
                        
                    elapsed = time.time() - ACTIVE_DOWNLOAD_TRACKER['start_time']
                    remaining = ACTIVE_DOWNLOAD_TRACKER['timeout_seconds'] - elapsed
                    timeout_remaining_display = format_seconds_to_ddhhmm(remaining)
                
                log_message = (
                    f"当前仍有未完成的下载任务... [待添加种子: {count_msg} 个] "
                    f"[ETA: {eta_display}] [超时剩余: {timeout_remaining_display}]"
                )
                logger.info(log_message)
                
                time.sleep(WAIT_DOWNLOAD_CHECK)
                if time.time() - last_stalled_check_time > INTERVAL_STALLED_CHECK:
                    process_stalled_tasks(client)
                    last_stalled_check_time = time.time()

            # -------------------------------------------------
            # 步骤 1.5: 初始做种保护期
            # -------------------------------------------------
            while True:
                check_and_update_active_download(client) 
                
                logger.info(f"正在进行 {UPLOAD_SAMPLE_DURATION} 秒的上传速度采样...")
                upload_speed = measure_average_upload_speed(client, duration=UPLOAD_SAMPLE_DURATION)
                
                if upload_speed > UPLOAD_SPEED_THRESHOLD_KB:
                    logger.info(f"当前平均上传速度: {upload_speed:.1f} KB/s (高于阈值 {UPLOAD_SPEED_THRESHOLD_KB} KB/s)。"
                                f"继续做种，等待 {WAIT_UPLOAD_CHECK/60:.0f} 分钟后再次检测...")
                    time.sleep(WAIT_UPLOAD_CHECK)
                    if time.time() - last_stalled_check_time > INTERVAL_STALLED_CHECK:
                        process_stalled_tasks(client)
                        last_stalled_check_time = time.time()
                else:
                    logger.info(f"当前平均上传速度: {upload_speed:.1f} KB/s (低于阈值)。准备进行磁盘清理。")
                    break 
            
            # -------------------------------------------------
            # 步骤 2: 清理目录
            # -------------------------------------------------
            cleanup_files()

            # -------------------------------------------------
            # 步骤 3-5: 添加新任务 & 磁盘死锁检测 (v4.0 核心更新)
            # -------------------------------------------------
            torrent_added = False
            
            while not torrent_added:
                try:
                    remote_torrents = client.torrents_info()
                except Exception:
                     raise Exception("获取远程种子列表失败，需要重连")

                remote_hashes = {t.hash.lower() for t in remote_torrents}
                
                lib_path = Path(TORRENT_LIB_PATH)
                
                candidate_path = None
                candidate_size = 0
                candidate_hash = None 
                
                found_any_new = False
                min_size_blocked = float('inf') # 记录被阻塞种子中最小的那个，用于日志
                
                torrent_files = sorted(lib_path.glob('*.torrent'))
                
                # V4.0 LOGIC: 遍历所有新种子，寻找一个能放得下的
                for t_file in torrent_files:
                    t_hash, t_size = get_torrent_info_from_file(t_file)
                    
                    if t_file.name.endswith(('.slow', '.dead')): 
                        continue 

                    if not t_hash: continue
                    
                    if t_hash not in remote_hashes:
                        # 这是一个新种子
                        found_any_new = True
                        
                        # 立即检查空间
                        space_ok, free_bytes = check_disk_space(t_size)
                        
                        if space_ok:
                            # 找到一个合适的！立即选中并跳出循环
                            candidate_path = t_file
                            candidate_size = t_size
                            candidate_hash = t_hash
                            logger.info(f"发现新种子且空间足够: {t_file.name}, 大小: {t_size / (1024**3):.2f} GB")
                            break # 跳出 for 循环，准备添加
                        else:
                            # 空间不足，记录一下，继续找下一个
                            if t_size < min_size_blocked:
                                min_size_blocked = t_size
                
                if not found_any_new:
                    logger.info(f"没有发现新种子，等待 {WAIT_NO_TORRENT} 秒后重新扫描...")
                    disk_full_start_time = None 
                    
                    if KICKSTART_MULTIPLIER > 1:
                        logger.info(f"无新种子压力，重置 Kickstart 倍增计数器为 1。")
                        KICKSTART_MULTIPLIER = 1
                    
                    time.sleep(WAIT_NO_TORRENT)
                    
                    if time.time() - last_stalled_check_time > INTERVAL_STALLED_CHECK:
                        process_stalled_tasks(client)
                        last_stalled_check_time = time.time()
                    continue 
                
                # 如果跳出循环且有 candidate_path，说明找到了合适的
                if candidate_path:
                    disk_full_start_time = None 
                    
                    if KICKSTART_MULTIPLIER > 1:
                        logger.info(f"磁盘空间充足，重置 Kickstart 倍增计数器为 1。")
                        KICKSTART_MULTIPLIER = 1
                    
                    logger.info(f"开始添加任务: {candidate_path.name}")
                    try:
                        with open(candidate_path, 'rb') as f:
                            client.torrents_add(
                                torrent_files=f, 
                                save_path=QB_SAVE_PATH,
                                tags=TORRENT_TAG
                            )
                        
                        logger.info("已发送添加指令，正在确认任务是否上线...")
                        if verify_torrent_added(client, candidate_hash):
                            logger.info(f"任务确认已上线: {candidate_path.name}")
                            torrent_added = True
                            time.sleep(WAIT_AFTER_ADD)
                        else:
                            logger.error("超时：任务未出现在列表中，可能添加失败或客户端响应慢。")
                            time.sleep(10)

                    except Exception as e:
                        logger.error(f"添加种子失败: {e}")
                        time.sleep(10)
                else:
                    # 遍历了所有新种子，没有任何一个能放下 -> 真正的磁盘空间不足
                    # 使用 min_size_blocked 作为日志中的“需要空间”参考值
                    display_needed_size = min_size_blocked if min_size_blocked != float('inf') else 0
                    
                    # 获取最新剩余空间用于日志
                    usage = shutil.disk_usage(LOCAL_PATH)
                    free_bytes_log = usage.free
                    
                    logger.info("磁盘不足，正在检测上传速度以决定是否延迟 Kickstart...")
                    avg_speed = measure_average_upload_speed(client, duration=UPLOAD_SAMPLE_DURATION)

                    if avg_speed > UPLOAD_SPEED_THRESHOLD_KB:
                        logger.info(f"虽然磁盘不足，但当前平均上传速度较高 ({avg_speed:.1f} KB/s)。"
                                    f"重置 Kickstart 倒计时，并继续做种等待 {WAIT_UPLOAD_CHECK/60:.0f} 分钟...")
                        
                        disk_full_start_time = None 
                        time.sleep(WAIT_UPLOAD_CHECK) 
                        
                        if time.time() - last_stalled_check_time > INTERVAL_STALLED_CHECK:
                            process_stalled_tasks(client)
                            last_stalled_check_time = time.time()
                            
                    else:
                        if disk_full_start_time is None:
                            disk_full_start_time = time.time()
                        
                        elapsed = time.time() - disk_full_start_time
                        remaining = DURATION_DISK_DEADLOCK - elapsed
                        
                        logger.warning(f"磁盘空间不足 (上传速度 {avg_speed:.1f} KB/s)! 最小待添加种子需要: {(display_needed_size / 1024**3):.2f} GB, 剩余: {(free_bytes_log / 1024**3):.2f} GB. "
                                       f"Kickstart 倒计时: {remaining:.0f} 秒 (当前倍数: {KICKSTART_MULTIPLIER})")
                        
                        if elapsed > DURATION_DISK_DEADLOCK:
                            kickstart_seeding_tasks(client)
                            disk_full_start_time = time.time() 

                        time.sleep(WAIT_DISK_SPACE)
            
            logger.info("进入下一轮循环...")

        except KeyboardInterrupt:
            logger.info("用户停止了脚本。")
            sys.exit(0)
        except Exception as main_e:
            logger.error(f"运行中发生错误: {main_e}")
            logger.info("10秒后尝试重新连接...")
            client = None
            time.sleep(10)

if __name__ == "__main__":
    main()