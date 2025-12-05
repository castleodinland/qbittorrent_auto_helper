package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"io/ioutil"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"sync"
)

// Config holds command line arguments
type Config struct {
	Port string
}

var config Config

// HTMLContent contains the entire frontend application
const HTMLContent = `
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Log Viewer</title>
    <style>
        :root {
            --bg-color: #1e1e1e;
            --sidebar-bg: #252526;
            --text-color: #d4d4d4;
            --accent-color: #007acc;
            --log-bg: #1e1e1e;
            --log-text: #cccccc;
            --tab-active-bg: #37373d;
            --tab-hover-bg: #2a2d2e;
            --btn-bg: #3c3c3c;
            --btn-border: #555;
        }

        * { box-sizing: border-box; margin: 0; padding: 0; }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background-color: var(--bg-color);
            color: var(--text-color);
            height: 100vh;
            height: 100dvh; 
            display: flex;
            flex-direction: column;
            overflow: hidden;
        }

        /* Layout */
        .container {
            display: flex;
            flex: 1;
            height: 100%;
            overflow: hidden;
            position: relative;
        }

        /* Sidebar / Tabs */
        .sidebar {
            width: 250px;
            background-color: var(--sidebar-bg);
            border-right: 1px solid #333;
            display: flex;
            flex-direction: column;
            overflow-y: auto;
            -webkit-overflow-scrolling: touch;
            flex-shrink: 0;
        }

        .sidebar-header {
            padding: 10px;
            font-weight: bold;
            font-size: 0.9rem;
            text-transform: uppercase;
            color: #888;
            border-bottom: 1px solid #333;
            flex-shrink: 0;
        }

        .file-list {
            list-style: none;
            flex: 1;
            overflow-y: auto;
        }

        .file-item {
            padding: 10px 15px;
            cursor: pointer;
            font-size: 14px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            transition: background 0.2s;
            border-left: 3px solid transparent;
        }

        .file-item:hover {
            background-color: var(--tab-hover-bg);
        }

        .file-item.active {
            background-color: var(--tab-active-bg);
            border-left-color: var(--accent-color);
            color: white;
        }

        /* Main Content */
        .main-view {
            flex: 1;
            display: flex;
            flex-direction: column;
            background-color: var(--log-bg);
            min-width: 0;
            overflow: hidden;
        }

        .toolbar {
            height: 42px;
            background-color: #333;
            display: flex;
            align-items: center;
            padding: 0 10px;
            justify-content: space-between;
            font-size: 12px;
            border-bottom: 1px solid #444;
            flex-shrink: 0;
        }

        .status-bar {
            display: flex;
            gap: 10px;
            align-items: center;
            overflow: hidden;
            white-space: nowrap;
            flex: 1;
        }
        
        .file-label {
            font-weight: bold;
            max-width: 150px;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        .refresh-tag {
            color: var(--accent-color);
            opacity: 0;
            transition: opacity 0.2s;
            font-weight: bold;
            font-size: 11px;
        }

        .refresh-tag.active {
            opacity: 1;
            animation: pulse 1s infinite alternate;
        }

        @keyframes pulse {
            from { opacity: 0.3; }
            to { opacity: 1; }
        }

        .time-tag {
            color: #888;
            font-family: monospace;
        }

        .controls {
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .btn {
            background-color: var(--btn-bg);
            border: 1px solid var(--btn-border);
            color: #ccc;
            padding: 4px 8px;
            border-radius: 3px;
            font-size: 11px;
            cursor: pointer;
            user-select: none;
        }

        .btn:active {
            background-color: #555;
        }

        .log-container {
            flex: 1;
            overflow-y: auto;
            padding: 15px;
            font-family: "Consolas", "Monaco", "Courier New", monospace;
            font-size: 13px;
            line-height: 1.5;
            color: var(--log-text);
            white-space: pre-wrap;
            word-break: break-all;
            -webkit-overflow-scrolling: touch;
            scroll-behavior: smooth;
        }

        /* Toggle Switch */
        .switch-label {
            display: flex;
            align-items: center;
            cursor: pointer;
            user-select: none;
            color: #ccc;
        }
        
        .switch-checkbox {
            margin-right: 4px;
        }

        .empty-state {
            display: flex;
            align-items: center;
            justify-content: center;
            height: 100%;
            color: #666;
            flex-direction: column;
        }

        /* Mobile Responsive */
        @media (max-width: 768px) {
            .container {
                flex-direction: column;
            }
            
            .sidebar {
                width: 100%;
                max-height: 120px;
                border-right: none;
                border-bottom: 1px solid #333;
            }

            .file-list {
                display: flex;
                flex-wrap: nowrap;
                overflow-x: auto;
            }

            .file-item {
                flex: 0 0 auto;
                border-left: none;
                border-bottom: 3px solid transparent;
                padding: 10px 15px;
            }

            .file-item.active {
                border-left: none;
                border-bottom-color: var(--accent-color);
            }

            .file-label {
                display: none; /* Hide filename in toolbar on mobile to save space */
            }
        }
    </style>
</head>
<body>

    <div class="container">
        <div class="sidebar">
            <div class="sidebar-header">Log Files</div>
            <ul class="file-list" id="fileList"></ul>
        </div>

        <div class="main-view">
            <div class="toolbar">
                <div class="status-bar">
                    <span id="currentFileLabel" class="file-label">No file</span>
                    <span id="refreshTag" class="refresh-tag">REFRESHING</span>
                    <span id="timeTag" class="time-tag">Updated: --:--:--</span>
                </div>
                <div class="controls">
                    <button class="btn" onclick="scrollToTop()">Top</button>
                    <button class="btn" onclick="scrollToBottom()">End</button>
                    <label class="switch-label">
                        <input type="checkbox" id="autoScrollCheck" class="switch-checkbox" checked>
                        <span>Auto</span>
                    </label>
                </div>
            </div>
            <div class="log-container" id="logContent">
                <div class="empty-state">
                    <h3>Scanning...</h3>
                </div>
            </div>
        </div>
    </div>

<script>
    const state = {
        currentFile: null,
        autoScroll: true,
        logs: [],
        firstLoad: true
    };

    const dom = {
        fileList: document.getElementById('fileList'),
        logContent: document.getElementById('logContent'),
        currentFileLabel: document.getElementById('currentFileLabel'),
        refreshTag: document.getElementById('refreshTag'),
        timeTag: document.getElementById('timeTag'),
        autoScrollCheck: document.getElementById('autoScrollCheck')
    };

    // --- Actions ---
    function scrollToTop() {
        state.autoScroll = false; // User manually scrolled, disable auto
        dom.autoScrollCheck.checked = false;
        dom.logContent.scrollTop = 0;
    }

    function scrollToBottom() {
        dom.logContent.scrollTop = dom.logContent.scrollHeight;
        // If user explicitly clicks End, we can probably re-enable auto-scroll
        state.autoScroll = true;
        dom.autoScrollCheck.checked = true;
    }

    function updateTimeDisplay() {
        const now = new Date();
        dom.timeTag.innerText = "Updated: " + now.toLocaleTimeString();
    }

    // --- API Calls ---
    async function fetchFileList() {
        try {
            const response = await fetch('/api/files');
            const files = await response.json();
            
            // Diff check
            if (JSON.stringify(files) !== JSON.stringify(state.logs)) {
                state.logs = files;
                renderFileList(files);
                
                // Auto-select first file on initial load or if no file selected
                if ((state.firstLoad || !state.currentFile) && files.length > 0) {
                    selectFile(files[0]);
                    state.firstLoad = false;
                }
            } else if (state.firstLoad && files.length > 0) {
                // Should not happen often, but ensures selection if list didn't change but it's first run
                selectFile(files[0]);
                state.firstLoad = false;
            }
        } catch (e) {
            console.error("Failed to fetch file list", e);
        }
    }

    async function fetchLogContent(filename) {
        if (!filename) return;
        
        // Show refreshing animation
        dom.refreshTag.classList.add('active');

        try {
            const response = await fetch('/api/content?file=' + encodeURIComponent(filename));
            if (!response.ok) throw new Error("File not found");
            
            const text = await response.text();
            
            // Only update DOM if content changed
            if (dom.logContent.innerText !== text) {
                dom.logContent.innerText = text;
                if (state.autoScroll) {
                    dom.logContent.scrollTop = dom.logContent.scrollHeight;
                }
            }
            updateTimeDisplay();
        } catch (e) {
            dom.logContent.innerHTML = '<div style="color:red; padding:20px;">Error loading file.</div>';
        } finally {
            // Hide refreshing animation
            setTimeout(() => {
                dom.refreshTag.classList.remove('active');
            }, 300); // Small delay to make sure the flash is visible
        }
    }

    // --- Rendering ---
    function renderFileList(files) {
        dom.fileList.innerHTML = '';
        
        if (files.length === 0) {
            dom.fileList.innerHTML = '<li style="padding:15px; color:#666;">No .log files found</li>';
            dom.logContent.innerHTML = '<div class="empty-state"><h3>No log files found</h3></div>';
            return;
        }

        files.forEach(file => {
            const li = document.createElement('li');
            li.className = 'file-item';
            if (file === state.currentFile) li.classList.add('active');
            li.innerText = file;
            li.onclick = () => selectFile(file);
            dom.fileList.appendChild(li);
        });
    }

    function selectFile(filename) {
        state.currentFile = filename;
        dom.currentFileLabel.innerText = filename || "No file";
        
        // Update UI active state
        Array.from(dom.fileList.children).forEach(li => {
            if (li.innerText === filename) li.classList.add('active');
            else li.classList.remove('active');
        });

        if (filename) {
            dom.logContent.innerText = "Loading...";
            fetchLogContent(filename);
        }
    }

    // --- Event Listeners ---
    dom.autoScrollCheck.addEventListener('change', (e) => {
        state.autoScroll = e.target.checked;
        if (state.autoScroll) {
            dom.logContent.scrollTop = dom.logContent.scrollHeight;
        }
    });

    // --- Loops ---
    setInterval(fetchFileList, 5000);
    fetchFileList(); // Initial load

    setInterval(() => {
        if (state.currentFile) {
            fetchLogContent(state.currentFile);
        }
    }, 1000);

</script>
</body>
</html>
`

func main() {
	// Parse command line arguments
	flag.StringVar(&config.Port, "port", "8085", "Port to serve the web interface")
	flag.Parse()

	// Setup routes
	http.HandleFunc("/", handleIndex)
	http.HandleFunc("/api/files", handleFileList)
	http.HandleFunc("/api/content", handleFileContent)

	fmt.Printf("Starting Log Viewer...\n")
	fmt.Printf("Directory: %s\n", getCurrentDir())
	fmt.Printf("URL:       http://localhost:%s\n", config.Port)
	fmt.Printf("Press Ctrl+C to stop.\n")

	// Start server
	err := http.ListenAndServe(":"+config.Port, nil)
	if err != nil {
		log.Fatal("Error starting server: ", err)
	}
}

// getCurrentDir returns the current working directory safely
func getCurrentDir() string {
	dir, err := os.Getwd()
	if err != nil {
		return "."
	}
	return dir
}

// handleIndex serves the embedded HTML
func handleIndex(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	fmt.Fprint(w, HTMLContent)
}

// handleFileList returns a JSON list of .log files in the current directory
func handleFileList(w http.ResponseWriter, r *http.Request) {
	files, err := filepath.Glob("*.log")
	if err != nil {
		http.Error(w, "Error listing files", http.StatusInternalServerError)
		return
	}

	// Sort files for consistent display
	sort.Strings(files)

	// Return empty array instead of null if no files
	if files == nil {
		files = []string{}
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(files)
}

// handleFileContent reads and returns the content of a specific log file
func handleFileContent(w http.ResponseWriter, r *http.Request) {
	filename := r.URL.Query().Get("file")

	if filename == "" {
		http.Error(w, "File parameter missing", http.StatusBadRequest)
		return
	}

	// SECURITY: Ensure we only read files in current directory and only .log files
	cleanName := filepath.Base(filename)
	if filepath.Ext(cleanName) != ".log" {
		http.Error(w, "Only .log files are allowed", http.StatusForbidden)
		return
	}

	// Verify file exists
	if _, err := os.Stat(cleanName); os.IsNotExist(err) {
		http.Error(w, "File not found", http.StatusNotFound)
		return
	}

	// Read file content
	content, err := ioutil.ReadFile(cleanName)
	if err != nil {
		http.Error(w, "Error reading file", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "text/plain; charset=utf-8")
	w.Write(content)
}

// Simple mutex implementation could be added if concurrent file writes/reads cause issues,
// but for reading logs (which are usually append-only), standard OS file locking usually suffices for display.
var mu sync.Mutex