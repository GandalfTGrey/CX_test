# 实验性能追踪 · 前端更新版

运行环境保持 Python 3.8.18、Streamlit 1.40.1、streamlit-aggrid 1.0.5、Pandas 2.0.3、NumPy 1.21.6。已有依赖不需要重装。

## 启动

```bash
cd streamlit_dashboard_frontend
python -m streamlit run app.py --server.fileWatcherType none
```

只运行本包的 `app.py`，不要运行旧目录中的 `leaderboard/main.py`。页面在侧栏接收服务端可访问的真实评测根目录，点击“应用筛选”后查询。也可预设数据源：

```bash
DASHBOARD_DATA_ROOT="/真实评测结果根目录" python -m streamlit run app.py --server.fileWatcherType none
```

本版没有演示数据来源、生成入口或默认示例回退。未设置目录时只显示配置引导，不创建数据。

## 使用

八个任务名称和顺序保持：GOD · 占用感知、COG · 道路认知、BEV · 障碍物、PNC · 规划、PVSeg · 语义分割、Depth · 深度、Navi · 导航、BEV-new · 障碍物。

- “任务指标”保留业务参数、计算和表格；BEV 高级 IoU / 类别条件在“高级指标参数”按钮中设置，下方显示选中条件。
- “基线对比”按数据源、任务和评测文件保存基线。翻页继续使用该基线，必要时只补读一个参考结果；基线不可用时提示重选，不自动替换。
- “Checkpoint 趋势”先选实验再读本页历史点；最新开关在该视图禁用，缺失值保留断点。
- “训练速度”独立读取日志，不使用评测关键词和结果分页。OBS 仍依赖已有 moxing 配置。
- 每页可选 10 / 25 / 50 / 100，只有一套服务端分页。表内排序、过滤和 CSV 只针对本页，含额外参考基线。
- 输入尚未应用时显示提示；读取、空结果、部分诊断、失败和取消分别反馈。保留旧结果时说明实际文件、条件、页码和版本时间。
- 已选指标、距离和视图选项在当前会话中保存；版本就绪时间使用 UTC+8，缓存命中不意味着重新读取源文件。

## 读取和目录

沿用现有本地 SQLite 索引和后台读取。索引默认放在 `/tmp/leaderboard-index`；持久化时用 `DASHBOARD_INDEX_DIR` 指定可写的本地磁盘目录，不能放到 NFS。普通筛选和任务切换查索引，刷新才发现目录，原始实验文件只读。

目录布局：

```text
根目录/实验名@60000/pnc_eval.json
根目录/实验名/eval/iter_60000/pnc_eval.json
根目录/实验名/pnc_eval.json                  # iteration 0
```

保持软链接、标记、可选元数据、JSON 稳定读取与单文件错误隔离。每页内容预算32 MiB、单文件16 MiB，超限单独说明，不整批清空。STAR / SUN 默认置顶，HIDE 始终排除。任意所选基线不受分页移除，但不会绕过隐藏标记或重复来源检查。

## 测试

已有 pytest 时执行 `python -m pytest -q`。测试会在系统临时目录生成合成 fixture，仅用于回归，不在产品中提供生成接口，也不附带生成后的数据。测试覆盖八任务、业务解析、索引规模、分页、控件状态、取消、旧结果说明、基线和CSV范围。

运行服务端目标为 Linux；Windows 可使用浏览器访问。索引锁使用 fcntl。当前仍不提供跨页全局数值排序、全量后台 CSV 导出或未经提供的外部逐帧可视化模块。
