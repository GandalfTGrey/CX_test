# 环境兼容

实测 Python 3.8.18、Streamlit 1.40.1、streamlit-aggrid 1.0.5、pandas 2.0.3、NumPy 1.21.6。无需升级现有环境，requirements-tested.txt 仅供缺包和复现实验时参考。

2026-11-10：64 项自动检查通过。浏览器检查八任务入口、AgGrid 真实行、筛选反馈、基线保留和空状态。具体环境与规模 fixture 记录见 verification.json。

控件宽度使用 use_container_width，字体为 sans serif，关闭文件监视。AgGrid 兼容层在项目中，不编辑 site-packages，保留真实错误及 Streamlit Stop/Rerun 信号。

服务端目标是用户现有 Linux 环境；单写者锁使用 fcntl。Windows 浏览器可访问面板，不将 Windows 本机作为已验证后端。

自动和浏览器检查使用合成数据，不代表生产 NFS 时延。扫描失败保留完整索引，单文件失败隔离，全部失败保留旧结果。
