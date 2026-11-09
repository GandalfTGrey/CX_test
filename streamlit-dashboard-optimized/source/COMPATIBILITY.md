# Python 3.8.18 兼容版

此版本直接适配现有环境，不要求升级 Python，也不要求将已有 Streamlit 1.39.0 切换为 1.40.1。

## 已验证组合

| Python | Streamlit | streamlit-aggrid | Pandas | NumPy | Plotly | 自动测试 |
|---|---|---|---|---|---|---|
| 3.8.18 | 1.39.0 | 1.0.5 | 2.0.3 | 1.24.4 | 5.24.1 | 55 项通过 |
| 3.8.18 | 1.40.1 | 1.0.5 | 2.0.3 | 1.24.4 | 5.24.1 | 55 项通过 |

两组均在真实 Python 3.8.18 解释器下运行 pytest 8.3.5，包含八类任务专用表格、基线分析、checkpoint 趋势、训练日志与会话切换。浏览器在 1.40.1 下验证八任务的 AgGrid、基线和趋势图，未出现页面脚本错误。

## 代码修正

- 使用 `use_container_width=True`，移除不支持的 `width='stretch'`。
- 下载按钮用 `on_click=None`，移除新版本才支持的回调参数。
- 任务选择使用水平 `st.radio`，保留原会话 key。
- 移除 `st.metric(border=True)`；使用合法主题字体 `sans serif`。
- 移除 Python 3.9 才支持的 `removeprefix`、`removesuffix`。
- 类型标注延迟求值，已通过 Python 3.8 的实际模块导入和运行验证。
- `dashboard/compat.py` 为 AgGrid 创建独立异常命名空间；不修改全局模块或 site-packages。Stop/Rerun 信号原样透传，真正的 Marshall 错误保留。
- 文件监视使用 `poll`，避免原先 inotify 数量上限错误。

## 在已有部署上使用

解压本包到优化版 `leaderboard_new/`，覆盖该优化版的程序文件、`dashboard/` 和 `.streamlit/`；不要将它当作原版 `leaderboard/main.py` 的依赖补丁。

在已有 Conda 环境中，从解压后的项目目录执行：

```bash
python -m streamlit run app.py --server.fileWatcherType=poll
```

无需为了这次修复重新安装整套依赖。已安装 Pandas、NumPy、Plotly 且可导入时，直接启动；只在缺少包时按 `requirements.txt` 的 Python 3.8 兼容范围补充。

源码包不包含实验数据、截图或环境文件。默认演示模式下可点击「生成模拟演示数据」；接入真实评测时在侧栏指定现有目录，或设置 `DASHBOARD_DATA_ROOT`。真实目录及 OBS 账号尚未远程接入验证。
