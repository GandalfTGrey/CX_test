# 环境兼容

目标环境不变：Python 3.8.18、Streamlit 1.40.1、streamlit-aggrid 1.0.5、Pandas 2.0.3、NumPy 1.21.6。

控件使用 Streamlit 1.40 支持的容器、radio、popover 和 use_container_width；字体为 sans serif，文件监视关闭。AgGrid 异常兼容层仍在项目中，不修改 site-packages。

后台索引、内容读取、缓存和业务数学计算沿用已验证版本。窄屏表格解除实验列冻结，允许表格自身横向滚动。CSV 的编码仍为 UTF-8 BOM，覆盖当前服务端页和参考基线。
