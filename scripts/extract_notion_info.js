/**
 * ==========================================
 * Notion AI 信息提取脚本
 * ==========================================
 * 使用方法：
 * 1. 登录 https://www.notion.so/ai
 * 2. 按 F12 打开开发者工具
 * 3. 切换到 Console 标签
 * 4. 粘贴本脚本并回车
 * 5. 手动���入你的 token_v2（从 Application → Cookies 复制）
 * 6. 结果会自动复制到剪贴板
 * ==========================================
 */

(function() {
    console.log('%c[Notion AI 信息提取器]', 'font-size: 16px; font-weight: bold; color: #00a699;');
    console.log('%c正在提取信息...', 'color: #666;');

    const info = {
        token_v2: 'YOUR_TOKEN_V2_HERE',  // 需要手动填入
        space_id: '',
        user_id: '',
        space_view_id: '',
        user_name: '',
        user_email: ''
    };

    // 方法 1: 从 Notion API 获取
    (async function() {
        try {
            const resp = await fetch('/api/v3/loadUserContent', {
                method: 'POST',
                headers: { 'content-type': 'application/json' },
                body: JSON.stringify({}),
                credentials: 'include'
            });

            if (resp.ok) {
                const data = await resp.json();
                const rm = data.recordMap || {};

                // 提取 user_id + 用户名/邮箱
                const userIds = Object.keys(rm.notion_user || {});
                if (userIds.length > 0) {
                    info.user_id = userIds[0];
                    const userVal = rm.notion_user[userIds[0]]?.value;
                    if (userVal) {
                        info.user_name = userVal.given_name || userVal.name || '';
                        info.user_email = userVal.email || '';
                    }
                }

                // 提取 space_id
                const spaceIds = Object.keys(rm.space || {});
                if (spaceIds.length > 0) {
                    info.space_id = spaceIds[0];
                }

                // 提取 space_view_id
                const svIds = Object.keys(rm.space_view || {});
                if (svIds.length > 0) {
                    info.space_view_id = svIds[0];
                }
            }
        } catch (err) {
            console.warn('%c⚠️ API 调用失败', 'color: #ff9800;', err.message);
        }

        // 输出结果
        outputResult();
    })();

    function outputResult() {
        const envOutput = `NOTION_ACCOUNTS='[{"token_v2":"${info.token_v2}","space_id":"${info.space_id}","user_id":"${info.user_id}","space_view_id":"${info.space_view_id}","user_name":"${info.user_name}","user_email":"${info.user_email}"}]'`;

        console.log('%c✅ 提取完成！', 'color: #00c853; font-size: 14px; font-weight: bold;');
        console.log('%c📋 已自动获取的信息：', 'color: #00c853;');
        console.table({
            'space_id': info.space_id || '❌ 未获取到',
            'user_id': info.user_id || '❌ 未获取到',
            'space_view_id': info.space_view_id || '❌ 未获取到',
            'user_name': info.user_name || '❌ 未获取到',
            'user_email': info.user_email || '❌ 未获取到'
        });
        console.log('%c⚠️ 请手动替换下方的 token_v2 值：', 'color: #ff9800; font-weight: bold;');
        console.log('%c' + envOutput, 'color: #00a699;');
        console.log('%c\n💡 使用步骤：', 'color: #666; font-weight: bold;');
        console.log('%c  1. 复制上面的 NOTION_ACCOUNTS=... 内容', 'color: #666;');
        console.log('%c  2. 将 YOUR_TOKEN_V2_HERE 替换为你的实际 token_v2', 'color: #666;');
        console.log('%c  3. 粘贴到 .env 文件中', 'color: #666;');

        // 复制到剪贴板
        navigator.clipboard.writeText(envOutput)
            .then(() => console.log('%c✅ 已复制到剪贴板', 'color: #00c853; font-weight: bold;'))
            .catch(() => console.warn('%c⚠️ 自动复制失败，请手动复制', 'color: #ff9800;'));

        return info;
    }
})();
