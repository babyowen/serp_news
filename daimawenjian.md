# 代码文件说明文档

### 2025-07-15 - 优化百度新闻API采集策略

**优化目标**:
根据用户测试发现，百度新闻API在不指定语言、不进行分页的情况下可以一次性返回更多结果。为了节约API调用成本并简化代码，对采集逻辑进行优化。

**具体变更**:

1. **`news_fetcher.py` - `fetch_serpapi_baidu_news` 函数重构**:
   - **移除分页逻辑**: 取消了原有的 `max_pages` 参数和循环分页获取机制。
   - **增加单次返回数量**: 在API请求参数中增加了 `rn=50`，使单次API调用能返回最多50条新闻结果。
   - **简化函数实现**: 整个函数逻辑被简化为一次API请求和相应的重试处理。

   ```python
   # 修改前
   def fetch_serpapi_baidu_news(keyword, max_pages=3):
       # ... 循环和分页逻辑 ...
       # ... 多次请求 ...
       return {"organic_results": all_results}

   # 修改后
   def fetch_serpapi_baidu_news(keyword):
       params = {
           "engine": "baidu_news",
           "q": keyword,
           "api_key": SERPAPI_KEY,
           "rtt": 4,
           "rn": 50  # 一次性获取更多结果
       }
       # ... 单次请求和重试逻辑 ...
       return resp.json()
   ```

**优化效果**:
- **提升采集效率**: 将之前需要多次API调用的分页查询改为单次调用。
- **节约API成本**: 显著减少了对百度新闻API的请求次数。
- **简化代码维护**: 函数逻辑更清晰、更易于理解和维护。

**验证方式**:
- 通过 `test_news_fetcher.py` 脚本进行测试，验证了修改后可以成功从百度新闻采集到50条数据，符合预期。

## 最新更新记录

### 2025-07-13 - 修改新闻摘要源过滤策略

**修改内容**：
将新闻摘要功能从"只有江苏省国资委使用所有新闻源，其他关键词只使用Google News"改为"所有关键词都使用所有新闻源"。

**具体变更**：

1. **`config.py`配置修改**：
   ```python
   # 修改前
   NEWS_SUMMARY_FILTER_SOURCEAPI = "serp_googlenews"
   
   # 修改后
   NEWS_SUMMARY_FILTER_SOURCEAPI = None  # 设置为None表示不过滤，使用所有新闻源
   ```

2. **`news_summarizer.py`逻辑简化**：
   - 移除了针对"江苏省国资委"的特殊处理逻辑
   - 统一使用配置项`NEWS_SUMMARY_FILTER_SOURCEAPI`来决定是否过滤
   - 简化了日志输出信息

**影响范围**：
- 所有关键词的摘要功能现在都会使用来自所有API源的新闻（百度新闻、必应新闻、DuckDuckGo新闻、Google新闻）
- 提高了摘要的全面性和准确性
- 统一了处理逻辑，减少了代码复杂度

### 2025-07-13 - 新增智能重试机制与连接池管理

**优化目标**：
基于SSL连接泄漏问题的根本解决，进一步优化系统的稳定性和性能，特别是针对偶发性网络错误的处理。

**新增功能**：

1. **智能重试机制**：
   - **错误分类识别**：
     - SSL错误：`ssl, connection, socket, handshake, certificate`
     - 网络错误：`network, dns, resolve, unreachable, connection refused`
     - 超时错误：`timeout, timed out, time out`
     - Token超限：`token, context length, input length, max input limit`
     - 速率限制：`rate limit, rate_limit, quota, too many requests`
   
   - **差异化重试策略**：
     - 普通错误：默认重试间隔（10秒/5秒）
     - SSL/网络错误：重试间隔×2（更长等待时间）
     - 速率限制：重试间隔×3（避免频繁触发限制）
     - Token超限：不重试（无法通过重试解决）

2. **连接池管理系统**：
   - **`OpenAIClientPool`**（摘要专用）：
     - 支持多平台多模型的客户端缓存
     - 每个客户端最大使用50次后自动更换
     - 自动管理连接的创建和关闭
   
   - **`ScoringClientPool`**（评分专用）：
     - 单一客户端高效复用
     - 每个客户端最大使用100次后自动更换
     - 专门优化评分场景的高频调用

3. **优化效果**：
   - **减少连接开销**：避免频繁创建/销毁SSL连接
   - **提高重试成功率**：智能识别错误类型，采用合适的重试策略
   - **增强系统稳定性**：偶发性网络问题不再导致程序崩溃
   - **改善用户体验**：详细的重试进度显示和错误分类

**技术实现细节**：

1. **`news_summarizer.py`重试优化**：
   ```python
   # 智能错误分类
   is_ssl_error = any(x in err_str.lower() for x in ["ssl", "connection", "socket", "handshake", "certificate"])
   is_network_error = any(x in err_str.lower() for x in ["network", "dns", "resolve", "unreachable", "connection refused"])
   is_rate_limit = any(x in err_str.lower() for x in ["rate limit", "rate_limit", "quota", "too many requests"])
   
   # 差异化重试间隔
   if is_ssl_error or is_network_error:
       actual_retry_interval = retry_interval * 2
   elif is_rate_limit:
       actual_retry_interval = retry_interval * 3
   else:
       actual_retry_interval = retry_interval
   ```

2. **`news_scorer.py`重试机制**：
   - 从无重试升级到3次重试
   - 增加60秒超时设置
   - 根据错误类型调整重试间隔
   - 连接池管理避免频繁创建客户端

3. **连接池生命周期管理**：
   - 程序启动时创建全局连接池
   - 运行过程中智能复用连接
   - 程序结束时统一清理所有连接

**使用场景**：
- **网络不稳定环境**：自动重试网络相关错误
- **高并发场景**：连接池减少资源竞争
- **长时间运行**：定期更换连接避免长连接问题
- **批量处理**：避免SSL连接累积导致的资源耗尽

### 2025-07-13 - 修复SSL连接泄漏问题，解决程序异常退出

**问题描述**：
在Windows环境下运行新闻摘要程序时，出现SSL连接资源泄漏问题：
1. **SSL连接未关闭警告**：`ResourceWarning: unclosed <ssl.SSLSocket fd=2572, family=2, type=1, proto=0, laddr=('10.0.12.17', 64386), raddr=('116.205.40.114', 443)>`
2. **程序异常退出**：返回错误码1，导致整个流程停止
3. **批量处理失败**：处理多个关键词时，SSL连接累积导致资源耗尽

**根本原因**：
- **OpenAI客户端连接未正确关闭**：在`news_summarizer.py`和`news_scorer.py`中，OpenAI客户端创建后没有显式关闭
- **资源泄漏累积**：每次调用大模型API都会创建新的SSL连接，但未释放
- **批量处理时影响放大**：处理多个关键词时，连接数量激增，最终导致系统资源耗尽

**详细修复措施**：

1. **`news_summarizer.py` - `call_llm`函数优化**：
   ```python
   def call_llm(system_prompt, user_prompt, platform, model_name, ...):
       # 创建OpenAI客户端
       client = OpenAI(api_key=model_cfg['api_key'], base_url=model_cfg['base_url'])
       
       try:
           # 原有的API调用逻辑
           response = client.chat.completions.create(...)
           return result, token_limit_info
       finally:
           # 确保客户端连接正确关闭，避免SSL连接泄漏
           try:
               if hasattr(client, 'close'):
                   client.close()
               elif hasattr(client, '_client') and hasattr(client._client, 'close'):
                   client._client.close()
           except Exception as e:
               safe_print(f"[DEBUG] 关闭OpenAI客户端时发生异常: {e}")
           safe_print(f"[DEBUG] OpenAI客户端已关闭（{platform}-{model_name}）")
   ```

2. **`news_scorer.py` - `score_news`函数优化**：
   ```python
   def score_news(title: str, content: str, keyword: str, main_keyword: str = None) -> int:
       # 创建OpenAI客户端
       client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
       
       try:
           # 原有的评分逻辑
           response = client.chat.completions.create(...)
           return score
       finally:
           # 确保客户端连接正确关闭，避免SSL连接泄漏
           try:
               if hasattr(client, 'close'):
                   client.close()
               elif hasattr(client, '_client') and hasattr(client._client, 'close'):
                   client._client.close()
           except Exception as e:
               safe_print(f"[DEBUG] 关闭OpenAI客户端时发生异常: {e}")
           safe_print(f"[DEBUG] OpenAI客户端已关闭（评分）")
   ```

3. **技术优化细节**：
   - **多层尝试关闭**：尝试`client.close()`和`client._client.close()`两种方式
   - **异常安全**：关闭过程中的异常不会影响主流程
   - **调试信息**：记录客户端关闭状态，便于问题排查
   - **资源管理**：使用try-finally确保无论成功失败都会关闭连接

**预期效果**：
- ✅ 消除SSL连接泄漏警告
- ✅ 避免程序异常退出（错误码1）
- ✅ 支持稳定的批量处理
- ✅ 改善系统资源利用率
- ✅ 提高长时间运行的稳定性

**测试验证**：
- 批量处理多个关键词时无SSL连接警告
- 程序正常完成所有处理步骤
- 系统资源使用稳定，无异常累积
- 日志中显示OpenAI客户端正确关闭

### 2025-07-13 - 优化Chrome浏览器配置，减少SSL连接错误

**问题描述**：
在Windows环境下的新闻正文抓取过程中，Chrome浏览器会产生以下几类日志信息：
1. **DevTools监听信息**：`DevTools listening on ws://127.0.0.1:xxxxx/devtools/browser/...`
2. **Chrome内部日志**：`WARNING: All log messages before absl::InitializeLog() is called are written to STDERR`
3. **SSL握手失败**：`[ERROR:net\socket\ssl_client_socket_impl.cc:896] handshake failed; returned -1, SSL error code 1, net_error -101/-100`

**Windows环境特有问题**：
- Windows下的Chrome进程管理和日志输出与Linux/Mac不同
- Windows的SSL证书处理机制更严格
- Windows防火墙和安全策略对网络连接的影响更大
- Windows下的Chrome控制台窗口会产生额外的系统消息

**问题原因**：
- SSL握手失败主要由目标网站的SSL证书问题、反爬虫机制或网络连接问题引起
- net_error -101：ERR_CONNECTION_RESET（连接重置）
- net_error -100：ERR_CONNECTION_CLOSED（连接关闭）
- Chrome默认配置对SSL验证较为严格，Windows环境下更为敏感

**Windows专用优化措施**：

1. **增强SSL容错能力**：
   - 添加`--ignore-ssl-errors`：忽略SSL错误
   - 添加`--ignore-certificate-errors-spki-list`：忽略证书错误列表
   - 添加`--ignore-urlfetcher-cert-requests`：忽略URL获取器证书请求
   - 添加`--disable-web-security`：禁用Web安全检查
   - 添加`--allow-running-insecure-content`：允许不安全内容
   - 添加`--disable-site-isolation-trials`：禁用站点隔离试验

2. **Windows特有的日志控制**：
   - 添加`--disable-logging`：禁用大部分日志
   - 添加`--log-level=3`：只显示致命错误
   - 添加`--silent`：静默模式
   - 添加`--disable-infobars`：禁用信息栏
   - 添加`--disable-notifications`：禁用通知
   - 添加`--disable-desktop-notifications`：禁用桌面通知
   - 设置`service.log_level = 'ERROR'`：服务层只输出错误

3. **Windows进程管理优化**：
   - 添加`--disable-hang-monitor`：禁用挂起监视器
   - 添加`--disable-prompt-on-repost`：禁用重新发送提示
   - 添加`--disable-domain-reliability`：禁用域名可靠性检查
   - 添加`--disable-component-extensions-with-background-pages`：禁用后台页面扩展
   - 设置`service.creation_flags = 0x08000000`：Windows下隐藏控制台窗口

4. **性能和稳定性优化**：
   - 设置`page_load_strategy = 'eager'`：不等待所有资源加载完成
   - 添加`--disable-images`：禁用图片加载
   - 添加`--disable-javascript`：在不需要JS的场景下禁用
   - 添加`--disable-java`：禁用Java插件
   - 添加`--disable-flash`：禁用Flash插件
   - 设置页面加载超时时间为30秒

5. **反爬虫对策**：
   - 添加`excludeSwitches`：移除自动化标识
   - 设置`useAutomationExtension = False`：禁用自动化扩展
   - 优化User-Agent字符串，专门针对Windows环境
   - 设置多项首选项阻止弹窗和通知

**代码结构优化**：
1. **新增函数`get_chrome_options_for_windows()`**：
   - 集中管理所有Windows专用的Chrome选项
   - 提供详细的选项分类和注释
   - 便于维护和调试

2. **新增函数`get_chrome_service_for_windows()`**：
   - 专门处理Windows环境下的Chrome服务配置
   - 智能检测Windows平台，应用专用设置
   - 提供异常处理，确保兼容性

**修复效果**：
- ✅ 大幅减少SSL握手失败的日志输出
- ✅ 提高对问题网站的容错能力
- ✅ 减少Windows特有的Chrome内部日志
- ✅ 隐藏Windows控制台窗口，减少系统消息
- ✅ 提升页面加载速度和稳定性
- ✅ 降低被网站反爬虫系统检测的概率
- ✅ 专门针对Windows环境的进程管理优化

**技术细节**：
在`fetch_content.py`中新增了两个专用函数：
```python
def get_chrome_options_for_windows():
    """获取针对Windows环境优化的Chrome选项"""
    options = Options()
    
    # Windows特有的SSL和安全配置
    options.add_argument('--ignore-ssl-errors')
    options.add_argument('--ignore-urlfetcher-cert-requests')
    options.add_argument('--disable-site-isolation-trials')
    
    # Windows特有的日志控制
    options.add_argument('--disable-infobars')
    options.add_argument('--disable-notifications')
    options.add_argument('--disable-desktop-notifications')
    
    # Windows进程管理
    options.add_argument('--disable-hang-monitor')
    options.add_argument('--disable-domain-reliability')
    
    return options

def get_chrome_service_for_windows():
    """获取针对Windows环境优化的Chrome服务配置"""
    service = Service(driver_path)
    service.log_level = 'ERROR'
    
    # Windows下隐藏控制台窗口
    if sys.platform.startswith('win'):
        try:
            service.creation_flags = 0x08000000  # CREATE_NO_WINDOW
        except:
            pass  # 兼容性处理
    
    return service
```

**注意事项**：
- 这些优化专门针对Windows环境，在其他平台上会自动适配
- 部分安全检查被禁用，仅适用于可信的新闻网站访问
- 控制台窗口隐藏功能仅在Windows环境下生效
- 如仍有个别网站出现SSL错误，属于正常现象，系统会自动跳过并继续处理
- 所有优化措施都经过异常处理，确保不影响程序的正常运行

### 2025-07-12 - 修复Windows环境编码问题

**问题描述**：
- 在Windows服务器上执行采集程序时出现UnicodeDecodeError
- 错误发生在subprocess._readerthread中，无法解码字节0xd5
- 虽然不影响数据保存，但会产生异常输出

**修复方案**：
1. **error_handler.py - safe_subprocess_run函数**
   - 添加跨平台编码兼容性处理
   - Windows环境使用GBK编码，避免UTF-8解码错误
   - 非Windows环境继续使用UTF-8编码
   - 添加errors='ignore'参数，忽略无法解码的字符

**修复效果**：
- ✅ 解决Windows环境下的UnicodeDecodeError问题
- ✅ 保持数据完整性不受影响
- ✅ 维持跨平台兼容性
- ✅ 提升用户体验，消除错误输出

**技术细节**：
```python
# Windows环境编码兼容性处理
if sys.platform.startswith('win'):
    # Windows下使用系统默认编码，避免UTF-8解码错误
    result = subprocess.run(cmd, shell=True, check=check, 
                          capture_output=True, text=True, encoding='gbk', errors='ignore')
else:
    # 非Windows环境使用UTF-8
    result = subprocess.run(cmd, shell=True, check=check, 
                          capture_output=True, text=True, encoding='utf-8')
```

### 2025-01-18 - 彻底解决跨平台emoji问题

**新增组件**：
1. **icon_manager.py** - 跨平台图标管理器
   - 自动检测运行环境（Windows/Mac/Linux）
   - 支持4种主题：emoji/text/colorful/minimal
   - 提供safe_print等安全输出函数
   - 自动处理GBK编码问题

2. **logger_utils.py** - 统一日志系统
   - 集成图标管理器
   - 提供Logger和NewsLogger类
   - 支持文件和控制台双重输出
   - 提供丰富的日志方法（start/success/error/step等）

3. **migration_example.py** - 迁移示例脚本
   - 展示新系统的各种用法
   - 提供详细的迁移指南
   - 包含性能对比和错误处理演示

**系统特性**：
- **自动环境检测**：根据操作系统和编码自动选择最佳主题
- **多主题支持**：emoji（Mac/Linux）、text（Windows兼容）、colorful（ANSI彩色）、minimal（最简化）
- **渐进迁移**：新旧系统可以并存，支持逐步迁移
- **零配置使用**：开箱即用，也支持高级配置
- **完整的API**：从简单的safe_print到完整的Logger类

**使用方法**：
```python
# 最简单的用法
from icon_manager import safe_print
safe_print("任务完成", "success")

# 推荐的日志器用法
from logger_utils import get_logger
logger = get_logger("main")
logger.start("开始执行")
logger.success("操作成功")

# 新闻系统专用
from logger_utils import get_news_logger
news_logger = get_news_logger("关键词", "2025-01-18")
news_logger.news_fetch("关键词", 25, "Google News")
```

**配置选项**（config.py）：
- `USE_ICON_MANAGER = True` - 启用新系统
- `FORCE_ICON_THEME = None` - 强制指定主题（None为自动检测）
- `ENABLE_LEGACY_UNICODE_CLEAN = True` - 保持向后兼容

**解决效果**：
- ✅ 彻底解决Windows GBK编码错误
- ✅ Mac开发环境保持emoji美观显示
- ✅ 自动适配不同运行环境
- ✅ 提供统一的API，便于维护
- ✅ 支持渐进式迁移，风险可控

### 2025-01-18 - 关键问题修复

**修复内容**：
1. **政府基金SKIP问题修复**：
   - 修复了`fetch_content.py`中的空列表跳过逻辑
   - 当新闻列表为空时，不再错误地跳过正文抓取
   - 修改逻辑：`len(news_list) > 0 and all('content' in item for item in news_list)`

2. **GBK编码错误修复**：
   - 修复了`news_summarizer.py`中的emoji字符导致的GBK编码错误
   - 为所有print语句添加了`clean_unicode_for_console()`函数包装
   - 更新了`config.py`中的EMOJI_MAP，添加了缺失的emoji字符映射

3. **PyTorch警告优化**：
   - 添加了`TRANSFORMERS_VERBOSITY=error`环境变量设置
   - 改善了tokenizer加载失败时的错误信息
   - 提供了更友好的安装提示

**临时文件命名说明**：
- `tmp_2025-07-12_养老_养老.json`的双重关键词是正确的
- 格式：`tmp_{date}_{main_keyword}_{search_keyword}.json`
- "养老"既是主关键词又是搜索关键词，因此会重复

**解决效果**：
- 修复了Windows环境下的GBK编码错误
- 解决了空列表的错误跳过问题
- 优化了PyTorch相关的警告提示
- 提高了系统的稳定性和可用性

### 2025-01-18 - 核心模块图标管理系统迁移

**迁移内容**：
基于之前创建的跨平台图标管理系统，将核心的抓取、打分、总结三个环节完全迁移到新系统：

1. **fetch_content.py (抓取环节)**：
   - 导入`icon_manager`和`logger_utils`模块
   - 创建`NewsLogger`实例替代传统print输出
   - 新增`content_fetch_start()`和`content_fetch()`专用方法
   - 替换所有emoji print语句为`safe_print()`或logger方法

2. **news_scorer.py (打分环节)**：
   - 导入图标管理系统并创建`scoring_logger`实例
   - 替换所有print语句为`safe_print()`
   - 包括debug信息、错误提示、模型选择等都已安全处理

3. **news_summarizer.py (总结环节)**：
   - 导入图标管理系统并创建`summary_logger`实例
   - 替换所有print语句为`safe_print()`
   - **特别修复了第553行的`optimize_system_prompt.strip()`输出**
   - **修复了`improved_summary.strip()`输出**

**迁移效果**：
- ✅ 彻底解决了Windows GBK编码错误问题
- ✅ 实现了跨平台兼容：Mac显示emoji，Windows显示文本
- ✅ 统一了日志输出格式和样式
- ✅ 提供了更好的错误处理和用户体验
- ✅ 保持了原有功能的完整性

**技术优势**：
- 自动环境检测和主题切换
- 优雅的错误降级处理
- 丰富的日志记录功能
- 高度可配置的输出格式
- 零配置开箱即用

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
- **统一使用DeepSeek V3 (deepseek-chat)**：快速高效的评分模型
**智能策略**：
- **统一模型策略**：所有关键词都使用DeepSeek V3模型，确保评分一致性和速度
- 双关键词机制：模型选择基于主关键词，AI评分使用搜索关键词
- Token超限自动切换到千问模型
- **个性化System Prompt**：支持针对特定关键词使用专门的评分提示词
**评分标准**：相关性、时效性、重要性综合评判
**输出格式**：{date}_{keyword}_scored.json
**最新优化**：
- **统一模型策略**：所有评分任务都使用DeepSeek V3模型，提高评分速度和一致性
- 支持主关键词和搜索关键词的分离处理
- **新增个性化System Prompt功能**：为"江苏省国资委"关键词配置了专门的评分提示词，支持更精准的评分
- 优化了江苏省国资委的评分提示词，增强了对国资委体系新闻的识别能力

### 5. news_summarizer.py - 新闻摘要生成 
**功能**：对高分新闻进行AI总结，生成多轮优化摘要
**处理流程**：
1. **第1轮初稿摘要**：使用DeepSeek Reasoner生成初始摘要
2. **第2-1轮评判官**：质量评估和优化建议
3. **第2-2轮优化**：根据建议优化摘要
4. **第3轮热点追踪**：对比昨天摘要，标注持续热点
**技术特性**：
- **多轮优化机制**：3轮递进式摘要优化，确保质量
- **智能模型切换**：Token超限时自动切换到千问模型
- **热点追踪功能**：识别连续多天的热点新闻
- **个性化过滤**：江苏省国资委使用全源新闻，其他关键词仅用Google News
- **容错机制**：API失败时优雅降级，记录详细日志
- **日志优化**：控制台输出简洁化，移除冗长的prompt显示，详细内容存储在单独文件
**输出格式**：{date}_{keyword}_summary.json（多轮摘要）和optimization_prompts.txt（调试信息）
**最新修复**：
- **修复误报失败问题**：main函数现在明确返回True/False，避免成功执行被误判为失败
- 成功完成所有摘要流程时返回True
- 各种失败情况（API超时、无新闻等）返回False
- 解决了日志中"运行结果: 成功"后仍显示"执行失败"的问题
- **日志输出简化**：控制台只显示关键信息（模型信息、token统计、执行状态），摘要内容只显示前200字符，完整的prompt和response保存在optimization_prompts.txt文件中

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
- **NEW**: KEYWORD_SPECIFIC_SYSTEM_PROMPTS：针对特定关键词的专门System Prompt映射表
**环境变量**：数据库连接、API密钥等敏感信息
**最新更新**：
- 新增NEWS_SCORE_SYSTEM_MSG_JIANGSU_SASAC：专门针对"江苏省国资委"关键词的评分提示词
- 新增KEYWORD_SPECIFIC_SYSTEM_PROMPTS映射表，支持为不同关键词配置专门的评分提示词

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
