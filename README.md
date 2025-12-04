# qbittorrent_auto_helper
qbittorrent自动化做种脚本

启动代码：
```bash
nohup python3 auto-torrent.py > /dev/null 2>&1 &
```
```bash
nohup go run detect.go --port 8085 > /dev/null 2>&1 &
```


杀进程代码：
```bash
pgrep -f auto-torrent.py | xargs -r kill -9
```
```bash
kill -9 $(lsof -t -i :8085)
```
