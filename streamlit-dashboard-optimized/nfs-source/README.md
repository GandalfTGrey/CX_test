# 实验性能追踪 · NFS 本地索引版

本版保留任务名称、顺序、入口、原有业务指标解析与选择器：

1. GOD · 占用感知
2. COG · 道路认知
3. BEV · 障碍物
4. PNC · 规划
5. PVSeg · 语义分割
6. Depth · 深度
7. Navi · 导航
8. BEV-new · 障碍物

目标环境：**Python 3.8.18 / Streamlit 1.40.1 / streamlit-aggrid 1.0.5 / pandas 2.0.3 / NumPy 1.21.6**。已有依赖无需重装，不需要修改已安装包。

## 启动

解压完整目录，在原有 Conda 环境运行：

```bash
cd /实际路径/streamlit_dashboard_nfs
python -m streamlit run app.py --server.fileWatcherType none
```

入口是本包的 `app.py`。运行原来的 `leaderboard/main.py` 不会使用本版加载器。保留原版目录，用独立目录试运行。

左侧选“本地目录”，填写服务端能访问的评测根目录，再点“应用筛选”。也可指定启动数据源：

```bash
DASHBOARD_DATA_ROOT="/真实评测结果根目录" python -m streamlit run app.py --server.fileWatcherType none
```

索引默认放在服务端 `/tmp/leaderboard-index`，不写入实验目录。需要持久保存时，用 `DASHBOARD_INDEX_DIR` 指定可写的**本地磁盘目录**，不要放在 NFS。系统清理 `/tmp` 后会重新索引。扫描采用 Linux 单写者锁，同一服务账号可复用索引。

源码包不含评测数据。需要试用时，点击“生成模拟演示数据”或运行 `python scripts/generate_demo.py`。生成器只写带模拟标识的目录，不覆盖真实结果。

## 根据实际报告改造的内容

诊断在 10,000 个目录上限停止，已发现的最新结果为 2,972 个、约 182 MiB。这是部分统计，不是完整目录总量；没有抽样读取，不能据此判断解析速度；容器的 `overlay` 也不能排除底层共享盘。

本版移除“超过 2,000 文件或总计 128 MiB 就整批返回空”的路径。后台子进程发现支持布局的全部 JSON，没有 10,000 目录上限，完成后原子切换 SQLite 索引。普通筛选、任务切换、选文件和翻页查本地索引，不重复遍历 NFS。刷新会重新核对文件属性，未变化内容按路径、mtime_ns、大小复用缓存。

目录枚举时识别标记，取消逐目录 9 次 exists 探测。元数据也进入索引；只读当前页存在的元数据，并对共享元数据去重。

默认每页 50 个实验 / checkpoint，可选 10 / 25 / 100。关联任务文件保持同一实验 / checkpoint 的分组，先筛选再读内容。非趋势页额外保留最多 8 个置顶基线，方便跨页对照。

单文件超过 16 MiB 单独跳过并诊断，不清空有效 peer；每页内容预算 32 MiB，用尽后单独报告未读取文件，可缩小页大小。坏 JSON、持续写入与指纹不一致同样独立报告。原始字节缓存预算 32 MiB，解析对象会占更多内存；后台最多保留 24 项请求，不宣称这是进程总内存硬限制。

## 操作反馈

- 草稿、已提交条件和成功结果分开保存。输入后显示未应用；成功后才更新生效条件。
- 首次扫描每秒显示目录数、结果数和耗时；刷新期间继续使用上次完整索引。
- 读取在后台进行，显示完成文件数、总数、耗时。取消保留草稿和旧表；失败可重试。
- 索引时间与结果读取时间分别显示；缓存命中不伪装成刚读取了源文件。
- 扫描失败或取消不发布不完整批次，不把不可达误判为全部删除。全部文件读取失败保留旧页，部分失败保留有效结果。

取消在当前 I/O 返回后生效。Linux 不可中断 NFS 调用不能保证立即退出，界面明确说明等待状态；工作线程不调用 Streamlit UI。

“任务指标”保留八任务专用控件。“基线对比”保留原始数值、差值、相对变化与用户指定的方向。“Checkpoint 趋势”先选择最多 12 个实验，再读取本页历史点；超出一页需要翻页，不插造评测点。“训练速度”继续按需读取日志，OBS 需要已有 moxing 配置。

**表内排序、过滤、CSV 只作用于当前服务端页（含额外基线）**。尚未实现跨页全局数值排序和全量后台 CSV 导出。原始 JSON 仍按需展开。

## 目录兼容

```text
根目录/实验名@60000/pnc_eval.json
根目录/实验名/eval/iter_60000/pnc_eval.json
根目录/实验名/pnc_eval.json                 # iteration 0
```

文件名沿用原任务约定，BEV 和 BEV-new 保持分开。实验或 checkpoint 的 `job_config.json`、`param.json` 可提供元数据：param 覆盖同层 job_config，checkpoint 覆盖实验层。缺少元数据不丢弃评测结果。

支持 STAR、SUN、GALAXY、FLAG、APPLE、GRAPE、SAKURA、STAR2、HIDE。默认 STAR / SUN 置顶，HIDE 始终排除。恢复跟随实验目录、checkpoint 和结果文件软链接，扫描深度固定，不递归追逐链接树。重复 `(实验, checkpoint, 文件名)` 会排除并诊断。

## 代码与验证

| 模块 | 职责 |
| --- | --- |
| dashboard/index.py | SQLite、后台扫描、批次切换、取消 |
| dashboard/loading.py | 后台分页读取、元数据去重、有界缓存 |
| dashboard/context.py | 原任务数据接口、预筛选、分页、旧结果回退 |
| dashboard/workbench.py | 八任务工作台、操作反馈 |
| dashboard/domain.py | 原有业务指标解析和控件 |
| dashboard/compat.py | AgGrid 1.0.5 异常引用兼容 |

已有测试依赖时执行 `python -m pytest -q`。回归用真实临时文件覆盖 10,010 个 checkpoint 目录、3,001 个有效 JSON、超过 128 MiB 的按页读取；还覆盖查询不重扫、取消、旧索引 / 结果回退、软链接、基线跨页、八任务视图和旧版控件兼容。临时数据不进入发行包。

这证明实现行为，不是用户 NFS 的生产测速。尚未访问真实挂载盘，首次扫描时间取决于完整目录总量及共享盘状态。已有 Whitelist 缺 schema、外部逐帧可视化模块未提供的提示保持原样；业务公式和元数据位置仍需用真实样本核对。
