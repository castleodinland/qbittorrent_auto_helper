// ==UserScript==
// @name         castle nba做种自动填表助手 (手动触发版)
// @namespace    http://tampermonkey.net/
// @version      1.2
// @description  在网页右上角显示一个按钮，点击后自动填写表单
// @author       Castle
// @match        http*://*/upload*php*
// ==/UserScript==

(function() {
    'use strict';

    /**
     * 核心填表逻辑
     */
    function fillForm() {
        console.log("按钮被点击，开始执行填表逻辑...");

        // 示例 1: 通过 ID 填写邮箱
        const myBBCode = `
[color=DarkRed][font=Comic Sans MS][size=6]转自sportscult，感谢原创作者[/size][/font][/color]

[color=Navy][font=Trebuchet MS][size=4]

[url=https://pixhost.to/show/5653/693689299_b13c7363-09da-4c33-bfd6-0cb93c894a2e.png][img]https://img2.pixhost.to/images/5653/693689299_b13c7363-09da-4c33-bfd6-0cb93c894a2e.png[/img][/url]

`;
        const emailField = document.querySelector('#descr');
        if (emailField) {
            emailField.value = myBBCode;
            // 触发事件确保前端框架识别
            emailField.dispatchEvent(new Event('input', { bubbles: true }));
            console.log("种子描述已填写");
            emailField.style.border = "2px solid red";
        } else {
            console.error("未找到 ID 为 'descr' 的元素");
        }

        // 2. 处理下拉选择框browsecat
        const selectBoxGara = document.querySelector('#browsecat');
        if (selectBoxGara) {
            // 技巧：如果你不知道该填什么值，在 F12 检查里看 <option value="xxx">
            selectBoxGara.value = '407';
            selectBoxGara.style.border = "2px solid red";
            // 关键：触发 change 事件
            selectBoxGara.dispatchEvent(new Event('change', { bubbles: true }));
        }

        let selectBox = document.querySelector('select[name="medium_sel[4]"]');
        if (selectBox) {
            // 2. 设置值。比如你想选“流媒体(WEB-DL)”，从截图看它的 value 是 "4"
            selectBox.value = "4";

            // 3. 极其重要：触发 change 事件，让网页知道你改了选项
            // 使用标准的 Event 构造函数
            const event = new Event('change', { bubbles: true });
            selectBox.dispatchEvent(event);

            console.log("成功选中：流媒体(WEB-DL)");

            // 调试小技巧：给选中的框加个红边，一眼就能看到对没对
            selectBox.style.border = "2px solid red";
        } else {
            console.error("未找到指定的下拉框，请检查 name 属性是否完全匹配。当前尝试的选择器是：select[name='medium_sel[4]']");
        }

        //视频编码
        selectBox = document.querySelector('select[name="codec_sel[4]"]');
        if (selectBox) {
            // 2. 设置值。比如你想选“流媒体(WEB-DL)”，从截图看它的 value 是 "4"
            selectBox.value = "1";

            // 3. 极其重要：触发 change 事件，让网页知道你改了选项
            // 使用标准的 Event 构造函数
            const event = new Event('change', { bubbles: true });
            selectBox.dispatchEvent(event);

            // 调试小技巧：给选中的框加个红边，一眼就能看到对没对
            selectBox.style.border = "2px solid red";
        } else {
            console.error("未找到指定的下拉框，请检查 name 属性是否完全匹配。当前尝试的选择器是：select[name='medium_sel[4]']");
        }

        //音频编码
        selectBox = document.querySelector('select[name="audiocodec_sel[4]"]');
        if (selectBox) {
            selectBox.value = "6";

            // 3. 极其重要：触发 change 事件，让网页知道你改了选项
            // 使用标准的 Event 构造函数
            const event = new Event('change', { bubbles: true });
            selectBox.dispatchEvent(event);

            // 调试小技巧：给选中的框加个红边，一眼就能看到对没对
            selectBox.style.border = "2px solid red";
        } else {
            console.error("未找到指定的下拉框，请检查 name 属性是否完全匹配。当前尝试的选择器是：select[name='medium_sel[4]']");
        }


        // --- 核心更新：根据标题自动识别分辨率 ---
        const nameInput = document.querySelector('#name');
        let autoStandardValue = null;

        if (nameInput) {
            const title = nameInput.value;
            console.log("正在解析标题: ", title);
            
            // 定义分辨率与值的对应关系 (基于截图)
            const resolutionMap = {
                '4320p': '6',
                '2160p': '5',
                '1440p': '7',
                '1080p': '1',
                '1080i': '2',
                '720p': '3',
                'SD': '4'
            };

            // 使用正则匹配，忽略大小写
            const match = title.match(/(4320p|2160p|1440p|1080p|1080i|720p|SD)/i);
            if (match) {
                const matchedKey = match[0].toLowerCase();
                // 转换回Map中对应的标准键名
                const standardKey = Object.keys(resolutionMap).find(key => key.toLowerCase() === matchedKey);
                autoStandardValue = resolutionMap[standardKey];
                console.log(`解析成功：识别到 ${standardKey}，对应值为 ${autoStandardValue}`);
            } else {
                autoStandardValue = '3';
                console.warn("标题中未识别到常见分辨率字段");
            }
        }

        //分辨率
        selectBox = document.querySelector('select[name="standard_sel[4]"]');
        if (selectBox) {
            selectBox.value = autoStandardValue;

            // 3. 极其重要：触发 change 事件，让网页知道你改了选项
            // 使用标准的 Event 构造函数
            const event = new Event('change', { bubbles: true });
            selectBox.dispatchEvent(event);

            // 调试小技巧：给选中的框加个红边，一眼就能看到对没对
            selectBox.style.border = "2px solid red";
        } else {
            console.error("未找到指定的下拉框，请检查 name 属性是否完全匹配。当前尝试的选择器是：select[name='medium_sel[4]']");
        }


        //地区
        selectBox = document.querySelector('select[name="source_sel[4]"]');
        if (selectBox) {
            selectBox.value = "4";

            // 3. 极其重要：触发 change 事件，让网页知道你改了选项
            // 使用标准的 Event 构造函数
            const event = new Event('change', { bubbles: true });
            selectBox.dispatchEvent(event);

            // 调试小技巧：给选中的框加个红边，一眼就能看到对没对
            selectBox.style.border = "2px solid red";
        } else {
            console.error("未找到指定的下拉框，请检查 name 属性是否完全匹配。当前尝试的选择器是：select[name='medium_sel[4]']");
        }

        //制作组
        selectBox = document.querySelector('select[name="team_sel[4]"]');
        if (selectBox) {
            selectBox.value = "5";

            // 3. 极其重要：触发 change 事件，让网页知道你改了选项
            // 使用标准的 Event 构造函数
            const event = new Event('change', { bubbles: true });
            selectBox.dispatchEvent(event);

            // 调试小技巧：给选中的框加个红边，一眼就能看到对没对
            selectBox.style.border = "2px solid red";
        } else {
            console.error("未找到指定的下拉框，请检查 name 属性是否完全匹配。当前尝试的选择器是：select[name='medium_sel[4]']");
        }

    }

    /**
     * 创建浮动按钮 UI
     */
    function createDebugButton() {
        const btn = document.createElement('button');
        btn.innerHTML = '🚀 自动填表';

        // 设置按钮样式，使其固定在右上角，不随页面滚动
        Object.assign(btn.style, {
            position: 'fixed',
            top: '20px',
            right: '20px',
            zIndex: '9999',
            padding: '10px 15px',
            backgroundColor: '#007bff',
            color: 'white',
            border: 'none',
            borderRadius: '5px',
            cursor: 'pointer',
            boxShadow: '0 2px 5px rgba(0,0,0,0.2)',
            fontSize: '14px',
            fontWeight: 'bold'
        });

        // 鼠标悬停效果
        btn.onmouseover = () => btn.style.backgroundColor = '#0056b3';
        btn.onmouseout = () => btn.style.backgroundColor = '#007bff';

        // 绑定点击事件
        btn.onclick = fillForm;

        document.body.appendChild(btn);
        console.log("调试按钮已就绪");
    }

    // 等待页面加载完成后显示按钮
    if (document.readyState === 'complete') {
        createDebugButton();
    } else {
        window.addEventListener('load', createDebugButton);
    }

})();