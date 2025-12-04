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
        }

        * { box-sizing: border-box; margin: 0; padding: 0; }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background-color: var(--bg-color);
            color: var(--text-color);
            height: 100vh;
            /* Mobile viewport height fix */
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
            min-width: 0; /* Fix flex child overflow */
            overflow: hidden; /* Ensure inner scroll works */
        }

        .toolbar {
            height: 40px;
            background-color: #333;
            display: flex;
            align-items: center;
            padding: 0 15px;
            justify-content: space-between;
            font-size: 12px;
            border-bottom: 1px solid #444;
            flex-shrink: 0;
        }

        .status-bar {
            display: flex;
            gap: 15px;
            align-items: center;
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
            /* Mobile Scroll Fixes */
            -webkit-overflow-scrolling: touch;
            scroll-behavior: smooth;
        }

        /* Toggle Switch */
        .switch-label {
            display: flex;
            align-items: center;
            cursor: pointer;
            user-select: none;
        }
        
        .switch-checkbox {
            margin-right: 5px;
        }

        .loading {
            color: #888;
            text-align: center;
            margin-top: 20px;
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
                height: auto;
                max-height: 150px; /* Collapsible area for tabs */
                border-right: none;
                border-bottom: 1px solid #333;
                flex-shrink: 0;
            }

            .file-list {
                display: flex;
                flex-wrap: nowrap;
                overflow-x: auto;
                -webkit-overflow-scrolling: touch;
            }

            .file-item {
                flex: 0 0 auto;
                border-left: none;
                border-bottom: 3px solid transparent;
                padding: 12px 15px;
            }

            .file-item.active {
                border-left: none;
                border-bottom-color: var(--accent-color);
            }
        }
    </style>
</head>
<body>

    <div class="container">
        <!-- Sidebar for File List -->
        <div class="sidebar">
            <div class="sidebar-header">Log Files</div>
            <ul class="file-list" id="fileList">
                <!-- Log files will be injected here -->
            </ul>
        </div>

        <!-- Main Viewing Area -->
        <div class="main-view">
            <div class="toolbar">
                <div class="status-bar">
                    <span id="currentFileLabel">No file selected</span>
                    <span id="updateStatus" style="color: #666;">Waiting...</span>
                </div>
                <div class="controls">
                    <label class="switch-label">
                        <input type="checkbox" id="autoScrollCheck" class="switch-checkbox" checked>
                        Auto-Scroll
                    </label>
                </div>
            </div>
            <div class="log-container" id="logContent">
                <div class="empty-state">
                    <h3>Select a log file to view</h3>
                    <p>Files in current directory are listed automatically.</p>
                </div>
            </div>
        </div>
    </div>

<script>
    const state = {
        currentFile: null,
        autoScroll: true,
        logs: [],
        lastFetchHash: ""
    };

    const dom = {
        fileList: document.getElementById('fileList'),
        logContent: document.getElementById('logContent'),
        currentFileLabel: document.getElementById('currentFileLabel'),
        status: document.getElementById('updateStatus'),
        autoScrollCheck: document.getElementById('autoScrollCheck')
    };

    // --- Helpers ---
    function formatTime() {
        const now = new Date();
        return now.toLocaleTimeString();
    }

    function scrollToBottom() {
        if (state.autoScroll) {
            dom.logContent.scrollTop = dom.logContent.scrollHeight;
        }
    }

    // --- API Calls ---
    async function fetchFileList() {
        try {
            const response = await fetch('/api/files');
            const files = await response.json();
            
            // Basic diff check to avoid rebuilding DOM if list hasn't changed (by length for simplicity)
            // A real app might do deep comparison, but this is sufficient.
            if (JSON.stringify(files) !== JSON.stringify(state.logs)) {
                state.logs = files;
                renderFileList(files);
            }
        } catch (e) {
            console.error("Failed to fetch file list", e);
        }
    }

    async function fetchLogContent(filename) {
        if (!filename) return;
        
        dom.status.innerText = "Refreshing...";
        try {
            const response = await fetch('/api/content?file=' + encodeURIComponent(filename));
            if (!response.ok) throw new Error("File not found");
            
            const text = await response.text();
            
            // Only update DOM if content is different
            if (dom.logContent.innerText !== text) {
                dom.logContent.innerText = text;
                scrollToBottom();
            }
            dom.status.innerText = "Updated: " + formatTime();
        } catch (e) {
            dom.logContent.innerHTML = '<div style="color:red; padding:20px;">Error loading file. It may have been deleted.</div>';
            dom.status.innerText = "Error";
        }
    }

    // --- Rendering ---
    function renderFileList(files) {
        dom.fileList.innerHTML = '';
        
        if (files.length === 0) {
            dom.fileList.innerHTML = '<li style="padding:15px; color:#666;">No .log files found</li>';
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

        // If current file is not in new list, deselect
        if (state.currentFile && !files.includes(state.currentFile)) {
            selectFile(null);
        }
    }

    function selectFile(filename) {
        state.currentFile = filename;
        dom.currentFileLabel.innerText = filename || "No file selected";
        
        // Update UI active state
        Array.from(dom.fileList.children).forEach(li => {
            if (li.innerText === filename) li.classList.add('active');
            else li.classList.remove('active');
        });

        if (filename) {
            dom.logContent.innerText = "Loading...";
            fetchLogContent(filename);
        } else {
            dom.logContent.innerHTML = '<div class="empty-state"><h3>Select a log file to view</h3></div>';
        }
    }

    // --- Event Listeners ---
    dom.autoScrollCheck.addEventListener('change', (e) => {
        state.autoScroll = e.target.checked;
        if (state.autoScroll) scrollToBottom();
    });

    // --- Loops ---
    // 1. Refresh file list every 5 seconds
    setInterval(fetchFileList, 5000);
    fetchFileList(); // Initial load

    // 2. Refresh content every 1 second
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
	// This prevents path traversal attacks like ../../etc/passwd
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
	// Note: For extremely large log files, this reads the whole file into memory.
	// For a simple tool, this is acceptable. For production handling GBs of logs,
	// seek/tail logic would be needed.
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