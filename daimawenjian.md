# 代码文件说明文档

## 📋 项目概述

本项目是一个**新闻采集与正文抓取自动化系统**，具备完善的错误处理机制和详细的日志记录功能。系统能够自动从多个新闻源采集新闻、抓取正文、进行AI评分和摘要生成，并将数据存储到数据库中。

## 🛡️ 错误处理架构

### 核心错误处理模块：`error_handler.py` ✅ **新增**

这是整个项目的**统一错误处理系统**，于2025年新增，提供以下功能：

#### 主要组件

1. **ErrorHandler类**：统一的错误记录和管理
   - `log_error()`: 记录结构化错误信息到 `error_log.txt`
   - `log_step_failure()`: 记录步骤失败到 `run_log.txt`
   - `log_program_crash()`: 记录程序崩溃信息

2. **装饰器系统**
   - `@with_error_handling(script_name, stage)`: 为函数添加自动错误处理
   - 自动捕获异常并记录详细信息
   - 支持KeyboardInterrupt（Ctrl+C）优雅处理

3. **安全子进程执行**
   - `safe_subprocess_run()`: 替代`os.system`，提供错误捕获
   - 自动记录命令执行状态和错误信息
   - 支持标准输出和错误输出捕获

4. **全局异常处理**
   - `setup_global_exception_handler()`: 捕获未处理的异常
   - 自动记录到日志文件

5. **脚本生命周期记录**
   - `log_script_start()`: 记录脚本开始执行
   - `log_script_complete()`: 记录脚本执行完成状态

## 📁 主要模块说明

### 1. `main.py` - 主控制程序 ✅

**功能**：协调整个新闻处理流程，包括采集、正文抓取、AI评分、摘要生成和数据库存储

**错误处理特性**：
- ✅ 使用 `@with_error_handling` 装饰器
- ✅ 全局异常处理器设置
- ✅ 使用 `safe_subprocess_run` 替代 `os.system`
- ✅ 详细的执行统计和成功率跟踪
- ✅ 脚本生命周期记录

**关键函数**：
- `execute_news_fetching()`: 新闻采集阶段
- `execute_content_fetching()`: 正文抓取阶段
- `execute_scoring()`: AI评分阶段
- `main()`: 主流程控制

### 2. `fetch_content.py` - 正文抓取模块 ✅

**功能**：多级兜底机制抓取新闻正文，支持定制化规则

**错误处理特性**：
- ✅ 使用 `@with_error_handling` 装饰器
- ✅ 批量处理和单关键词处理的错误统计
- ✅ 详细的抓取失败日志记录
- ✅ 脚本生命周期记录

**抓取策略**：
1. 定制化规则抓取（针对特定站点）
2. trafilatura 静态提取
3. newspaper3k 静态提取
4. Playwright 渲染抓取
5. Selenium 兜底抓取

### 3. `news_scorer.py` - AI评分模块 ✅

**功能**：使用AI大模型对新闻进行智能评分，支持规则打分

**错误处理特性**：
- ✅ 使用 `@with_error_handling` 装饰器
- ✅ API调用异常处理和重试机制
- ✅ Token超限错误的详细记录
- ✅ 批量处理统计和错误跟踪
- ✅ **新增Unicode编码处理**：`clean_unicode_for_console()` 函数处理新闻内容中的特殊字符

**特殊处理**：
- 规则匹配自动打分（节省token）
- API错误详细日志记录
- 支持多种AI模型配置

**✅ 编码问题修复**（2025年最新更新）：
- **问题识别**：新闻内容中包含emoji和特殊Unicode字符（如©、✔、📰等）导致Windows GBK编码错误
- **解决方案**：添加`clean_unicode_for_console()`函数，自动替换或移除有问题的字符
- **应用范围**：所有控制台输出都经过Unicode清理处理
- **✅ 总结模板emoji图标恢复**：区分了新闻内容编码问题和总结模板视觉效果，恢复了总结模板中的emoji图标（📰、📺、📊）

### 4. `news_summarizer.py` - 智能摘要模块 ✅

**功能**：生成新闻摘要，支持多轮摘要和热点追踪

**错误处理特性**：
- ✅ 使用 `@with_error_handling` 装饰器
- ✅ API调用错误处理和重试机制
- ✅ 脚本生命周期记录
- ✅ **新增Unicode编码处理**：同样应用了`clean_unicode_for_console()`函数

**摘要流程**：
1. 初稿摘要生成
2. 评判官建议 + 优化摘要
3. 热点追踪（对比历史摘要）

**✅ 新增特殊处理逻辑**（2025年最新更新）：
- **关键词特殊处理**：为"江苏省国资委"关键词提供特殊处理
- **多源新闻摘要**：该关键词使用所有API来源（Google、Baidu、Bing、DuckDuckGo）的3分以上新闻进行摘要
- **默认过滤规则**：其他关键词仍使用配置中的 `NEWS_SUMMARY_FILTER_SOURCEAPI` 设置（当前为 "serp_googlenews"）
- **动态过滤切换**：程序根据关键词自动选择是否应用来源过滤
- **详细日志输出**：明确标记当前使用的新闻来源范围，便于调试和确认

### 5. `write_to_mysql.py` - 数据库写入模块 ✅

**功能**：将处理后的数据写入MySQL数据库

**错误处理特性**：
- ✅ 使用 `@with_error_handling` 装饰器
- ✅ 数据库连接错误处理
- ✅ 数据写入失败详细记录
- ✅ 连接资源安全释放

**写入内容**：
- 新闻正文及评分数据
- 新闻摘要数据
- 新闻源统计数据
- 网站域名数据

### 6. `fetch_and_filter.py` - 新闻采集过滤模块 ✅

**功能**：从多个新闻API采集新闻，进行过滤和去重

**错误处理特性**：
- ✅ 使用 `@with_error_handling` 装饰器
- ✅ API调用错误处理
- ✅ 数据处理异常记录
- ✅ 脚本生命周期记录

**采集源**：
- Google News (SerpAPI)
- Baidu News (SerpAPI)
- Bing News (SerpAPI)
- DuckDuckGo News (SerpAPI)

### 7. `news_fetcher.py` - 新闻获取基础模块

**功能**：提供底层新闻API调用功能

**错误处理特性**：
- ✅ 基本的API错误记录到 `error_log.txt`
- ✅ 重试机制（3次重试）
- ✅ 超时处理

### 8. 其他模块

- `config.py`: 配置文件，包含API密钥、关键词配置等
- `config_grab_rules.py`: 定制化抓取规则配置
- `app.py`: Web界面展示模块
- `test_fetcher.py`: 测试模块
- `error_handler.py`: ✅ **核心错误处理模块**（新增）
- `技术优化记录.md`: ✅ **技术优化记录文档**（新增）

## 📊 日志系统

### 错误日志：`output/error_log.txt`
记录所有错误的详细信息，包括：
- 时间戳
- 脚本名称
- 关键词
- 错误类型
- 错误信息
- 详细堆栈信息
- 上下文信息

### 运行日志：`output/run_log.txt`
记录程序运行状态，包括：
- 脚本开始/完成时间
- 执行步骤状态
- 成功/失败统计
- 跳过原因记录
- 执行结果摘要

## 🚀 系统优势

### 1. **完善的错误处理**
- 统一的错误处理机制
- 详细的错误分类和记录
- 自动异常捕获和恢复

### 2. **可靠的执行流程**
- 断点续传支持
- 智能跳过已处理数据
- 多级兜底策略

### 3. **详细的日志记录**
- 结构化日志格式
- 完整的执行轨迹
- 便于问题定位和调试

### 4. **高容错性**
- 单个步骤失败不影响整体流程
- 自动重试机制
- 优雅的降级处理

## 🔧 使用方式

### 完整流程运行
```bash
python main.py [日期]
```

### 单独模块运行
```bash
# 新闻采集
python fetch_and_filter.py "关键词" 2025-06-01 --output "输出文件.json"

# 正文抓取
python fetch_content.py 关键词 2025-06-01

# AI评分
python news_scorer.py 关键词 2025-06-01

# 摘要生成
python news_summarizer.py --keyword 关键词 --date 2025-06-01

# 数据库写入
python write_to_mysql.py --date 2025-06-01
```

## 📝 错误处理最佳实践

1. **所有主要函数都使用 `@with_error_handling` 装饰器**
2. **程序开始时调用 `setup_global_exception_handler()`**
3. **使用 `safe_subprocess_run` 替代 `os.system`**
4. **在脚本开始和结束时记录生命周期**
5. **异常发生时记录详细的上下文信息**
6. **提供有意义的错误信息给用户**

通过这套完善的错误处理机制，无论在哪个阶段发生错误，都能在日志中找到详细的记录，大大提高了系统的可维护性和问题排查效率。 