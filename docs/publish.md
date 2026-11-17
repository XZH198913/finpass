# 发布到 PyPI：可复制的步骤与已验事实

`0.0.1` 是首个可发布版本，`0.0.3` 起发布到 PyPI 正式索引。本文只记录**已经跑通过**的步骤。
§5 列了还剩的待定项：版权署名（占位中）、仓库 URL（留空）、token（需你注册）——这三项不是遗漏，是需要人拍板。

## 0. 版本号只有一处

`src/finpass/__init__.py` 的 `__version__` 是唯一事实源，`pyproject.toml` 用
`dynamic = ["version"]` + `[tool.setuptools.dynamic] version = {attr = "finpass.__version__"}`
引用它。改版本只改那一行，`finpass --version` 与包元数据不会各说一套。

## 1. 构建

```bash
rm -rf dist build src/finpass.egg-info
python -m build
python -m twine check dist/*
```

实测（2026-11-11，Python 3.13.9）：

```text
Successfully built finpass-0.0.1.tar.gz and finpass-0.0.1-py3-none-any.whl
Checking dist/finpass-0.0.1-py3-none-any.whl: PASSED
Checking dist/finpass-0.0.1.tar.gz: PASSED
```

`twine check` 校验的是 PyPI 上的 README 能不能渲染——**PASSED 是上传前唯一能本地验的元数据判据**。

## 2. 抽检 wheel（别只信「构建成功」）

wheel 里最容易漏的是 `profiles/*.yaml`：漏了照样构建成功，装到干净环境一跑就是
「找不到 profile」——而那一行错在用户看来像是自己参数写错了。

```bash
mkdir -p wheelcheck && python -m zipfile -e dist/finpass-0.0.1-py3-none-any.whl wheelcheck/
PYTHONPATH=wheelcheck python -c "import finpass; print(finpass.__file__)"
PYTHONPATH=wheelcheck python -m finpass.cli run examples/pingan_bank_daily_dirty.csv --out wheelcheck/out --auto-only
PYTHONPATH=wheelcheck python -m finpass.cli verify wheelcheck/out/pingan_bank_daily_dirty.passport.json
rm -rf wheelcheck
```

实测结果：包位置解析到解包目录，4 个 profile（`a_stock_daily` / `a_stock_valuation` /
`futures` / `fund_nav`）都在 `finpass/profiles/` 里，`run` 退出码 2、`verify` 三项判据全 OK。
`PYTHONPATH` 优先于 site-packages，所以这一次跑的是 wheel 里的代码，不是 `-e .` 装的那份。

**别只验成功路径。** 用户第一次跑，撞上的十有八九是失败路径——列名和 profile 对不上是最常见的一种。
所以抽检里必须再跑一条「故意给错的 CSV」，确认装出来的包里失败提示还在（若 `cli.py` 的
异常映射没被打进 wheel，这里会是 traceback，而 traceback 在 `-e .` 环境里是看不到的）。
CLI 的用户可见提示已双语化，但 profile 加载器的内部契约报错仍保留中文；下面这条实测输出
是「中文提示 + CSV/profile 中的原始列名」，属预期，不是漏翻：

```bash
printf 'a,b,c\n1,2,3\n' > wheelcheck/wrong-cols.csv
PYTHONPATH=wheelcheck python -m finpass.cli run wheelcheck/wrong-cols.csv --out wheelcheck/out
echo "exit=$?"
```

实测（2026-11-11）：

```text
[失败] profile a_stock_daily 缺少必需列：日期、最高价、最低价、收盘价、成交额_元
exit=1
```

一句明确提示、无 traceback、退出码 1。**注意是 1 不是 2**：2 在本项目里是 `EXIT_UNRESOLVED`
（跑完了、有未解决项，CI 当软失败放行），命令没跑起来一律 1，详见 README 的退出码表。

## 3. 上传

先上 TestPyPI，再上正式。凭据用 token（`pypi-` 开头），不要用密码：

```bash
python -m twine upload --repository testpypi dist/*
python -m pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ finpass==0.0.1
python -m twine upload dist/*
```

第二条是 TestPyPI 的坑：它的索引里没有 pandas 这类依赖，必须加 `--extra-index-url` 才能装上。

## 4. 上传后确认

```bash
pip install finpass==0.0.1
finpass --version          # finpass 0.0.1
finpass run 你自己的.csv --out out/first --auto-only
```

`finpass`（PyPI 项目名）在 2026-11-11 查询时返回 404，即该名字下没有公开项目。
**这不等于永久占用**：名字先到先得，注册完才算锁住。

## 5. 待定项

许可证已定 **Apache-2.0**：FinPass 对外的承诺是「审计级清洗、决策可追溯」，商用方关心的是
专利风险——Apache-2.0 第 3 条给了明确的专利授权与 retaliation 条款，比 MIT 更贴这个定位。
`LICENSE` 已放进仓库根目录，`pyproject` 用 SPDX 字符串（PEP 639）：

```toml
license = "Apache-2.0"
license-files = ["LICENSE"]
```

构建产物里应能看到 `License-Expression: Apache-2.0` 与 `License-File: LICENSE`，
以及 wheel 内的 `finpass-0.0.1.dist-info/licenses/LICENSE`（`twine check` 不会替你查这个，
是本节 §2 的抽检在看）。

| 事项 | 现状 | 谁来做 |
|---|---|---|
| 版权署名 | `LICENSE` 末尾写的是 `Copyright 2026 FinPass`，**占位** | 上传前换成主体名（个人或公司）。只改这一行，不影响许可证条款 |
| 项目主页 / 仓库 URL | `[project.urls]` **留空**（PyPI 允许） | 等公开仓库确定后补；补上后需发新版本才会显示在 PyPI 页面 |
| PyPI 账号与 token | 无 | 你来注册；token 属于凭据，不要写进仓库，用 `TWINE_USERNAME=__token__` + `TWINE_PASSWORD` 环境变量 |

## 6. 发布之后

版本号一旦上传就**不能覆盖**（PyPI 不允许重传同名同版本，只能 yank + 发新版本）。所以
文案、README、报错提示的改动都要在上传前落定——这也是本项目把「报错说人话」排在发布之前的理由：
发完再改一句话，就得再发一个版本。
