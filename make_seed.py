import os
import subprocess
import math
import sys

# ================= 配置区域 =================
# 参数 1: 你的 PT 站 Announce URL (Passkey)
ANNOUNCE_URL = "https://tracker.qingwapt.com/announce.php"

# 参数 2: 需要做种的完整目录路径 (末尾不要带斜杠)
TARGET_DIR = "/home/pt_main/baobao/凯叔西游记"
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

    print(f"--- 启动 PT 制作自动化流程 ---")
    print(f"目标目录: {TARGET_DIR}")
    
    # 2. 生成 NFO (寻找第一个视频文件)
    print(f"\n[1/3] 正在生成 NFO 文件...")
    video_file = None
    for root, dirs, files in os.walk(TARGET_DIR):
        for f in files:
            if f.lower().endswith(('.mp4', '.mkv', '.avi', '.ts')):
                video_file = os.path.join(root, f)
                break
        if video_file: break
    
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

    # 3. 计算 Piece Size 并制作种子
    print(f"\n[2/3] 正在制作种子文件...")
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
            
            print(f"\n[3/3] 制作完成！")
            print(f"种子文件: {torrent_path}")
            print(f"NFO 文件: {nfo_path}")
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