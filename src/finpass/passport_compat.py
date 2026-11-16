"""旧产物兼容与保留策略的单一事实源（阶段 0 登记，消费方在 7a / 7b）。

阶段 0 只登记、不接线：本模块不 import CLI / verify，也不改变它们的现有行为。
这样「表可被测试解析」不需要先把 7a 的实现做出来。

模块边界：本文件只回答「旧产物缺什么、缺了怎么办」与「保留策略的数值上限」。
**``passport_id`` / ``dry_run`` 是 passport 字段，不是兼容常量**，阶段 1 加它们时不要写进本模块——
「字段在哪定义」与「字段怎么兼容」是两个关注点，混在一起会让兼容规则反过来规定产物结构。

两张表刻意分开，而不是合并成 ``COMPAT_RULES``：

- ``FIELD_COMPAT`` 是「字段 → 三值」，值域有限、可枚举，测试能断言集合封闭；
- ``SCENARIO_COMPAT`` 是「场景 → 识别行为」，值指向别处的机制（扩展点 id / 退出码口径），
  不该被枚举断言。

合成一个字典会出现「一种值类型、两种语义」，断言只能退化成「值是 str」，等于没断言。

引用关系（避免重复表述导致漂移）：

- ``events.phase`` 的默认值唯一来源是 ``PHASE_DEFAULT``（4b 起它的**值**别名自
  ``engine.PHASE_INITIAL``，全包只剩一处字面量）；spec 判据 2 只引用编号，不复述字面量。
- ``skip`` 的语义 = **不改写字段，下游按自身默认值处理缺失**。对 ``events.phase`` 而言：
  读端看到缺 ``phase`` 时按 ``PHASE_DEFAULT`` 视为 ``initial``；**写入端不主动补** ``initial``。
  写入端不动旧产物（对审计无损），读端补（下游语义统一），两者不可混为一谈。
- ``passport_id`` 的 ``error`` **只在被当作 patch 父代时生效**，其余场景按「字段缺失」处理；
  这条语义属于规则而非字典值，见 spec 判据 8，不塞进 ``FIELD_COMPAT``。
- ``KEEP_CHAIN_*`` 的数值语义唯一来源是 spec 判据 9，``validate_keep_chain`` 是唯一校验入口。
  ``validate_keep_chain`` 在阶段 0 是**纯登记、无调用方**；接线是 7a 的交付物，
  在 7a 之前看到它没人调用属正常，**不许顺手接上**（提前接会绕过 7a 对「到上限后如何继续」的设计）。
"""

from __future__ import annotations

from .engine import PHASE_INITIAL

#: 事件 ``phase`` 缺失时的默认阶段名。**值是别名**：阶段名的唯一来源在 ``engine.PHASE_INITIAL``
#: （4b 实现阶段闸门时把字面量收进了引擎），这里只导出护照读取端用的名字——
#: 保证全包只有一处字面量，``test_phase_default.py`` 钉住这一点。
PHASE_DEFAULT = PHASE_INITIAL

#: 旧护照缺字段时的处理方式：``warn`` / ``skip`` / ``error``（值域封闭，三值）。
#: ``events.phase`` 取 ``skip``：写入端不改旧产物，读端按 ``PHASE_DEFAULT`` 补语义。
FIELD_COMPAT: dict[str, str] = {
    "output_file_sha256": "warn",
    "profile_schema_version": "warn",
    "passport_id": "error",
    "events.phase": "skip",
}

#: 场景 → 该场景下的识别行为。值不做枚举断言，只断言「键存在、值非空」。
SCENARIO_COMPAT: dict[str, str] = {
    "dry_run": "ext:dry_run",
    "auto_only": "exit_code_behavior",
}

#: ``--keep-chain`` 的代数上限：含 run 在内的**总代数**（5 = run + 4 个 patch）。
KEEP_CHAIN_DEFAULT = 5
KEEP_CHAIN_MIN = 2
KEEP_CHAIN_MAX = 100


def validate_keep_chain(n: int) -> int:
    """校验 ``--keep-chain`` 的取值，越界抛 ``ValueError``（消息可直接展示给用户）。

    阶段 0 只提供校验入口，CLI 接线在 7a；因此这里不依赖任何 CLI 参数对象。
    """
    if not isinstance(n, int) or isinstance(n, bool):
        raise ValueError(f"--keep-chain 必须是整数，收到 {n!r}")
    if n < KEEP_CHAIN_MIN:
        raise ValueError(f"--keep-chain 至少 {KEEP_CHAIN_MIN}（run + 1 patch），收到 {n}")
    if n > KEEP_CHAIN_MAX:
        raise ValueError(
            f"--keep-chain 至多 {KEEP_CHAIN_MAX}（产物目录无界增长违反可审计性），收到 {n}"
        )
    return n
