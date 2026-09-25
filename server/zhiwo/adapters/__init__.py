"""Mnemosyne Adapter。业务代码不直接访问 Kernel 表，已批准的清理例外除外。

正式连接走 kernel_client。memory_search 只保留 P0 实验映射，正式服务不调用它。
"""
