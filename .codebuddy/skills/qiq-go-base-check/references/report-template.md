# 审查报告与机器门禁格式

## 人类报告模板

### 总结

- 决策：PASS / BLOCKED / UNKNOWN / NEEDS_CONFIRMATION。
- 是否允许本次候选快照合入：仅 PASS 为是；工作区预审不可作为最终合入依据。
- 目标引用/target SHA、head SHA、merge-base SHA、候选快照 ID、采集时间、目标最新性证据。
- 快照类型：提交 / 工作区预审 / 平台或受控合并结果；实际构建和测试验证的快照。
- 分支交叉修改/集成风险及合并结果验证情况。

### 影响范围矩阵

| 服务/模块 | 修改/间接影响 | 构建入口/结果 | 自动化测试入口/结果 | 新增行为与单测/断言/覆盖证据 |
| --- | --- | --- | --- | --- |

### 八类门禁

| ID | 类别 | PASS/FAIL/UNKNOWN/NA | 理由 | 证据 |
| --- | --- | --- | --- | --- |
| build | 编译构建 | | | |
| tests | 单测用例 | | | |
| logic | 逻辑 bug | | | |
| security | Web/权限安全 | | | |
| performance | 静态性能/SQL 索引 | | | |
| observability | 关键链路指标 | | | |
| logging | 关键错误日志 | | | |
| business_rules | 数据库/DDL/JOIN 规则识别与执行 | | | |

将必要人工确认放在独立表，不将其当作技术豁免。没有相应变更时，business_rules 写有依据的 NA。

### 问题清单

| ID | 类别/严重性 | 阻断？ | 文件:行号 | 触发条件与执行路径 | 影响 | 修复建议 | 复查方式 |
| --- | --- | --- | --- | --- | --- | --- | --- |

区分已确认问题、证据缺口与非阻断建议；只将已确认且影响本次变更的漏洞/bug 写为 finding，无关存量问题放 warnings。问题引用保持精确行号；代码片段使用工具环境要求的行号引用格式。

### 必要人工确认

| 确认 ID/类型 | 数据库/表/查询及代码位置 | 状态 | 理由/替代方案 | 现网环境及已完成证据 | 批准人/时间 | 适用快照 |
| --- | --- | --- | --- | --- | --- | --- |

状态为 PENDING / CONFIRMED / REJECTED。每张表、每个受影响 JOIN 必须列出对应关卡，不使用笼统确认。确认依赖当前代码及目标环境，只能由有权限的人提供。

### 执行证据

逐条记录工作目录、命令、必要参数/环境（脱敏）、工具版本、开始/结束时间、退出码、实际快照、CI 链接或本地日志引用、失败摘要。没有执行的必查项写原因及补证方式，不能伪造命令日志。

### 不阻断提醒与下一步

列出重复/高基数指标精简、热路径冗余日志精简及低风险建议；列出阻断解除所需的具体步骤。声明当前 Skill 没有替仓库配置 CI/分支保护，不能单独禁止 Git 合并。

## JSON 输入格式（schema_version = 1）

使用以下字段生成真实审查报告，不将此说明中的占位内容当证据。

### 顶层字段

| 字段 | 类型/要求 |
| --- | --- |
| schema_version | 整数 1 |
| scope | 下表定义的审查快照对象 |
| checks | 恰好覆盖八个 ID，无缺失或重复 |
| required_confirmation_ids | 必要人工确认 ID 数组，无确认时为空；必须与 confirmations 精确对应 |
| confirmations | 下述确认对象数组，无确认时为空 |
| findings | 已确认的本次问题数组，无问题时为空 |
| warnings | 不阻断提醒字符串数组，无提醒时为空 |

### scope

| 字段 | 要求 |
| --- | --- |
| target_ref | 非空目标引用，例如 origin/main |
| target_sha / head_sha / merge_base_sha | 真实完整小写 SHA，40 或 64 位十六进制 |
| reviewed_at | 有效的 ISO 8601 审查时间，带时区和秒，例如 2026-10-12T10:00:00Z |
| snapshot_id | 唯一快照标识；绑定目标提交、候选提交/工作区指纹及环境证据 |
| snapshot_kind | committed / worktree / merge_result |
| target_verified | 布尔；确认目标最新且有效时为 true，否则总结果 UNKNOWN |
| integration_risk | 布尔；交叉修改或依赖等存在集成风险时为 true |
| merge_result_verified | 布尔；有匹配平台/受控集成快照的验证证据时为 true，不以分支单独通过冒充 |

worktree 仅预审，聚合器始终返回不允许合入；merge_result 类型必须声明已验证。存在 integration_risk 且未验证合并结果时总结果 UNKNOWN。

### checks 数组项

- id：build / tests / logic / security / performance / observability / logging / business_rules。
- status：PASS / FAIL / UNKNOWN / NA。
- reason：非空理由；NA 必须解释为何不适用。
- evidence：非空证据引用字符串数组；UNKNOWN 可以为空，但 reason 必须说明补证需求。证据引用要能追溯到快照、代码行或可信 CI，不使用“AI 说通过”作为唯一证据。

当技术检查完整且业务关卡都已登记，但人工还未确认时，business_rules 可记 PASS，confirmations 中保留 PENDING，总结果 NEEDS_CONFIRMATION。未能识别关卡则记 UNKNOWN。

### confirmations 数组项

- id：唯一非空确认 ID。
- type：DATABASE_REUSE / PRODUCTION_SCHEMA / JOIN。
- status：PENDING / CONFIRMED / REJECTED。
- scope：非空，精确数据库/表/变更项或代码查询位置。
- reason：非空复用理由、JOIN 必要性/替代方案或现网完成说明。
- snapshot_id：非空关联快照；CONFIRMED 必须与本报告 scope.snapshot_id 一致。
- evidence：证据引用字符串数组；CONFIRMED 必须非空。
- approved_by / confirmed_at：CONFIRMED 必填非空批准人及有效 ISO 8601 时间（带时区和秒），使用真实人工身份与时间。
- environment：PRODUCTION_SCHEMA 且 CONFIRMED 必填精确现网环境。

脚本不验证“现网已经改表”是否真实。审查者必须核实相应证据，不可将计划任务等填写为已完成。缺批准人/证据/环境或已确认快照不匹配均是无效报告。

### findings 数组项

- id：唯一非空问题 ID。
- category：八类 ID 之一。
- severity：CRITICAL / HIGH / MEDIUM / LOW。
- blocking：布尔。
- rule：问题类型；必须阻断的类型见下文，普通低风险建议填写 GENERAL，不能将硬门禁问题伪装为 GENERAL。
- summary：非空问题摘要。
- evidence：非空证据引用数组，包含位置、路径和影响。

已确认 security 问题、CRITICAL/HIGH 的 logic/performance 问题以及以下显式硬门禁问题，即使 blocking 错填 false 也强制阻断。必须阻断的问题同时在相应 check 记录 FAIL；若误写 PASS，聚合器仍根据 finding 阻断。不允许把无索引、缺单测等仅写作非阻断 warning。

| rule | category |
| --- | --- |
| BUILD_FAILURE | build |
| TEST_FAILURE / MISSING_UNIT_COVERAGE | tests |
| SEVERE_LOGIC_BUG | logic |
| SECURITY_VULNERABILITY | security |
| PERFORMANCE_REGRESSION / NO_USABLE_SQL_INDEX / SLOW_QUERY | performance |
| MISSING_CRITICAL_METRIC | observability |
| MISSING_ERROR_LOG / MISSING_LOG_CONTEXT | logging |
| BUSINESS_RULE_VIOLATION | business_rules |

rule 与 category 不一致或未提供合法 rule 时报告无效。GENERAL 仅用于不属于上述硬门禁的普通建议；单纯指标/日志冗余放 warnings。

## 聚合器运行与结果

执行 `python3 SKILL_DIR/scripts/evaluate_gate.py REPORT.json`，stdout 输出单个 JSON，包含 decision、allow_merge、failed_checks、unknown_checks、scope_issues、blocking_findings、pending_confirmations、rejected_confirmations、warnings 和 snapshot_id。报告无效时仅输出无效结果与脱敏错误。

| 决策 | 退出码 | 合入 |
| --- | --- | --- |
| PASS | 0 | 仅本报告绑定快照可放行 |
| BLOCKED | 1 | 禁止 |
| NEEDS_CONFIRMATION | 2 | 禁止，等待必要人工确认 |
| UNKNOWN | 3 | 禁止，补足必查证据 |
| INVALID_REPORT | 4 | 禁止，修正报告格式或过期确认 |

优先级：报告不可解析/格式不符先返回 INVALID_REPORT；合法报告内 FAIL/阻断 finding/REJECTED 优先于 UNKNOWN，UNKNOWN 优先于待确认，最后才 PASS。提醒不会阻断。

聚合器只校验结构与聚合声明，不能独立证明编译、测试、索引、AI review 或人工身份真实有效。可信 CI 接入时由受保护的审查流程生成报告，校验当前候选/目标 SHA 和审批来源，再要求本检查退出码为 0；不要允许 PR 修改报告或审查脚本来获得通过。未经单独请求不实施 CI/分支保护配置。

## 最少验证场景

- 任一受影响服务构建失败：BLOCKED。
- 测试全绿但新增代码无有断言单测：tests FAIL，BLOCKED。
- 缺执行环境：对应检查 UNKNOWN，不放行。
- 引入严重 bug、IDOR、未参数化 SQL：对应检查 FAIL，BLOCKED。
- 有 SQL 但无可用索引：performance FAIL，BLOCKED；缺索引定义则 UNKNOWN。
- 关键链路没有指标，或关键错误只有模糊日志：对应检查 FAIL，BLOCKED。
- 新表复用数据库未解释、现网 DDL 未完成、JOIN 未确认：NEEDS_CONFIRMATION；其余技术问题仍可导致更强阻断。
- 指标或日志冗余但无其他问题：warnings，不因冗余本身阻断。
- 代码/目标或确认快照变化：重新审查，不能沿用旧 PASS。
