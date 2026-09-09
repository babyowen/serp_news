# 提示词与运行配置（Issue #18）

提示词、关键词、专用评分映射、黑名单、规则及模型调用参数保存在部署目录之外的 SQLite 文件中。`config.py` 只提供兼容读取入口；`config_defaults.json` 只用于显式初始化，不参与运行时合并或错误兜底。

选择 SQLite 是为了让新版本写入和生效指针切换在同一事务中完成，并处理多进程并发。没有引入外部配置服务，不涉及业务 MySQL 数据或腾讯云数据库备份。

## 首次生产升级：先迁移旧文件，再替换代码

不要先更新生产目录里的 `config.py`，再从新代码的默认值初始化生产配置。迁移源必须是更新前的生产文件；已有本地快照也必须在上线前与服务器最新内容重新核对。

1. 暂停人工调整关键词/提示词，保留生产项目原件。确认正在运行的服务是否已加载磁盘最新配置；本地文件相同不能证明常驻进程内存相同。
2. 将本版本代码放在一个**单独的预发布目录**，使用其中的迁移工具读取旧生产目录。迁移工具只需要 Python 3.10+ 标准库，不会导入旧代码、加载旧 `.env` 或连接数据库。
3. 选择所有部署目录之外的稳定位置，例如 `/srv/serp-news-state/runtime.sqlite3`。运行 Web 和定时任务的账户应有该文件及所在目录的读写权限；SQLite 恢复中断写入也需要写权限。
4. 先预览，再初始化。下列目录是示例，请替换为服务器实际路径：

```bash
# 使用新工具，读取尚未覆盖的旧生产项目
python3 /srv/serp-news-next/config_cli.py --store /srv/serp-news-state/runtime.sqlite3 init --source /srv/serp-news-current --dry-run
python3 /srv/serp-news-next/config_cli.py --store /srv/serp-news-state/runtime.sqlite3 init --source /srv/serp-news-current --note '首次迁移生产文件配置'
python3 /srv/serp-news-next/config_cli.py --store /srv/serp-news-state/runtime.sqlite3 status
```

迁移会原样保存 22 段现有/归档提示词、关键词及顺序、映射和模型参数，并将原始 `config.py`、抓取规则及三份模型调用源码的字节和 SHA-256 保存到配置存储。发现额外配置字段、动态赋值、不支持的连接来源或模板时会拒绝迁移，不会猜测值或静默遗漏。

三份模型调用源码须符合已审核的调用结构。允许修改注释、文档字符串，以及已支持的模型名称和请求参数常量；额外覆盖提示词/接口、动态选择模型、内联修改消息或其他代码结构变化都会拒绝迁移。遇到拒绝时，应保留生产原件，核对实际生效逻辑并完善迁移适配器；不能删除生产调整或直接刷新源码结构指纹来让检查通过。

相同来源重复初始化是幂等操作，即使之后已编辑过配置，也不会切回首次版本。目标已存在但来源不同、目标损坏或为空文件时都会报错，不覆盖文件。`--dry-run` 不创建目标目录或存储。

5. 导出并校验，然后在生产 `.env` 或服务管理器中增加：

```ini
SERP_CONFIG_STORE=/srv/serp-news-state/runtime.sqlite3
# 多个 Web worker 必须共享稳定的会话密钥；使用独立生成的随机值
FLASK_SECRET_KEY=replace_with_a_random_private_value
```

`FLASK_SECRET_KEY` 未设置或为空时会记录启动告警并使用临时密钥，重启后旧会话/表单失效；独立加载的多个 worker 还会相互无法验证会话。生产必须配置同一个稳定的随机密钥，不要使用示例占位值。

```bash
python3 /srv/serp-news-next/config_cli.py --store /srv/serp-news-state/runtime.sqlite3 export --output /srv/serp-news-state/before-upgrade.json
python3 /srv/serp-news-next/config_cli.py --store /srv/serp-news-state/runtime.sqlite3 backup --output /srv/serp-news-state/before-upgrade.sqlite3
```

6. 确认原文校验、配置版本和恢复文件后，再部署新代码并重启服务。部署期间如生产文件又有调整，先重新获取最新来源并预览差异。

## 本地开发初始化

开发使用独立文件，不能指向生产配置或把配置存储放进仓库：

```bash
export SERP_CONFIG_STORE="$HOME/.local/share/serp-news-dev/runtime.sqlite3"
python3 config_cli.py --store "$SERP_CONFIG_STORE" init --defaults
```

将该绝对路径写入本地 `.env`，供以后从 IDE、定时任务或其他工作目录启动时使用。业务入口只读取项目根目录的 `.env`，已有进程环境变量优先；CLI 为保持迁移过程无副作用，不自动加载 `.env`，请传 `--store` 或在 shell 中设置 `SERP_CONFIG_STORE`。

## 查看、编辑、比较和恢复

- `/admin/keywords` 保存为新版本。每张表单带读取时的版本号，过期提交返回 409；格式错误返回 400。编辑不再重写 Python 文件。
- 关键词重命名保留原位置、专用提示词绑定及规则评分；删除主题保留其专用绑定和规则，重新添加时仍可使用已调好的配置。重命名目标若已有专用绑定或保留的评分规则，会拒绝保存，避免将不同主题的规则合并。至少保留一个主关键词。
- `/admin/models` 展示生效版本、模型信息、现用及归档提示词。
- `/admin/config-history` 展示最近 100 个版本，支持差异预览、导出及恢复。恢复会创建新版本，后续历史不删除。
- 管理请求开始时固定一个快照；后续请求读取最新版本。密钥等 `.env` 变更仍需要按原部署方式重启服务。
- 所有管理写操作（包括启动、重评和标签合并）都需认证及同一会话的 CSRF 令牌；管理响应统一禁止缓存。版本参数格式错误/跨存储返回 400，版本不存在返回 404，真实存储故障继续返回 503。

首次不提供富文本提示词编辑器，可通过配置 JSON 显式导入：

```bash
python3 config_cli.py --store "$SERP_CONFIG_STORE" status
python3 config_cli.py --store "$SERP_CONFIG_STORE" export --output /tmp/config-before.json
python3 config_cli.py --store "$SERP_CONFIG_STORE" export --editable --output /tmp/config-candidate.json
# 保留 config-before.json 原件，编辑 config-candidate.json；记录导出时显示的 source_version。
python3 config_cli.py --store "$SERP_CONFIG_STORE" diff --file /tmp/config-candidate.json
python3 config_cli.py --store "$SERP_CONFIG_STORE" import --file /tmp/config-candidate.json --expected-version 'STORE_UUID:REVISION' --note '调整说明' --dry-run
python3 config_cli.py --store "$SERP_CONFIG_STORE" import --file /tmp/config-candidate.json --expected-version 'STORE_UUID:REVISION' --note '调整说明'
```

现用模型请求参数在 `models.scoring`、`models.item_summarizer`、`models.region`；`settings.DEEPSEEK_MODEL` 等字段只为旧导入兼容保留，不决定这三个调用的实际模型。通用摘要未指定 temperature，迁移不会自行补值。

`STORE_UUID:REVISION` 必须替换为编辑前记录的完整版本；若期间有其他保存，应重新比较后提交。跨环境版本号不能混用。字段、模板输入协议或未知配置项不兼容时会报错，不自动补默认值或删除未知项。新功能需要新配置时，应创建候选版本并显式启用。

提示词 ID 不能使用配置读取入口的保留名称，包括已有设置名、`DEFAULT_KEYWORDS` 等派生字段，以及 `API_KEY`、`DEEPSEEK_API_KEY` 等凭据别名；现用和归档提示词都受此限制。其他合法的新提示词 ID 可以正常保存。

`import` 和 `restore` 的 `--dry-run` 也会检查预期版本，过期时返回失败；正式保存仍在事务内再次检查，以处理预览之后发生的并发修改。

```bash
python3 config_cli.py --store "$SERP_CONFIG_STORE" history
python3 config_cli.py --store "$SERP_CONFIG_STORE" restore --version 'STORE_UUID:OLD_REVISION' --expected-version 'STORE_UUID:CURRENT_REVISION' --note '恢复说明' --dry-run
python3 config_cli.py --store "$SERP_CONFIG_STORE" restore --version 'STORE_UUID:OLD_REVISION' --expected-version 'STORE_UUID:CURRENT_REVISION' --note '恢复说明'
```

## 批次与续跑

`main.py` 在开始时将输出目录和主关键词绑定到一个完整配置版本，传给所有子进程，并在批次日志记录版本和 SHA-256。同日期、同输出目录的续跑保持原版本；新日期的任务使用当前生效版本。独立评分和单条摘要脚本也检查批次绑定。地域/业务类型历史补标脚本在进程启动时固定配置，可通过 `SERP_CONFIG_REVISION` 显式指定历史版本。

单条摘要的数据库查询仅处理本次成功绑定的主关键词。未传 `--keyword` 时，也不会处理数据库中其他已删除或未绑定主题的历史记录；续跑使用原绑定版本的关键词集合。需要处理已删除主题时，应明确指定包含该主题的历史配置版本及 `--keyword`，并核对已有输出的批次绑定。

`SERP_CONFIG_REVISION` 是可选的**任务级**变量，不应固定写进生产 `.env` 或长期服务配置，否则新任务也会锁定旧版本。

已有新闻 JSON 但没有版本绑定时会报错。确认这些输出与所选配置一致后，可显式承接：

```bash
python main.py YYYY-MM-DD --keyword 养老 --adopt-existing-config
python news_scorer.py 养老 YYYY-MM-DD --adopt-existing-config
```

不要用该开关绕过未知来源的历史数据。已绑定的批次不能通过此开关换版本；同日期不同主题如已绑定不同版本，应分别按 `--keyword` 续跑。后台重评也沿用已有绑定。

例如，同一输出目录中主题 A 已绑定 v3，之后用 v5 补跑主题 B，就会形成混版本绑定。此后该目录的全量续跑和后台批量重评均会拒绝，必须按主题分别运行。恢复当前生效配置也不会改写这些绑定。补跑已删除主题或新增主题前，应先决定是否接受逐主题续跑；希望仍能全量重跑时，使用独立输出目录。

后台重评会先检查所选版本中确有 `_scored.json` 文件的主题，只处理这些主题；日期不存在、目录为空或只有未评分文件时会提示并退出，不创建绑定。若评分文件缺少历史绑定，后台不会自动承接。核对文件与历史版本一致后，从项目根目录按主题执行下面的命令；将版本、主题和日期替换为已核对的值：

```bash
SERP_CONFIG_REVISION='STORE_UUID:REVISION' python news_scorer.py 养老 YYYY-MM-DD --rescore --adopt-existing-config
```

该命令会绑定并重评该主题。所有主题绑定版本一致时，随后可通过后台重评将评分结果更新到数据库；混版本目录则按主题复用后台的缺失评分更新函数，并沿用同一版本。普通 `write_to_mysql.py` 入库命令会跳过已存在记录，不能代替评分更新：

```bash
SERP_CONFIG_REVISION='STORE_UUID:REVISION' python - 养老 YYYY-MM-DD <<'PY'
import sys
from pathlib import Path
from batch_config import prepare_batch
keyword, date = sys.argv[1:]
prepare_batch(date, keyword)
import write_to_mysql as writer
try:
    path = Path('output') / date / f'{date}_{keyword}_scored.json'
    writer.update_scores_from_json(str(path), keyword)
finally:
    writer.cursor.close()
    writer.conn.close()
PY
```

`fetch_and_filter.py`、`fetch_content.py`、`news_fetcher.py`、`write_to_mysql.py` 在主流水线内继承固定版本，但独立执行时不会自行查找输出目录的批次绑定。直接运行可能按当前配置遗漏已删除主题的旧文件；独立处理历史数据必须核对绑定并显式传入对应的 `SERP_CONFIG_REVISION`，对有关键词参数的入口指定主题。统一这些独立入口的绑定行为留待后续改进，不能把独立运行当作自动续跑。

需要对同一日期使用新配置重新分析时，使用**独立且为空的输出工作目录**和明确的配置版本，不在旧输出上隐式混用结果。采集/评分等流水线脚本使用相对输出路径，常规运行仍应从项目根目录启动；如需另一工作目录，应先按项目现有入口和脚本路径约束准备独立运行副本，保留旧结果。

## 备份与回退

`export` 导出一个带校验的完整配置版本，不包含环境变量中的密钥值；可在空目录用 `init --file` 恢复为独立配置存储。`backup` 使用 SQLite 一致性备份，包含全部历史、批次绑定及原始迁移源，适合完整恢复；不要直接复制正在写入的 SQLite 文件。

所有导出、备份和原始源码恢复目标必须不存在，避免覆盖旧备份。原始迁移源可导出到一个新目录：

```bash
python3 config_cli.py --store "$SERP_CONFIG_STORE" export-sources --directory /srv/serp-news-state/original-source
```

保留配置文件和至少一份独立的完整备份。只回退代码不会自动恢复配置；旧代码不读取新存储，需要配套恢复原始 `config.py` 和匹配的模型调用源码。若迁移后已调整配置，应先决定恢复到哪个业务版本，不能将旧源码默认视为最新生产配置。

配置文件损坏时停止新任务，使用已经验证的完整备份恢复到新路径，核对 `status` 后再切换 `SERP_CONFIG_STORE`。仅导出单个版本再初始化时会生成新的存储 ID，不带旧批次绑定；历史输出需单独核验。不同代码结构版本不保证自动兼容，恢复前先使用对应代码版本校验。

## 离线验收

```bash
python -m pip install -r requirements-dev.txt
python run_config_tests.py
```

测试使用临时配置及工作目录，阻止网络连接，不读写生产配置或业务服务。覆盖生产基线的 22 段原文及模板展开、并发提交、写入中断、进程崩溃、损坏检测、幂等初始化、升级不覆盖、历史恢复、批次绑定、Web 表单和现有业务回归。

补充的 20 个系统案例、首次代码审查的 15 项回归及独立审查后的回归见 [全局测试记录](configuration-test-report.md)，覆盖迁移边界、事务提交失败、在线备份、鉴权、跨目录启动、整条流水线的版本传递、规则冲突、管理写操作保护和配置保留名称。旧日志回归脚本会在独立进程中执行。

`tests/fixtures/legacy/` 来自改造前已跟踪源码，仅用于 AST 迁移测试；`tests/fixtures/prompt_hashes.json` 固定本次确认的提示词哈希。初次迁移和存储重构不得顺带更新这些原文基线。旧源码夹具有意保留原文件的尾随空白，不应自动格式化；检查新增代码空白时可显式排除 `tests/fixtures/legacy/`。
