from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

def grab_cnstock(driver):
    try:
        content_elem = driver.find_element(By.XPATH, "//div[starts-with(@class, 'index_wrap__')]")
        ps = content_elem.find_elements(By.TAG_NAME, "p")
        text = "\n".join([p.text.strip() for p in ps if p.text.strip()])
        if text:
            return text, 'custom'
        text = content_elem.get_attribute('innerText').strip()
        if text:
            return text, 'custom'
    except Exception as e:
        print(f"[调试] cnstock.com 定制化抓取异常: {e}")
    return '', 'custom'

def grab_jfdaily(driver):
    try:
        # 先尝试 news-content
        try:
            content_elem = driver.find_element(By.CSS_SELECTOR, "div.news-content")
        except Exception:
            content_elem = None
        # 如果没找到，再尝试 newscontents
        if not content_elem:
            try:
                content_elem = driver.find_element(By.CSS_SELECTOR, "div.newscontents")
            except Exception:
                content_elem = None
        if content_elem:
            ps = content_elem.find_elements(By.TAG_NAME, "p")
            text = "\n".join([p.text.strip() for p in ps if p.text.strip()])
            if text:
                return text, 'custom'
            text = content_elem.text.strip()
            if text:
                return text, 'custom'
    except Exception as e:
        print(f"[调试] jfdaily.com 定制化抓取异常: {e}")
    return '', 'custom'

def grab_msn_cn(driver):
    try:
        # 最多等10秒，直到div.article-body.polished出现
        content_elem = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.article-body.polished"))
        )
        ps = content_elem.find_elements(By.TAG_NAME, "p")
        text = "\n".join([p.text.strip() for p in ps if p.text.strip()])
        if text:
            return text, 'custom'
        text = content_elem.text.strip()
        if text:
            return text, 'custom'
    except Exception as e:
        print(f"[调试] msn.cn 定制化抓取异常: {e}")
    return '', 'custom'

def grab_msn_cn_playwright(page):
    from bs4 import BeautifulSoup
    try:
        # 伪装user-agent，禁用webdriver
        page.context.set_extra_http_headers({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
        })
        page.evaluate("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page.goto(page.url, timeout=60000)
        # 自动处理cookie/同意弹窗
        try:
            page.click('button:has-text("同意")', timeout=3000)
        except Exception:
            pass
        # 模拟滚动触发懒加载
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(3000)
        # 检查iframe
        frames = page.frames
        for frame in frames:
            try:
                html = frame.content()
                soup = BeautifulSoup(html, 'html.parser')
                content_div = soup.find('div', class_='article-body polished') or \
                              soup.find('div', class_=lambda x: x and 'article-body' in x)
                if content_div:
                    ps = content_div.find_all('p')
                    text = '\n'.join([p.get_text(strip=True) for p in ps if p.get_text(strip=True)])
                    if text and len(text) > 50:
                        # 输出iframe渲染后HTML供分析
                        with open('output/msn_playwright_debug.html', 'w', encoding='utf-8') as f:
                            f.write(html)
                        return text, 'custom'
                    text = content_div.get_text(separator='\n', strip=True)
                    if text and len(text) > 50:
                        with open('output/msn_playwright_debug.html', 'w', encoding='utf-8') as f:
                            f.write(html)
                        return text, 'custom'
            except Exception as e:
                print(f'[调试] iframe抓取异常: {e}')
        # 回到主页面兜底
        html = page.content()
        with open('output/msn_playwright_debug.html', 'w', encoding='utf-8') as f:
            f.write(html)
        soup = BeautifulSoup(html, 'html.parser')
        content_div = soup.find('div', class_='article-body polished') or \
                      soup.find('div', class_=lambda x: x and 'article-body' in x)
        if content_div:
            ps = content_div.find_all('p')
            text = '\n'.join([p.get_text(strip=True) for p in ps if p.get_text(strip=True)])
            if text and len(text) > 50:
                return text, 'custom'
            text = content_div.get_text(separator='\n', strip=True)
            if text and len(text) > 50:
                return text, 'custom'
    except Exception as e:
        print(f'[调试] msn.cn Playwright定制化抓取异常: {e}')
    return '', 'custom'

def grab_yicai(driver):
    try:
        content_elem = driver.find_element(By.CSS_SELECTOR, "div.m-text")
        ps = content_elem.find_elements(By.TAG_NAME, "p")
        text = "\n".join([p.text.strip() for p in ps if p.text.strip()])
        if text:
            return text, 'custom'
        text = content_elem.text.strip()
        if text:
            return text, 'custom'
    except Exception as e:
        print(f"[调试] yicai.com 定制化抓取异常: {e}")
    return '', 'custom'

# 注册表：每项是 (匹配函数, 抓取函数)
CUSTOM_GRAB_RULES = [
    (lambda url: 'yicai.com' in url, grab_yicai),
    (lambda url: 'm.cnstock.com' in url or 'cnstock.com' in url, grab_cnstock),
    (lambda url: 'jfdaily.com' in url, grab_jfdaily),
    (lambda url: 'msn.cn' in url, grab_msn_cn),
    # 可继续添加更多定制化规则
] 