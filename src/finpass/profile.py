"""profile 加载与校验：域知识只来自 ``profiles/*.yaml``。

设计约束（04 定稿）：

- 会话层不认识「收盘价」「涨跌停」这类词，只认 profile 声明的**角色**（key / date_column /
  columns / missing_tokens / business_rules），因此新增一个域＝新增一个 yaml，不动引擎；
- 缺字段、重复 rule id、key 列不在数据里，一律**在 open 阶段**抛 ``ValueError``，
  错误信息直接点名缺哪一列，不留给后面的规则默默跳过。

参数来源（阶段 3 收口）只有两处，代码里不再留默认值：

- ``target_state[列]``：列级目标态（``unit_scale.factor`` / ``range`` / ``format``…），
  「这一列应该长什么样」；
- ``policy``：行级决策范式（跨行跨列、任何单一列都承载不了的约定），如
  ``duplicate_keep`` / ``default_row_action``。
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any, Iterable

import yaml

PROFILE_DIR = Path(__file__).resolve().with_name("profiles")
DEFAULT_PROFILE = "a_stock_daily"
_REQUIRED_FIELDS = ("name", "business_rules")

#: profile schema 版本：**加载器约定，不是某份 profile 的属性**。
#: 放全局常量而不是每个 yaml 顶层，避免出现「A profile 是 v2、B profile 是 v1」的分支。
#:
#: 升版规则：
#:
#: - **major +1**：破坏性——旧 profile 无法被新加载器加载（必须先跑 ``finpass profile migrate``）；
#: - **minor +1**：兼容性新增——旧 profile 仍可加载，新字段可选；
#: - **patch +1**：不影响加载的变化（注释、docstring 措辞）。
#:
#: 版本记录（**每次升版必须在这里留一行**：阶段 4 的 engine 要靠它判断
#: 「这个 profile 是哪个 schema」，才知道 ``policy`` 缺段时按什么规则处理）：
#:
#: - ``2.0.0``：``target_state`` 必填（列级目标态），缺它的旧 profile 在加载期被拒绝；
#: - ``2.1.0``：新增可选 ``policy`` 段（行级决策范式）。**声明了** ``duplicate_key`` /
#:   ``ohlc_contradiction`` 的 profile 必须在 ``policy`` 里给出对应键，否则加载期拒绝；
#:   没声明这两条规则的旧 profile 照旧加载——所以是 minor，不是 major。
#: - ``2.2.0``：列级目标态新增可选 ``role``（``high`` / ``low`` / ``close``），顶层的
#:   ``columns:`` 角色映射删除（角色只有 ``target_state`` 一处来源）。声明了
#:   ``ohlc_contradiction`` 的 profile 必须三个角色各恰好一列，否则加载期拒绝。
#: - ``2.3.0``：主键列从顶层 ``key:`` 搬进 ``target_state[*].role: key``（顶层 ``key:`` 删除，
#:   至少一列必填；复合主键按声明顺序拼接）。理由：engine 只接收 ``target_state`` /
#:   ``policy``，需要主键列却拿不到 profile 顶层字段。
#:
#: 各 profile 自己的 ``version`` 与本常量**独立演进**：``version`` 变是因为「这份 profile 的内容变了」，
#: schema 变是因为「加载器的合同变了」。护照里两个字段并存（``profile_schema_version`` / ``profile_version``），
#: 就是为了让 verify 能区分「算法没变但 schema 升级」和「算法变了」。
PROFILE_SCHEMA_VERSION = "2.3.0"

#: 引擎内置的规则类型。``business_rules`` 里出现集合外的 id 时，必须在
#: ``cross_column_rules`` 里有同名实体——禁止声明「没有目标态承载」的规则。
BUILTIN_RULE_IDS = frozenset(
    {"missing_token", "duplicate_key", "ohlc_contradiction", "unit_scale", "date_format", "range_check"}
)

#: 行级决策范式（schema 2.1.0）：规则类型 → (``policy`` 键, 允许取值)。
#: 声明了该类型就必须在 ``policy`` 里给出这个键——加载期校验，代码里不留默认值，
#: 否则「保留哪条」「缺省怎么处理」又会在 engine 与 profile 各存一份。
POLICY_REQUIREMENTS: dict[str, tuple[str, frozenset[str]]] = {
    "duplicate_key": ("duplicate_keep", frozenset({"first", "last"})),
    "ohlc_contradiction": ("default_row_action", frozenset({"answer", "skip"})),
}

#: ``default_row_action`` 的 ``answer`` **不是**「要求用户回答」，也不是「自动放行」：
#: 它只在「问题已被人工回答为修正、而 ``params["rows"]`` 没覆盖到某一行」时，决定该行
#: 按「以收盘价为准修正」执行（``answer``）还是打标保留（``skip``）。
#: 问题本身照样进队列等人工决策——这一点写在这里，避免阶段 4 的 engine 误读成产出 flag。

#: 跨列例外上限：跨列规则是例外而不是常态，超过 3 条说明该拆 profile 或改数据。
MAX_CROSS_COLUMN_RULES = 3

#: 主键角色（schema 2.3.0）：主键列从顶层 ``key:`` 搬进 ``target_state[*].role``。
#: 顶层 ``key:`` 已删除——engine 只收 ``target_state``，不该再去 profile 顶层拿结构。
KEY_ROLE = "key"

#: 列级目标态里的角色（schema 2.2.0 起）：规则靠它定位列，**不靠列名、也不靠列序**。
#: 值域封闭——``role: open`` 这类扩展属于下一次 schema 变更，不允许悄悄加。
#: ``key`` 可以出现在**多列**（复合主键），其余角色各自唯一。
COLUMN_ROLES = frozenset({"high", "low", "close", KEY_ROLE})

#: 只能出现一次的角色（``key`` 是唯一允许多值的例外）。
SINGLE_VALUED_ROLES = frozenset({"high", "low", "close"})

#: 规则类型 → 必须齐备的角色。**强制条件是「声明了依赖它的规则」**，
#: 不是「列语义是 price」：没有 OHLC 关系的价格序列不需要 role。
ROLE_REQUIREMENTS: dict[str, frozenset[str]] = {
    "ohlc_contradiction": SINGLE_VALUED_ROLES,
}


@dataclasses.dataclass(frozen=True)
class RuleSpec:
    """一条规则的声明。``type`` 缺省与 ``id`` 相同，便于 ``- missing_token`` 这种简写。"""

    id: str
    type: str
    params: dict[str, Any] = dataclasses.field(default_factory=dict)

    @classmethod
    def parse(cls, raw: Any) -> "RuleSpec":
        if isinstance(raw, str):
            return cls(id=raw, type=raw)
        if not isinstance(raw, dict):
            raise ValueError(f"business_rules 条目必须是字符串或字典，收到：{raw!r}")
        rule_id = raw.get("id")
        if not rule_id:
            raise ValueError(f"business_rules 条目缺少 id：{raw!r}")
        params = {key: value for key, value in raw.items() if key not in {"id", "type"}}
        return cls(id=str(rule_id), type=str(raw.get("type", rule_id)), params=params)

    def get(self, field: str, default: Any = None) -> Any:
        """取规则参数；``column`` / ``columns`` 这类字段统一从这里读。"""
        value = self.params.get(field, default)
        if field == "columns" and isinstance(value, str):
            return [value]
        return value

    @property
    def mode(self) -> str:
        """``auto`` = 无需人工拍板即可执行；``human`` = 必须有人决策（降级时打标保留）。"""
        return str(self.params.get("mode", "human"))

    @property
    def columns(self) -> list[str]:
        """规则涉及的列（``column`` 或 ``columns`` 两种写法都认）。"""
        cols = self.get("columns") or []
        single = self.get("column")
        if single:
            cols = [*cols, single]
        return [str(col) for col in cols]


@dataclasses.dataclass(frozen=True)
class ColumnTarget:
    """一列的目标态：这一列「**应该**长什么样」。

    只强制 ``semantic``（列语义），其余字段按需声明、原样收进 ``options``：
    ``dtype`` / ``order`` / ``unit`` / ``thousands_sep`` / ``allows_unit_suffix`` /
    ``derive_from`` / 规则开关（如 ``unit_scale: false``）。
    阶段 3 不把可选项逐个固化成属性——否则任何一个域想加一个字段都要改加载器。

    schema 2.2.0 起还承载 ``role``（``high`` / ``low`` / ``close``）：角色是「这一列在跨列
    约束里扮演谁」，属于列的目标态，不该再有一份顶层的 ``columns:`` 映射。
    """

    column: str
    semantic: str
    options: dict[str, Any] = dataclasses.field(default_factory=dict)

    def get(self, field: str, default: Any = None) -> Any:
        return self.options.get(field, default)


@dataclasses.dataclass(frozen=True)
class CrossColumnRule:
    """跨列例外：列级目标态表达不了的约束（如最高/最低/收盘三者关系）。"""

    id: str
    involves: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class Profile:
    """一份已校验的域声明。"""

    name: str
    version: str
    date_column: str
    missing_tokens: list[str]
    #: **当前未接线**：``synonyms`` 只被解析、不被任何消费点读取，列名必须与
    #: ``target_state`` 的键逐字一致（``validate_columns`` 只做逐字比对）。
    #: 保留字段是为了向后兼容旧 profile 文件，不代表「写了就生效」——
    #: 改列名的映射由数据准备脚本负责，不交给 profile。
    synonyms: dict[str, list[str]]
    units: dict[str, str]
    rules: list[RuleSpec]
    calendar: dict[str, Any]
    target_state: dict[str, ColumnTarget]
    cross_column_rules: list[CrossColumnRule]
    source: str
    #: 行级决策范式（阶段 3 新增，可选段）：``duplicate_keep`` / ``default_row_action``。
    #: 这里**不给默认值**——缺键由会话层在 open 阶段明确拒绝，避免代码与 profile 各留一份。
    policy: dict[str, Any] = dataclasses.field(default_factory=dict)
    schema_version: str = PROFILE_SCHEMA_VERSION

    def target(self, column: str) -> ColumnTarget | None:
        """取某列的目标态；未声明返回 None（调用方决定是「不检查」还是「报错」）。"""
        return self.target_state.get(column)

    @property
    def key_columns(self) -> list[str]:
        """主键列（1..n），顺序 = ``target_state`` 里的声明顺序。

        注意：这是全仓库**唯一**「声明顺序即语义」的地方——复合主键拼出来的
        ``row_key`` 是 ``a|b``，顺序变了字符串就变。所以这里不用 set，
        也不能像 ``involves`` 那样把顺序当无关紧要。
        """
        return [column for column, target in self.target_state.items() if target.get("role") == KEY_ROLE]

    @property
    def key_label(self) -> str:
        """复合主键的显示名，如 ``合约代码+日期``。"""
        return "+".join(self.key_columns)

    @property
    def rule_ids(self) -> list[str]:
        return [rule.id for rule in self.rules]

    def role_columns(self) -> dict[str, str]:
        """单值角色 → 实际列名（``high`` / ``low`` / ``close``）。

        唯一来源是 ``target_state[*].role``（2.2.0 起没有 ``columns:``）；
        ``key`` 不在这里——它可以是多列，见 :attr:`key_columns`。
        """
        roles: dict[str, str] = {}
        for column, target in self.target_state.items():
            role = target.get("role")
            if role is not None and str(role) != KEY_ROLE:
                roles[str(role)] = column
        return roles

    def required_columns(self) -> list[str]:
        """打开数据前就必须存在的列：主键 + 日期列 + 声明了 role 的列 + 规则引用的列。"""
        required: list[str] = [*self.key_columns, self.date_column, *self.role_columns().values()]
        for rule in self.rules:
            required.extend(rule.columns)
        seen: dict[str, None] = {}
        for col in required:
            seen.setdefault(col, None)
        return list(seen)


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        payload = yaml.safe_load(fh) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"profile 必须是字典结构：{path}")
    return payload


def _resolve(spec: str | Path | dict[str, Any] | Profile | None) -> tuple[dict[str, Any], str]:
    """把「profile 名 / yaml 路径 / 字典 / 已加载对象」统一成 (原始字典, 来源)。"""
    if isinstance(spec, Profile):
        return dataclasses.asdict(spec), spec.source
    if isinstance(spec, dict):
        return dict(spec), "<dict>"
    if spec is None:
        spec = DEFAULT_PROFILE

    candidate = Path(spec)
    if candidate.is_file():
        return _read_yaml(candidate), str(candidate)
    path = PROFILE_DIR / f"{spec}.yaml"
    if path.is_file():
        return _read_yaml(path), str(path)
    raise ValueError(f"找不到 profile：{spec}（已查找 {path}）")


def _parse_target_state(raw: Any, source: str) -> dict[str, ColumnTarget]:
    """解析列级目标态。``semantic`` 必填，其余字段原样收进 ``options``。"""
    if not isinstance(raw, dict):
        raise ValueError(f"target_state 必须是「列名 → 目标态」的字典（{source}）")
    targets: dict[str, ColumnTarget] = {}
    for column, spec in raw.items():
        # 反方向的「保留键约定」（4b）：engine 的 target_state 里 ``__`` 开头的是元信息键，
        # 用户列若同名会覆盖会话层生成的 ``__rules__``，行为不可预测，所以在加载期就拒绝。
        if str(column).startswith("__"):
            raise ValueError(
                f'target_state 的列名不能以 "__" 开头（保留给 engine 的元信息键，如 __rules__）：'
                f"{column}（{source}）"
            )
        if not isinstance(spec, dict):
            raise ValueError(f"target_state[{column}] 必须是字典（{source}）")
        semantic = str(spec.get("semantic") or "").strip()
        if not semantic:
            raise ValueError(f"target_state[{column}] 缺少 semantic（{source}）")
        options = {key: value for key, value in spec.items() if key != "semantic"}
        targets[str(column)] = ColumnTarget(column=str(column), semantic=semantic, options=options)
    return targets


def _parse_cross_column_rules(
    raw: Any, source: str, target_columns: set[str]
) -> list[CrossColumnRule]:
    """解析跨列例外：上限 3 条、id 唯一、``involves`` 至少两列且必须已在 target_state 里。"""
    items = raw or []
    if not isinstance(items, list):
        raise ValueError(f"cross_column_rules 必须是列表（{source}）")
    if len(items) > MAX_CROSS_COLUMN_RULES:
        raise ValueError(
            f"cross_column_rules 最多 {MAX_CROSS_COLUMN_RULES} 条，实际 {len(items)}："
            f"跨列是例外不是常态，超过就该拆 profile 或改数据（{source}）"
        )
    rules: list[CrossColumnRule] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError(f"cross_column_rules 条目必须是字典（{source}）")
        rule_id = str(item.get("id") or "").strip()
        if not rule_id:
            raise ValueError(f"cross_column_rules 条目缺少 id（{source}）")
        if rule_id in seen:
            raise ValueError(f"cross_column_rules 有重复 id：{rule_id}（{source}）")
        involves_raw = item.get("involves") or []
        if not isinstance(involves_raw, list) or len(involves_raw) < 2:
            raise ValueError(f"cross_column_rules 的 {rule_id} 至少要 involves 两列（{source}）")
        involves = tuple(str(col) for col in involves_raw)
        unknown = [col for col in involves if col not in target_columns]
        if unknown:
            raise ValueError(
                f"cross_column_rules 的 {rule_id} 引用了 target_state 里没有的列："
                f"{'、'.join(unknown)}（{source}）"
            )
        seen.add(rule_id)
        rules.append(CrossColumnRule(id=rule_id, involves=involves))
    return rules


def _validate_policy(raw_policy: dict[str, Any], rules: list[RuleSpec], source: str) -> None:
    """声明了规则就必须给出它的行级范式；缺键 / 值非法都在**加载期**拒绝。

    放在加载期而不是会话层的理由：这个要求只依赖 ``business_rules`` 的 type，
    不需要看数据。留在会话层就等于「同一个合同有两个校验点」，阶段 4 的 engine
    又会多一份判断。
    """
    for rule in rules:
        requirement = POLICY_REQUIREMENTS.get(rule.type)
        if requirement is None:
            continue
        key, allowed = requirement
        choices = "/".join(sorted(allowed))
        value = raw_policy.get(key)
        if value is None:
            raise ValueError(
                f"profile 声明了 {rule.id}（type={rule.type}），但 policy.{key} 缺失"
                f"（可选：{choices}）（{source}）"
            )
        if str(value) not in allowed:
            raise ValueError(f"policy.{key} 非法：{value!r}（可选：{choices}）（{source}）")


def _validate_roles(target_state: dict[str, ColumnTarget], rules: list[RuleSpec], source: str) -> None:
    """角色校验：值域封闭、单值角色唯一、主键必有一条、规则需要的角色齐备。

    与 ``_validate_policy`` 同构：强制条件来自 ``business_rules`` 的 type，不需要看数据。
    **唯一性双向**：既拒绝「缺某个角色」，也拒绝「两个列都声明同一个角色」——
    后者会让 ``role → 列`` 的映射取决于 yaml 书写顺序，等于把语义交给顺序。
    例外是 ``key``：复合主键本就有多列，它的顺序是有语义的（见 ``key_columns``）。
    """
    by_role: dict[str, list[str]] = {}
    for column, target in target_state.items():
        role = target.get("role")
        if role is None:
            continue
        if str(role) not in COLUMN_ROLES:
            raise ValueError(
                f"target_state[{column}].role 非法：{role!r}"
                f"（只认 {'/'.join(sorted(COLUMN_ROLES))}）（{source}）"
            )
        by_role.setdefault(str(role), []).append(column)

    # ``key`` 是唯一允许多值的角色（复合主键）；其余角色各自唯一，
    # 否则「role → 列」的映射会取决于 yaml 书写顺序。
    duplicated = {
        role: columns
        for role, columns in by_role.items()
        if role in SINGLE_VALUED_ROLES and len(columns) > 1
    }
    if duplicated:
        role, columns = sorted(duplicated.items())[0]
        raise ValueError(
            f"target_state 里 role 重复：{role} 同时声明在 {'、'.join(columns)}（{source}）"
        )

    if not by_role.get(KEY_ROLE):
        raise ValueError(
            "profile 的 target_state 没有任何 role: key"
            f"（schema 2.3.0 起顶层 key: 段已删除，主键列必须用 role: key 标记）（{source}）"
        )

    for rule in rules:
        required = ROLE_REQUIREMENTS.get(rule.type)
        if required is None:
            continue
        missing = sorted(required - set(by_role))
        if missing:
            raise ValueError(
                f"profile 声明了 {rule.id}（type={rule.type}），但 target_state 缺少 role："
                f"{'、'.join(missing)}（需要 {'/'.join(sorted(required))} 各一列）（{source}）"
            )


def load_profile(spec: str | Path | dict[str, Any] | Profile | None = None) -> Profile:
    """加载并校验 profile，任何结构问题都在这里抛 ``ValueError``。"""
    raw, source = _resolve(spec)

    # 占位 profile（如 ``fund_nav``）：**明说自己是占位**，而不是让调用方从
    # 「缺少必需字段：name、business_rules」去猜——那条信息看起来像配置写错了，
    # 用户会去补字段，补完照样跑不通（缺的是域语义，不是三行 yaml）。
    if raw.get("placeholder"):
        note = str(raw.get("note") or "该 profile 是占位文件，尚未迁移到当前 schema")
        raise ValueError(f"{note}（{source}）")

    missing = [field for field in _REQUIRED_FIELDS if not raw.get(field)]
    if missing:
        raise ValueError(f"profile 缺少必需字段：{'、'.join(missing)}（{source}）")

    # schema 2.3.0：顶层 key 段已删除。留着就静默失效（两份主键来源），所以明确拒绝。
    if raw.get("key"):
        raise ValueError(
            "profile 的 key 段已删除（schema 2.3.0）：主键列改写到 target_state[列].role: key"
            f"（复合主键按声明顺序拼接），不允许保留两份主键来源（{source}）"
        )

    rules: list[RuleSpec] = []
    for item in raw["business_rules"]:
        rules.append(RuleSpec.parse(item))
    duplicate_ids = {rule_id for rule_id in (rule.id for rule in rules) if [r.id for r in rules].count(rule_id) > 1}
    if duplicate_ids:
        raise ValueError(f"profile 的 business_rules 有重复 id：{'、'.join(sorted(duplicate_ids))}")

    for rule in rules:
        if rule.mode not in {"auto", "human"}:
            raise ValueError(f"profile 的规则 {rule.id} 的 mode 非法：{rule.mode}（只认 auto / human）")

    # schema 2.0.0：target_state 必填。旧 profile 在这里被明确拒绝，而不是拖到规则执行才炸。
    if not raw.get("target_state"):
        raise ValueError(
            f"profile 缺少必需字段：target_state"
            f"（schema 2.0.0 起必填；旧 profile 请先跑 `finpass profile migrate`）（{source}）"
        )
    target_state = _parse_target_state(raw["target_state"], source)
    cross_column_rules = _parse_cross_column_rules(
        raw.get("cross_column_rules"), source, set(target_state)
    )

    cross_ids = {rule.id for rule in cross_column_rules}
    for rule in rules:
        # 看 type 而不是 id：``id: unit_ambiguity, type: unit_scale`` 是允许的写法
        # （一条内置规则可以按域起不同名字），只有 type 与 id 都落空才是「没有承载」。
        if rule.type in BUILTIN_RULE_IDS or rule.id in cross_ids:
            continue
        raise ValueError(
            f"business_rules 的 {rule.id}（type={rule.type}）既不是内置规则，也不在 cross_column_rules 里："
            f"禁止声明没有目标态承载的规则（{source}）"
        )

    # schema 2.2.0：顶层的 columns 角色映射已删除。留着就静默失效，所以明确拒绝并指向新位置。
    if raw.get("columns"):
        raise ValueError(
            "profile 的 columns 段已删除（schema 2.2.0）：角色改写到 target_state[列].role"
            f"（如 role: high），不允许保留两份角色来源（{source}）"
        )
    _validate_roles(target_state, rules, source)
    key_columns = [column for column, target in target_state.items() if target.get("role") == KEY_ROLE]

    # date_column 不再回落到字面量「日期」（那是域词泄漏到加载器）：
    # 单主键时默认取主键列，复合主键时**必须显式声明**——否则「哪个日期」只能靠猜。
    if raw.get("date_column"):
        date_column = str(raw["date_column"])
    elif len(key_columns) == 1:
        date_column = key_columns[0]
    else:
        raise ValueError(
            "profile 有复合主键时必须显式声明 date_column"
            f"（不再回落到字面量『日期』；当前主键：{'、'.join(key_columns)}）（{source}）"
        )

    policy_raw = raw.get("policy") or {}
    if not isinstance(policy_raw, dict):
        raise ValueError(f"policy 必须是字典（{source}）")
    _validate_policy(policy_raw, rules, source)
    return Profile(
        name=str(raw["name"]),
        version=str(raw.get("version", "0.1.0")),
        date_column=date_column,
        missing_tokens=[str(token) for token in (raw.get("missing_tokens") or [])],
        # 只解析、不消费：仓库内 profile 已删掉该段，保留解析仅为兼容旧文件。
        synonyms={str(col): [str(alias) for alias in aliases] for col, aliases in (raw.get("synonyms") or {}).items()},
        units={str(col): str(unit) for col, unit in (raw.get("units") or {}).items()},
        rules=rules,
        calendar=dict(raw.get("calendar") or {}),
        target_state=target_state,
        cross_column_rules=cross_column_rules,
        source=source,
        policy={str(key): value for key, value in policy_raw.items()},
    )


def validate_columns(columns: Iterable[str], profile: Profile) -> None:
    """缺必需列时立刻拒绝：``ValueError: profile futures_daily 缺少必需列：合约代码``。"""
    present = {str(col) for col in columns}
    absent = [col for col in profile.required_columns() if col not in present]
    if absent:
        raise ValueError(f"profile {profile.name} 缺少必需列：{'、'.join(absent)}")
