"""测试 minio_client.remove_prefix - 真删红线核心逻辑。

不依赖真实 MinIO 服务,用 monkeypatch 替换 get_minio() 返回的 client。

覆盖场景:
1. 小批量(<1000 对象)→ 1 次 remove_objects 调用,全部成功
2. 大批量(2500 对象)→ 3 次批量调用,全部成功 — 验证分批切片正确
3. 部分失败 → 抛 RuntimeError,失败 key 出现在 error message 中
4. 空 prefix → 返回 0,不调用 remove_objects
"""
from __future__ import annotations

from typing import List
from unittest.mock import MagicMock

import pytest


class _FakeObj:
    """模拟 minio.list_objects 返回的对象 — 只用到 object_name。"""

    def __init__(self, name: str):
        self.object_name = name


class _FakeDelErr:
    def __init__(self, key: str):
        self.object_name = key
        self.code = "AccessDenied"
        self.message = "fake forbidden"


def _make_fake_client(
    objects: List[str], fail_keys: List[str] | None = None
):
    """生成 minio.Minio mock — list_objects 返指定 keys,remove_objects 按 fail_keys 选择性失败。"""
    fail_keys = set(fail_keys or [])
    client = MagicMock()
    # list_objects → 迭代器
    client.list_objects.return_value = iter([_FakeObj(o) for o in objects])
    # remove_objects(bucket, [DeleteObject]) → 迭代器of 失败项
    captured_batches: list[list] = []

    def _remove_objects(_bucket, batch):
        captured_batches.append(list(batch))
        return iter(
            _FakeDelErr(d.name if hasattr(d, "name") else getattr(d, "_name", None) or str(d))
            for d in captured_batches[-1]
            if (d.name if hasattr(d, "name") else getattr(d, "_name", None) or str(d)) in fail_keys
        )

    client.remove_objects.side_effect = _remove_objects
    client.__captured_batches__ = captured_batches  # for tests to inspect
    return client


@pytest.fixture
def patched_minio(monkeypatch):
    """提供一个 fake client,默认无失败。"""
    from services import minio_client as mc

    holder = {}

    def _install(objects: List[str], fail_keys: List[str] | None = None):
        client = _make_fake_client(objects, fail_keys)
        monkeypatch.setattr(mc, "get_minio", lambda: client)
        holder["client"] = client
        return client

    holder["install"] = _install
    return holder


def test_remove_prefix_small_batch(patched_minio):
    from services.minio_client import remove_prefix

    client = patched_minio["install"](
        [f"sessions/abc/records/{i}.jpg" for i in range(7)]
    )
    n = remove_prefix("sessions/abc/")
    assert n == 7
    assert client.remove_objects.call_count == 1
    # batch 内应是 7 个 DeleteObject
    assert len(client.__captured_batches__[0]) == 7


def test_remove_prefix_pagination(patched_minio):
    """2500 对象必须分 3 批 (1000+1000+500),不能漏。"""
    from services.minio_client import remove_prefix

    objs = [f"sessions/big/voice/chunk_{i:04d}.mp3" for i in range(2500)]
    client = patched_minio["install"](objs)
    n = remove_prefix("sessions/big/")
    assert n == 2500
    assert client.remove_objects.call_count == 3
    batches = client.__captured_batches__
    assert [len(b) for b in batches] == [1000, 1000, 500]


def test_remove_prefix_empty(patched_minio):
    from services.minio_client import remove_prefix

    client = patched_minio["install"]([])
    n = remove_prefix("sessions/nope/")
    assert n == 0
    # 空批不应触发 remove_objects 调用
    assert client.remove_objects.call_count == 0


def test_remove_prefix_partial_failure_raises(patched_minio):
    """任一对象删失败 → RuntimeError,counts + samples 在 error message 里。"""
    from services.minio_client import remove_prefix

    objs = [f"sessions/x/rec/{i}.bin" for i in range(5)]
    patched_minio["install"](objs, fail_keys=[objs[2], objs[4]])
    with pytest.raises(RuntimeError) as ei:
        remove_prefix("sessions/x/")
    msg = str(ei.value)
    assert "2 个对象未能删除" in msg
    # 至少有一个失败 key 出现在 message 中
    assert any(k in msg for k in objs[2:5])
