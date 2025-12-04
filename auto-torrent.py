import os
import time
import shutil
import hashlib
import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler
from qbittorrentapi import exceptions

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

# 日志文件名 (v3.9)
LOG_FILENAME = 'auto-torrent-v3.9.log'

# 基础时间间隔配置 (秒)
WAIT_DOWNLOAD_CHECK = 60       # 检查下载是否完成的间隔
WAIT_DISK_SPACE = 60           # 磁盘不足时的重试间隔 (速度低时)
WAIT_NO_TORRENT = 120          # 没有新种子时的重试间隔
WAIT_AFTER_ADD = 5             # 添加种子后的缓冲时间

# 维护与死锁检测配置
INTERVAL_STALLED_CHECK = 1800  # (30分钟) 检查死任务(Stalled)的间隔
DURATION_DISK_DEADLOCK = 300   # (10分钟) 连续磁盘不足触发重启的时间阈值

# --- 上传速度检测配置 ---
UPLOAD_SPEED_THRESHOLD_KB = 200  # (KB/s) 上传速度阈值 (平均值)
WAIT_UPLOAD_CHECK = 300          # (5分钟) 上传速度高时的等待间隔
UPLOAD_SAMPLE_DURATION = 30      # (30秒) 速度检测的采样时长

# --- 慢速下载超时配置 ---
TIMEOUT_DOWNLOAD_HOURS = 1       # 下载超时时间 (小时)

# --- Kickstart 批量配置 ---
KICKSTART_BATCH_SIZE = 5         # 每次触发 Kickstart 时重启的任务数量

# ==========================================
# 全局状态
# ==========================================
# 存储需要进行一次性 Kickstart 的任务哈希集合 (已完成下载，等待上传)
TORRENTS_TO_KICKSTART = set()

# 跟踪当前唯一的正在下载的任务
# {'hash': str, 'start_time': float (timestamp), 'name': str}
ACTIVE_DOWNLOAD_TRACKER = {'hash': None, 'start_time': None, 'name': None}
TIMEOUT_DOWNLOAD_SECONDS = TIMEOUT_DOWNLOAD_HOURS * 60 * 60

# ==========================================
# 日志配置 (v3.9: UTC+8 支持 - 修复版)
# ==========================================
def beijing_time_converter(seconds):
    """
    将日志时间强制转换为 UTC+8 (北京时间)
    """
    return time.gmtime(seconds + 8 * 3600)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

if logger.hasHandlers():
    logger.handlers.clear()

try:
    file_handler = RotatingFileHandler(
        LOG_FILENAME, 
        maxBytes=5*1024*1024, 
        backupCount=3, 
        encoding='utf-8'
    )
    
    # 创建 Formatter 实例
    log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    
    # 关键修复：将转换器赋值给实例属性，而不是类属性
    # 这样避免了被 Python 误识别为绑定方法，从而正确接收参数
    log_formatter.converter = beijing_time_converter
    
    file_handler.setFormatter(log_formatter)
    logger.addHandler(file_handler)
except Exception as e:
    print(f"无法创建日志文件: {e}")
    sys.exit(1)

# 控制台输出也使用相同的 Formatter
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(log_formatter)
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

def verify_torrent_added(client, torrent_hash, timeout=30):
    """轮询确认种子确实已添加到列表中"""
    start_time = time.time()
    while time.time() - start_time < timeout:
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
    
    # v3.9: 增加 .go 到白名单
    whitelist_extensions = ['.py', '.sh', '.log', '.go']
    whitelist_dirs = ['torrent-lib', '.git', '__pycache__']
    # 保护 v3.9 版本的脚本自身
    whitelist_files = [LOG_FILENAME, 'auto-torrent-v3.9.py'] 
    
    lib_path_abs = Path(TORRENT_LIB_PATH).absolute()

    if not target_dir.exists():
        return

    for item in target_dir.iterdir():
        try:
            if item.absolute() == lib_path_abs: continue
            if item.is_dir() and item.name in whitelist_dirs: continue
            if item.is_file() and item.suffix in whitelist_extensions: continue
            if item.is_file() and item.name in whitelist_files: continue
            
            # 跳过所有 .slow 标记的种子文件
            if item.is_file() and item.suffix == '.slow' and item.stem.endswith('.torrent'): continue
            if item.is_file() and item.name.endswith('.torrent.slow'): continue

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
    logger.warning(f"下载超时 ({TIMEOUT_DOWNLOAD_HOURS}小时): 任务 {t_name} (Hash: {t_hash[:10]}) 未完成，开始清理...")
    
    # 1. 删除任务和数据
    try:
        client.torrents_delete(torrent_hashes=t_hash, delete_files=True)
        logger.info(f"已从 qBittorrent 删除慢速任务和数据: {t_name}")
    except Exception as e:
        logger.error(f"删除慢速任务 {t_name} 失败: {e}")
        
    # 2. 标记种子文件为 .slow
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
    """(功能 1) 处理死任务：删除任务、文件并重命名种子"""
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
                # 区别于 .slow，使用 .dead
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
    """(辅助功能) 统计本地库中尚未添加到客户端的种子数量"""
    try:
        remote_torrents = client.torrents_info()
        remote_hashes = {t.hash.lower() for t in remote_torrents}
        lib_path = Path(TORRENT_LIB_PATH)
        count = 0
        # 排除 .slow 和 .dead 标记的文件
        for t_file in lib_path.glob('*.torrent'):
            t_hash, _ = get_torrent_info_from_file(t_file)
            if t_hash and t_hash not in remote_hashes:
                count += 1
        return count
    except Exception as e:
        logger.warning(f"统计剩余种子失败: {e}")
        return -1

def initialize_kickstart_queue(client):
    """脚本启动时，只将当前处于**下载状态**的任务加入 Kickstart 队列。"""
    global TORRENTS_TO_KICKSTART
    logger.info(f"检查启动前正在下载的任务 (Tag: {TORRENT_TAG})，将其加入 Kickstart 队列...")
    
    try:
        torrents = client.torrents_info(tag=TORRENT_TAG, filter='downloading')
        
        for t in torrents:
            TORRENTS_TO_KICKSTART.add(t.hash)
            logger.debug(f"已将正在下载任务 {t.name} (State: {t.state}) 加入 Kickstart 队列。")

        logger.info(f"Kickstart 队列初始化完成，待处理任务数 (当前正在下载): {len(TORRENTS_TO_KICKSTART)}")

    except Exception as e:
        logger.error(f"初始化 Kickstart 队列失败: {e}")

def initialize_download_tracker(client):
    """(v3.7) 脚本启动时，检查是否有正在下载的任务，并开始计时"""
    global ACTIVE_DOWNLOAD_TRACKER
    
    try:
        # 查找所有正在下载 (downloading) 且带有特定标签的任务
        downloading_torrents = client.torrents_info(tag=TORRENT_TAG, filter='downloading')
        
        if downloading_torrents:
            # 只关注第一个下载中的任务作为当前活动任务
            active_t = downloading_torrents[0]
            
            ACTIVE_DOWNLOAD_TRACKER = {
                'hash': active_t.hash,
                'start_time': time.time(),
                'name': active_t.name
            }
            logger.info(f"启动时发现正在下载任务: {active_t.name[:30]}...，开始下载超时计时 ({TIMEOUT_DOWNLOAD_HOURS}小时)。")
        else:
            logger.info("启动时未发现正在下载的任务。")

    except Exception as e:
        logger.error(f"初始化下载计时器失败: {e}")

def check_and_update_active_download(client):
    """(v3.7) 检查当前活动的下载任务是否已改变或完成，并更新计时器状态"""
    global ACTIVE_DOWNLOAD_TRACKER
    
    try:
        # 1. 获取当前下载任务 (只取第一个)
        downloading_torrents = client.torrents_info(tag=TORRENT_TAG, filter='downloading')
        current_active_hash = downloading_torrents[0].hash if downloading_torrents else None
        
        # 2. 如果当前有任务在下载
        if current_active_hash:
            current_active_name = downloading_torrents[0].name
            
            # A. 任务哈希匹配：继续计时
            if current_active_hash == ACTIVE_DOWNLOAD_TRACKER['hash']:
                return
            
            # B. 任务哈希不匹配 (新任务开始下载或旧任务完成/被删除)
            else:
                if ACTIVE_DOWNLOAD_TRACKER['hash']:
                    # 只有当旧任务和新任务的哈希不匹配时，才打印旧任务停止计时的信息
                    logger.info(f"下载任务已切换/完成。旧任务 {ACTIVE_DOWNLOAD_TRACKER['name'][:30]}... 停止计时。")
                    
                # 记录新的任务信息并重置计时
                ACTIVE_DOWNLOAD_TRACKER = {
                    'hash': current_active_hash,
                    'start_time': time.time(),
                    'name': current_active_name
                }
                logger.info(f"新下载任务 {current_active_name[:30]}... 开始计时 ({TIMEOUT_DOWNLOAD_HOURS}小时)。")

        # 3. 如果当前没有任务在下载
        else:
            if ACTIVE_DOWNLOAD_TRACKER['hash']:
                logger.info(f"下载任务已完成/停止。任务 {ACTIVE_DOWNLOAD_TRACKER['name'][:30]}... 停止计时。")
            
            # 清除计时器
            ACTIVE_DOWNLOAD_TRACKER = {'hash': None, 'start_time': None, 'name': None}
            
    except Exception as e:
        logger.error(f"更新下载计时器状态失败: {e}")
        # 如果出错，为安全起见，暂时停止跟踪
        ACTIVE_DOWNLOAD_TRACKER = {'hash': None, 'start_time': None, 'name': None}

def check_for_timeout_and_delete(client):
    """(v3.7) 检查活动下载任务是否超时，如果超时则删除并标记种子"""
    global ACTIVE_DOWNLOAD_TRACKER
    
    if ACTIVE_DOWNLOAD_TRACKER['hash']:
        elapsed = time.time() - ACTIVE_DOWNLOAD_TRACKER['start_time']
        
        if elapsed > TIMEOUT_DOWNLOAD_SECONDS:
            t_hash = ACTIVE_DOWNLOAD_TRACKER['hash']
            t_name = ACTIVE_DOWNLOAD_TRACKER['name']
            
            logger.warning(f"任务 {t_name} 下载超时 ({elapsed:.0f}秒)，开始执行清理...")
            
            # 1. 执行清理操作
            cleanup_slow_torrent(client, t_hash, t_name)
            
            # 2. 清除跟踪记录
            ACTIVE_DOWNLOAD_TRACKER = {'hash': None, 'start_time': None, 'name': None}
            
            # 返回 True 表示已执行清理，外部循环可能需要立即重新检查下载状态
            return True
        else:
            remaining = TIMEOUT_DOWNLOAD_SECONDS - elapsed
            logger.debug(f"任务 {ACTIVE_DOWNLOAD_TRACKER['name'][:30]}... 剩余超时时间: {remaining:.0f} 秒")
            
    return False

def kickstart_seeding_tasks(client):
    """(v3.8 核心升级) 批量重启 Kickstart 队列中的任务"""
    global TORRENTS_TO_KICKSTART
    
    # 1. 检查队列是否为空
    if not TORRENTS_TO_KICKSTART:
        logger.info("Kickstart 队列为空，没有任务可以重启。")
        return

    logger.warning(">>> 触发 Kickstart 机制：磁盘空间长期不足，准备批量重启待处理任务...")

    # 2. 准备批次
    all_hashes = list(TORRENTS_TO_KICKSTART)
    # 取前 N 个，如果不足 N 个，切片会自动取所有
    batch_hashes = all_hashes[:KICKSTART_BATCH_SIZE]
    
    # 用于收集真正要执行操作的有效哈希
    valid_exec_list = []
    # 用于收集无论是否成功都需要从全局队列移除的哈希 (包括不存在的、执行过的)
    removal_list = []

    try:
        # 3. 批量查询状态，进行筛选
        # 获取这一批次的任务详情
        t_info_list = client.torrents_info(torrent_hashes=batch_hashes)
        
        # 建立映射方便查找：hash -> task_info
        found_map = {t.hash: t for t in t_info_list}
        
        for h in batch_hashes:
            if h not in found_map:
                # 情况 A: 任务在 qBittorrent 中已不存在
                logger.warning(f"Kickstart 任务 {h[:10]} 不存在，将从队列移除。")
                removal_list.append(h)
                continue
            
            t = found_map[h]
            if t.progress < 1.0:
                # 情况 B: 任务还未下载完成
                logger.warning(f"Kickstart 任务 {t.name[:20]}... 尚未完成 ({t.progress*100:.1f}%)，保留在队列中跳过。")
                # 注意：这里我们不加入 removal_list，让它留在全局队列里，等下次它下载完了再处理
                continue
            
            # 情况 C: 任务有效且已完成
            valid_exec_list.append(h)
            removal_list.append(h) # 执行后需要移除

        # 4. 批量执行操作
        if valid_exec_list:
            logger.info(f"开始批量 Kickstart {len(valid_exec_list)} 个任务...")
            
            # 4.1 暂停
            client.torrents_pause(torrent_hashes=valid_exec_list)
            logger.info(f"已批量暂停 {len(valid_exec_list)} 个任务，等待 20 秒...")
            time.sleep(20)
            
            # 4.2 恢复
            client.torrents_resume(torrent_hashes=valid_exec_list)
            logger.info(f"已批量恢复 {len(valid_exec_list)} 个任务，等待 20 秒...")
            time.sleep(20)
            
            # 4.3 Reannounce
            client.torrents_reannounce(torrent_hashes=valid_exec_list)
            logger.info(f"已批量 Reannounce {len(valid_exec_list)} 个任务。等待 20 秒以确保 Tracker 更新...")
            time.sleep(20)
            
            logger.info(f"批量 Kickstart 完成。")
        else:
            logger.info("本批次没有需要执行 Kickstart 的有效任务。")

        # 5. 从全局队列中移除已处理或失效的任务
        for h in removal_list:
            if h in TORRENTS_TO_KICKSTART:
                TORRENTS_TO_KICKSTART.remove(h)
        
        logger.info(f"已更新队列，剩余待处理 Kickstart 任务数: {len(TORRENTS_TO_KICKSTART)}")

    except Exception as e:
        logger.error(f"执行批量 Kickstart 失败: {e}")

# ==========================================
# 主逻辑循环
# ==========================================

def main():
    logger.info("脚本服务 v3.9 已启动...")
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
                # V3.5: 启动时初始化 Kickstart 队列
                initialize_kickstart_queue(client)
                # V3.7: 启动时初始化下载任务计时器
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
                
                # V3.7 核心逻辑：检查和更新当前下载任务状态及超时
                check_and_update_active_download(client)
                
                if check_for_timeout_and_delete(client):
                    # 如果任务因超时被删除，立即重新检查下载状态，不等待
                    continue
                
                pending_count = count_unadded_torrents(client)
                count_msg = f"{pending_count}" if pending_count >= 0 else "未知"
                
                logger.info(f"当前仍有未完成的下载任务，等待 {WAIT_DOWNLOAD_CHECK} 秒... [待添加种子: {count_msg} 个]")
                
                time.sleep(WAIT_DOWNLOAD_CHECK)
                if time.time() - last_stalled_check_time > INTERVAL_STALLED_CHECK:
                    process_stalled_tasks(client)
                    last_stalled_check_time = time.time()

            # -------------------------------------------------
            # 步骤 1.5: 初始做种保护期 (下载完成后的首次检测)
            # -------------------------------------------------
            while True:
                # V3.7: 任务完成时，下载跟踪器应已在 check_and_update_active_download 中清除
                # 此时 ACTIVE_DOWNLOAD_TRACKER['hash'] 应该为 None
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
            # 步骤 3-5: 添加新任务 & 磁盘死锁检测
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
                found_new = False
                
                torrent_files = sorted(lib_path.glob('*.torrent'))
                for t_file in torrent_files:
                    t_hash, t_size = get_torrent_info_from_file(t_file)
                    # 排除已标记为慢速或死档的文件
                    if t_file.name.endswith(('.slow', '.dead')): 
                        logger.debug(f"跳过已标记种子: {t_file.name}")
                        continue 

                    if not t_hash: continue
                    if t_hash not in remote_hashes:
                        candidate_path = t_file
                        candidate_size = t_size
                        candidate_hash = t_hash
                        found_new = True
                        logger.info(f"发现新种子: {t_file.name}, 大小: {t_size / (1024**3):.2f} GB")
                        break 
                
                if not found_new:
                    logger.info(f"没有发现新种子，等待 {WAIT_NO_TORRENT} 秒后重新扫描...")
                    disk_full_start_time = None 
                    time.sleep(WAIT_NO_TORRENT)
                    
                    if time.time() - last_stalled_check_time > INTERVAL_STALLED_CHECK:
                        process_stalled_tasks(client)
                        last_stalled_check_time = time.time()
                    continue 
                
                space_ok, free_bytes = check_disk_space(candidate_size)
                
                if space_ok:
                    disk_full_start_time = None # 空间足够，清零倒计时
                    
                    logger.info(f"磁盘空间充足，开始添加任务: {candidate_path.name}")
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
                            
                            # 成功添加的任务加入 Kickstart 队列
                            TORRENTS_TO_KICKSTART.add(candidate_hash)
                            logger.info(f"新添加任务 {candidate_hash[:10]} 已加入 Kickstart 队列。")
                            
                            # V3.7: 新任务的计时将在下一轮 while has_unfinished_downloads 循环开始时设置
                            
                            time.sleep(WAIT_AFTER_ADD)
                        else:
                            logger.error("超时：任务未出现在列表中，可能添加失败或客户端响应慢。")
                            time.sleep(10)

                    except Exception as e:
                        logger.error(f"添加种子失败: {e}")
                        time.sleep(10)
                else:
                    # >>> 磁盘不足时，先检查上传速度 <<<
                    logger.info("磁盘不足，正在检测上传速度以决定是否延迟 Kickstart...")
                    avg_speed = measure_average_upload_speed(client, duration=UPLOAD_SAMPLE_DURATION)

                    if avg_speed > UPLOAD_SPEED_THRESHOLD_KB:
                        # 速度高，进入“保护期”逻辑
                        logger.info(f"虽然磁盘不足，但当前平均上传速度较高 ({avg_speed:.1f} KB/s)。"
                                    f"重置 Kickstart 倒计时，并继续做种等待 {WAIT_UPLOAD_CHECK/60:.0f} 分钟...")
                        
                        disk_full_start_time = None # 关键：重置倒计时
                        time.sleep(WAIT_UPLOAD_CHECK) # 长等待，类似做种保护
                        
                        # 等待期间顺便检查死任务
                        if time.time() - last_stalled_check_time > INTERVAL_STALLED_CHECK:
                            process_stalled_tasks(client)
                            last_stalled_check_time = time.time()
                            
                    else:
                        # 速度低，执行 Kickstart 倒计时逻辑
                        if disk_full_start_time is None:
                            disk_full_start_time = time.time()
                        
                        elapsed = time.time() - disk_full_start_time
                        remaining = DURATION_DISK_DEADLOCK - elapsed
                        
                        logger.warning(f"磁盘空间不足 (上传速度 {avg_speed:.1f} KB/s)! 需要: {(candidate_size / 1024**3):.2f} GB, 剩余: {(free_bytes / 1024**3):.2f} GB. "
                                       f"Kickstart 倒计时: {remaining:.0f} 秒")
                        
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