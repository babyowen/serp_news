"""统一LLM客户端连接池模块"""
from openai import OpenAI


class LLMClientPool:
    """按 (api_key, base_url) 键复用 OpenAI 客户端，达到使用次数后自动回收重建"""

    def __init__(self):
        self._clients = {}  # (api_key, base_url) -> {"client": OpenAI, "uses": int}
        self._default_max_uses = 200

    def get_client(self, api_key, base_url, max_uses=None):
        """获取或创建 OpenAI 客户端

        Args:
            api_key: API密钥
            base_url: API基础URL
            max_uses: 最大使用次数（默认200）
        """
        if max_uses is None:
            max_uses = self._default_max_uses

        key = (api_key, base_url)

        if key in self._clients:
            entry = self._clients[key]
            if entry["uses"] >= max_uses:
                self._close_client(key)
            else:
                entry["uses"] += 1
                return entry["client"]

        client = OpenAI(api_key=api_key, base_url=base_url)
        self._clients[key] = {"client": client, "uses": 1}
        return client

    def _close_client(self, key):
        """安全关闭并移除指定客户端"""
        if key in self._clients:
            try:
                self._clients[key]["client"].close()
            except Exception:
                pass
            del self._clients[key]

    def close_all(self):
        """关闭所有客户端"""
        for key in list(self._clients.keys()):
            self._close_client(key)

    def get_stats(self):
        """获取连接池统计信息"""
        return {
            key: {"uses": entry["uses"]}
            for key, entry in self._clients.items()
        }


# 全局单例
_pool = LLMClientPool()


def get_pool():
    """获取全局连接池实例"""
    return _pool
