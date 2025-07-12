# 代码文件说明文档

## 最新更新记录

### 2025-01-17 - Windows GBK编码问题修复

**问题描述**：
在Windows生产环境部署时，程序在执行正文抓取操作时报错：
```
UnicodeEncodeError: 'gbk' codec can't encode character '\u2705' in position 5: illegal multibyte sequence
```

**问题原因**：
Windows系统默认使用GBK编码，无法处理代码中使用的emoji图标（如✅、❌、🔑等Unicode字符）。

**解决方案**：
1. **fetch_content.py** - 替换所有print语句中的emoji图标为文本标记
   - ✅ → [成功]
   - ❌ → [失败] 
   - 📊 → [统计]

2. **error_handler.py** - 修复日志和print语句中的emoji图标
   - 🚩 → [开始]
   - 🔑 → [关键词]
   - 📋 → [命令]
   - ✅ → [完成]
   - ❌ → [错误]
   - 🕒 → 直接使用时间格式

3. **main.py** - 替换步骤提示中的emoji图标
   - 🌟 → [步骤1/2/3/4/5]
   - 📊 → [统计]
   - 🎉 → [完成]

4. **news_scorer.py** - 修复print语句和日志中的emoji图标
   - 修复了缩进问题导致的语法错误
   - 替换所有emoji为文本标记

5. **news_summarizer.py** - 替换所有emoji图标
   - 🤖 → [尝试]
   - 📝 → [流式输出]
   - ✅ → [成功]
   - ❌ → [失败]
   - 🔄 → [重试]

6. **write_to_mysql.py** - 替换统计信息中的emoji图标
   - 📊 → [统计]

**修复效果**：
- 解决了Windows环境下的GBK编码错误
- 保持了日志的可读性
- 确保程序在Windows生产环境正常运行

**注意事项**：
- 所有emoji图标都已替换为方括号包围的文本标记
- 保持了原有的功能和日志格式
- 兼容Windows和Linux/Mac环境

### 2025-01-17 - fetch_content.py返回值逻辑修复

**问题描述**：
用户反映在Windows主机上手动执行抓取公积金关键词时，虽然抓取过程正常完成（成功抓取40篇，失败6篇），但最后仍然报告执行失败。

**问题原因**：
1. `process_json` 函数被 `@with_error_handling` 装饰器包装
2. 函数正常执行完毕但没有显式返回值，默认返回 `None`
3. 跳过已处理文件时也返回 `None`
4. `main` 函数中的逻辑：如果 `process_json` 返回 `None` 就认为执行失败

**解决方案**：
1. **修复process_json函数返回值**：
   - 正常执行完成时返回 `True`
   - 跳过已处理文件时返回 `True`（跳过也算成功）
   - 文件不存在时返回 `False`

2. **修复main函数判断逻辑**：
   - 原来只判断 `result is None`
   - 现在判断 `result is None or result is False`

**修复效果**：
- 正常执行完成：返回码0（成功）
- 跳过已处理文件：返回码0（成功）
- 文件不存在：返回码1（失败）
- 避免了正常执行时的误报失败

**测试验证**：
- 跳过已处理文件：`python fetch_content.py 公积金 2025-07-10 --test` → 退出码0
- 文件不存在：`python fetch_content.py 测试关键词 2025-07-10 --test` → 退出码1

---

## 概述
本文档记录serp_news项目中各个代码文件的功能、作用和实现细节，便于查找和排查问题。

## 核心程序文件

### 1. main.py - 主程序入口
**功能**：项目总控制程序，协调执行新闻采集、处理、评分、摘要、入库等完整流程
**主要模块**：
- 新闻采集：调用news_fetcher.py和fetch_and_filter.py
- 内容抓取：调用fetch_content.py
- AI评分：调用news_scorer.py  
- 摘要生成：调用news_summarizer.py
- 数据库写入：调用write_to_mysql.py
- 错误处理：集成error_handler.py
**配置依赖**：config.py中的DEFAULT_KEYWORDS、各模块开关配置
**日志记录**：统一写入output/run_log.txt
**最新更新**：支持自动批量处理默认关键词列表

### 2. news_fetcher.py - 新闻数据采集
**功能**：从多个搜索引擎API获取新闻数据
**支持平台**：Google News、Bing News、DuckDuckGo News、百度新闻  
**数据处理**：去重、格式化、保存为JSON
**输出格式**：raw_serp_{api}_{keyword}_{date}.json
**关键特性**：
- 多API并发采集
- 自动去重合并
- 异常恢复机制
- 请求频率控制
**配置项**：各API的base_url、headers、超时设置

### 3. fetch_content.py - 新闻正文抓取
**功能**：根据新闻链接抓取完整正文内容
**技术实现**：
- requests + BeautifulSoup解析
- 多种编码自动检测
- 正文提取算法优化
- 自定义抓取规则支持
**输出格式**：更新原JSON，添加content、wordcount字段
**异常处理**：网络超时、解析失败的容错机制
**性能优化**：并发抓取、智能重试
**最新修复**：修复了process_json函数返回值问题，避免正常执行时误报为失败

### 4. news_scorer.py - AI智能评分
**功能**：使用大语言模型对新闻进行1-5分评分
**模型支持**：
- DeepSeek V3 (deepseek-chat)：快速模型，适合大数据量
- DeepSeek R1 (deepseek-reasoner)：推理模型，准确性更高
**智能策略**：
- 根据关键词自动选择模型（大数据量关键词使用V3，其他使用R1）
- 双关键词机制：模型选择基于主关键词，AI评分使用搜索关键词
- Token超限自动切换到千问模型
**评分标准**：相关性、时效性、重要性综合评判
**输出格式**：{date}_{keyword}_scored.json
**最新优化**：
- 添加了模型选择的详细日志
- 支持主关键词和搜索关键词的分离处理
- 特殊关键词（江苏省国资委、国资委测试）自动使用V3模型

### 5. news_summarizer.py - 新闻摘要生成 
**功能**：对高分新闻进行AI总结，生成多轮优化摘要
**处理流程**：
1. **第1轮-初稿摘要**：基础总结
2. **第2-1轮-评判官意见**：分析初稿问题
3. **第2-2轮-优化摘要**：根据建议改进
4. **第3轮-热点追踪**：对比昨天摘要，识别持续热点
**模型策略**：
- 优先使用DeepSeek模型
- Token超限自动切换千问模型
- 流式输出支持
**数据过滤**：
- 只处理3分及以上新闻
- 支持特定来源过滤（默认只用Google News）
- 江苏省国资委关键词特殊处理，使用所有来源
**输出格式**：{date}_{keyword}_summary.json + {date}_{keyword}_optimization_prompts.txt
**调用优化**：详细的进度提示和错误诊断，超时时间优化为300秒
**热点追踪修复**：修复了数据库连接问题，确保能正确获取昨天的摘要进行对比

### 6. write_to_mysql.py - 数据库写入
**功能**：将处理后的数据写入MySQL数据库
**支持表格**：
- scored_news：新闻正文及评分数据
- summary_news：摘要数据（支持多轮）
- news_websites：新闻源域名统计
- news_source_stats：新闻来源分布统计
**数据处理**：
- 自动去重（多字段组合）
- 空内容过滤
- 批量导入优化
- 错误记录和统计
**最新更新**：
- 添加search_keyword字段支持
- 修复了fetch_latest_summary函数的数据库连接问题
- 独立数据库连接避免全局cursor失效
**安全特性**：参数化查询防SQL注入

### 7. config.py - 全局配置文件
**功能**：集中管理所有配置参数
**配置类别**：
- API配置：各搜索引擎接口参数
- 模型配置：AI模型的API密钥和地址
- 业务配置：关键词列表、文件路径模板
- 系统配置：超时设置、并发数量
**关键参数**：
- DEFAULT_KEYWORDS：默认处理的关键词列表
- NEWS_SUMMARY_MODELS：支持的AI模型配置
- 各种PROMPT模板：评分、摘要、优化的提示词
**环境变量**：数据库连接、API密钥等敏感信息

### 8. error_handler.py - 错误处理系统
**功能**：全局异常处理和错误日志记录
**特性**：
- 装饰器模式的异常捕获
- 结构化错误日志
- 脚本运行状态跟踪
- 错误分类和统计
**输出格式**：output/error_log.txt
**集成方式**：所有主要程序都集成了错误处理

## 工具和辅助文件

### 9. app.py - Web界面
**功能**：提供Web界面查看新闻数据和运行状态
**技术栈**：Flask + HTML模板
**页面功能**：
- 首页：系统概览
- 数据库查看：新闻列表和摘要
- 日期选择：按日期筛选数据
- 关键词选择：按关键词查看结果
**模板文件**：templates/目录下的HTML文件

### 10. 其他工具文件
- **run.bat**：Windows批处理脚本，快速启动主程序
- **requirements.txt**：Python依赖包列表
- **test_fetcher.py**：新闻采集功能测试脚本

## 输出目录结构

### output/日期目录/
每日运行结果按日期组织：
- **原始数据**：raw_serp_{api}_{keyword}_{date}.json
- **正文数据**：{date}_{keyword}.json  
- **评分数据**：{date}_{keyword}_scored.json
- **摘要数据**：{date}_{keyword}_summary.json
- **优化记录**：{date}_{keyword}_optimization_prompts.txt
- **运行日志**：run_log.txt

### output/根目录
- **error_log.txt**：错误日志汇总
- **news_sources.txt**：新闻源域名列表
- **news_source_stats.json**：新闻源统计数据
- **run_log.txt**：主运行日志

## 运行流程说明

### 完整运行流程（main.py）
1. **新闻采集阶段**：调用news_fetcher.py从各API获取新闻
2. **正文抓取阶段**：调用fetch_content.py获取新闻完整内容
3. **AI评分阶段**：调用news_scorer.py对新闻进行智能评分
4. **摘要生成阶段**：调用news_summarizer.py生成多轮优化摘要
5. **数据库写入阶段**：调用write_to_mysql.py将数据持久化存储

### 单独运行模式
每个核心模块都支持独立运行，便于调试和部分处理：
```bash
python news_fetcher.py --keyword "关键词" --date "2025-01-15"
python fetch_content.py --keyword "关键词" --date "2025-01-15"  
python news_scorer.py --keyword "关键词" --date "2025-01-15"
python news_summarizer.py --keyword "关键词" --date "2025-01-15"
python write_to_mysql.py --date "2025-01-15"
```

## 关键技术特性

### 1. 智能容错机制
- API失败自动重试
- 模型切换策略
- 数据验证和清洗
- 异常恢复和日志记录

### 2. 性能优化
- 并发处理提升效率
- Token使用优化
- 数据库连接池
- 智能缓存策略

### 3. 数据质量保证
- 多层去重机制
- 内容质量过滤
- AI评分验证
- 人工审核接口

### 4. 可扩展性设计
- 模块化架构
- 配置文件集中管理
- 插件式功能扩展
- API接口标准化

## 最新技术优化记录

### 2025年修复和优化
1. **数据库字段优化**：添加search_keyword字段，支持精准的搜索关键词记录
2. **AI评分模型优化**：智能模型选择，大数据量关键词使用更快的DeepSeek V3
3. **双关键词机制**：模型选择基于主关键词，AI评分使用搜索关键词，提高评分准确性
4. **摘要进度优化**：详细的进度提示，超时时间优化，错误诊断增强
5. **热点追踪修复**：修复数据库连接问题，确保热点追踪功能正常工作
6. **空内容过滤**：自动过滤内容为空的新闻记录，提高数据质量

这些优化显著提升了系统的稳定性、准确性和用户体验。
