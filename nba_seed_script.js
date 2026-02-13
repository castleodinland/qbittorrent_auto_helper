// ==UserScript==
// @name         PT发布全自动填表Pro ubits专用 (JSON输入版)
// @namespace    http://tampermonkey.net/
// @version      3.5
// @description  手动输入或粘贴 make_seed_pro.py 生成的 JSON 数据，一键完成 PT 站发布页面填写
// @author       Castle
// @match        http*://*/upload*php*
// @match        http*://*/edit*php*
// @grant        none
// ==/UserScript==

(function () {
    'use strict';

    /**
     * 辅助函数：设置元素值并触发事件
     */
    function setElement(selector, value, isSelect = false) {
        const el = document.querySelector(selector);
        if (el && value) {
            el.value = value;
            el.dispatchEvent(new Event(isSelect ? 'change' : 'input', { bubbles: true }));
            el.style.border = "2px solid #28a745";
            el.style.backgroundColor = "#f0fff4";
            return true;
        }
        return false;
    }

    /**
     * 辅助函数：安全设置下拉框值并触发事件
     */
    function setSelectValue(selector, value, label = "") {
        const select = document.querySelector(selector);
        if (select && value) {
            select.value = value;
            select.dispatchEvent(new Event('change', { bubbles: true }));
            select.style.border = "2px solid #28a745"; // 填充成功显示绿色
            console.log(`[自动填充] ${label}: 匹配成功 -> ${value}`);
            return true;
        }
        return false;
    }

    /**
    * 辅助函数：安全勾选复选框
    */
    function setCheckboxChecked(name, label = "") {
        const checkbox = document.querySelector(`input[type="checkbox"][name="${name}"]`);
        if (checkbox) {
            checkbox.checked = true;
            checkbox.dispatchEvent(new Event('change', { bubbles: true }));
            // 给父元素或自身加个高亮提示
            checkbox.style.outline = "2px solid #28a745";
            console.log(`[自动勾选] ${label} (${name}): 已勾选`);
            return true;
        }
        return false;
    }

    /**
     * 核心逻辑：解析 JSON 并填充表单
     */
    function fillFormWithJSON(jsonText) {
        try {
            if (!jsonText || jsonText.trim() === "") return;

            const data = JSON.parse(jsonText);

            if (!data.mediainfo || !data.title) {
                alert("解析失败：输入的不是有效的发布 JSON 格式，请核对 Python 脚本输出。");
                return;
            }

            // 1. 文本字段填写
            setElement('input[name="small_descr"]', data.subtitle);
            setElement('textarea[name="technical_info"]', data.mediainfo);
            setElement('#descr', data.description);
            setElement('input[name="url"]', data.imdb);

            //================================================================================================
            // --- 2. 解析逻辑 ---

            // 分辨率解析
            const infoField = document.querySelector('textarea[name="technical_info"]');
            const nfo = (infoField ? infoField.value : document.querySelector('#technical_info').value);
            // 1. 优化正则表达式
            // 增加对全角空格、不可见空格的兼容性，并确保匹配到数字后的单位部分
            // /Height\s*:\s*([\d\s,]+)(?:\s*pixels)?/i
            // g 标志可以根据需要决定是否使用，这里针对单次匹配优化
            const heightMatch = nfo.match(/Height\s*:\s*([\d\s\u00A0,]+)/i);
            const scanMatch = nfo.match(/Scan type\s*:\s*(\w+)/);

            let resValue = "";
            if (heightMatch) {
                // 2. 提取并清理数据
                // [\d\s\u00A0,]+ 匹配数字、普通空格、不换行空格(\u00A0)和逗号
                // 清理：去掉所有非数字字符（保留纯数字）
                const rawValue = heightMatch[1].replace(/[^\d]/g, '');
                const h = parseInt(rawValue, 10);

                // 假设 scanMatch 在代码上下文其他地方定义
                // 获取扫描类型：Progressive 或 Interlaced
                const scanMatch = nfo.match(/Scan\s+type\s*:\s*(\w+)/i);
                const isInterlaced = scanMatch && scanMatch[1].toLowerCase().includes('interlaced');

                if (!isNaN(h)) {
                    if (h >= 4320) resValue = "6"; // 4320p
                    else if (h >= 2160) resValue = "5"; // 2160p
                    else if (h >= 1440) resValue = "7"; // 1440p
                    else if (h >= 1080) resValue = isInterlaced ? "2" : "1"; // 1080i 或 1080p
                    else if (h >= 720) resValue = "3"; // 720p
                    else resValue = "4"; // SD
                }
            }

            // 视频编码解析
            let codecValue = "";
            if (nfo.match(/Format\s*:\s*HEVC/i) || nfo.match(/Format\s*:\s*H\.265/i)) codecValue = "7";
            else if (nfo.match(/Format\s*:\s*AVC/i) || nfo.match(/Format\s*:\s*H\.264/i)) codecValue = "1";
            else if (nfo.match(/Format\s*:\s*VC-1/i)) codecValue = "2";
            else if (nfo.match(/Format\s*:\s*MPEG-2/i)) codecValue = "4";

            // 音频编码解析
            let audioValue = "";
            if (nfo.match(/Format\s*:\s*AAC/i)) audioValue = "6";
            else if (nfo.match(/Format\s*:\s*AC-3/i) || nfo.match(/Commercial name\s*:\s*Dolby Digital/i)) audioValue = "14";
            else if (nfo.match(/Format\s*:\s*DTS/i)) {
                if (nfo.match(/DTS-HD/i)) audioValue = "11";
                else audioValue = "3";
            }
            else if (nfo.match(/Format\s*:\s*FLAC/i)) audioValue = "1";
            else if (nfo.match(/Format\s*:\s*MP3/i) || nfo.match(/Format\s*:\s*MPEG Audio/i)) audioValue = "4";

            // --- 3. 执行填充 ---

            // 分辨率
            setSelectValue('select[name="standard_sel[4]"]', resValue, "分辨率");
            // 视频编码
            setSelectValue('select[name="codec_sel[4]"]', codecValue, "视频编码");
            // 音频编码
            setSelectValue('select[name="audiocodec_sel[4]"]', audioValue, "音频编码");

            //================================================================================================


            // 固定项填充
            setSelectValue('#browsecat', '407', "主分类");
            setSelectValue('select[name="medium_sel[4]"]', '4', "媒介");
            setSelectValue('select[name="source_sel[4]"]', '4', "地区");
            setSelectValue('select[name="team_sel[4]"]', '5', "制作组");

            // --- 新增：自动勾选复选框 ---
            setCheckboxChecked('asoffer', '符合悬赏/匿名');
            setCheckboxChecked('uplver', '发布者宣誓/确认');

            console.log("JSON 数据已成功填充到表单。");
        } catch (e) {
            console.error("解析 JSON 出错:", e);
            alert("JSON 格式错误，请确保复制了完整的内容！\n错误详情：" + e.message);
        }
    }

    /**
     * 弹出输入对话框
     */
    function showInputDialogOld() {
        const jsonInput = prompt("请粘贴 Python 脚本生成的 JSON 数据：\n(提示：Ctrl+V 粘贴后点击确定)");
        if (jsonInput !== null) {
            fillFormWithJSON(jsonInput);
        }
    }

    function showInputDialog() {
        // 创建遮罩层
        const overlay = document.createElement('div');
        Object.assign(overlay.style, {
            position: 'fixed',
            top: '0',
            left: '0',
            width: '100%',
            height: '100%',
            backgroundColor: 'rgba(0,0,0,0.5)',
            zIndex: '10000',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center'
        });

        // 创建对话框
        const dialog = document.createElement('div');
        Object.assign(dialog.style, {
            backgroundColor: 'white',
            padding: '20px',
            borderRadius: '10px',
            width: '600px',
            maxWidth: '90%'
        });

        dialog.innerHTML = `
        <h3 style="margin-top:0">请拖拽 JSON 文件或粘贴数据</h3>
        <div id="drop-zone" style="border:2px dashed #ccc; padding:40px; text-align:center; border-radius:8px; margin-bottom:15px; background:#f9f9f9">
            📁 拖拽 JSON 文件到这里
        </div>
        <textarea id="json-input" style="width:100%; height:30px; padding:8px; font-family:monospace; border:1px solid #ddd; border-radius:5px; box-sizing:border-box" placeholder="或直接粘贴 JSON 数据..."></textarea>
        <div style="margin-top:15px; text-align:right">
            <button id="cancel-btn" style="padding:8px 20px; margin-right:8px; cursor:pointer">取消</button>
            <button id="confirm-btn" style="padding:8px 20px; background:#2980b9; color:white; border:none; border-radius:5px; cursor:pointer">确定</button>
        </div>
    `;

        overlay.appendChild(dialog);
        document.body.appendChild(overlay);

        const dropZone = document.getElementById('drop-zone');
        const textarea = document.getElementById('json-input');

        // 拖拽事件
        dropZone.ondragover = (e) => {
            e.preventDefault();
            dropZone.style.borderColor = '#2980b9';
            dropZone.style.background = '#e3f2fd';
        };

        dropZone.ondragleave = () => {
            dropZone.style.borderColor = '#ccc';
            dropZone.style.background = '#f9f9f9';
        };

        dropZone.ondrop = (e) => {
            e.preventDefault();
            dropZone.style.borderColor = '#ccc';
            dropZone.style.background = '#f9f9f9';

            const file = e.dataTransfer.files[0];
            if (file && file.name.endsWith('.json')) {
                const reader = new FileReader();
                reader.onload = (event) => {
                    textarea.value = event.target.result;
                };
                reader.readAsText(file);
            } else {
                alert('请拖拽 JSON 文件');
            }
        };

        // 确定按钮
        document.getElementById('confirm-btn').onclick = () => {
            const jsonInput = textarea.value;
            document.body.removeChild(overlay);
            if (jsonInput.trim()) {
                fillFormWithJSON(jsonInput);
            }
        };

        // 取消按钮
        document.getElementById('cancel-btn').onclick = () => {
            document.body.removeChild(overlay);
        };

        textarea.focus();
    }

    /**
     * 初始化 UI 按钮
     */
    function initUI() {
        if (document.getElementById('auto-fill-btn-pro')) return;

        const btn = document.createElement('button');
        btn.id = 'auto-fill-btn-pro';
        btn.innerText = '⚡ 粘贴 JSON 自动填表';
        Object.assign(btn.style, {
            position: 'fixed',
            top: '60px',
            right: '20px',
            zIndex: '9999',
            padding: '14px 28px',
            backgroundColor: '#2980b9',
            color: 'white',
            border: 'none',
            borderRadius: '50px',
            cursor: 'pointer',
            boxShadow: '0 6px 20px rgba(41, 128, 185, 0.4)',
            fontWeight: 'bold',
            fontSize: '15px',
            transition: 'all 0.3s'
        });

        btn.onmouseover = () => btn.style.transform = 'scale(1.05)';
        btn.onmouseout = () => btn.style.transform = 'scale(1)';

        btn.onclick = (e) => {
            e.preventDefault();
            showInputDialog();
        };

        document.body.appendChild(btn);
    }

    // 执行初始化
    if (document.readyState === 'complete') {
        initUI();
    } else {
        window.addEventListener('load', initUI);
    }
})();
