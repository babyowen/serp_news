I will modify `config.py` to add the new main keyword, the 17 search keywords, and a customized AI scoring prompt.

### 1. Update `SEARCH_KEYWORDS` in `config.py`
Add the "潜在招标客户一" category with the 17 specified organizations:
```python
"潜在招标客户一": [
    "南京市鼓楼区总工会",
    "江苏省科学技术情报研究所",
    "江苏省科学技术发展战略研究院",
    "江苏省生产力促进中心",
    "江苏省妇联新媒体中心",
    "江苏省纺织产品质量监督检验研究院",
    "江苏省环境科学研究院",
    "江苏省高校招生就业指导服务中心",
    "江苏省农村经济产业指导服务中心",
    "江苏省市场监督管理局机关服务中心",
    "南京市计量监督检测院",
    "南京市土地矿产市场管理中心",
    "南京市规划和自然资源局财务中心",
    "南京信息职业技术学院",
    "南京市建筑工程质量安全检测中心",
    "南京旅游职业学院",
    "南京大学教育发展基金会"
]
```

### 2. Create Custom System Prompt `NEWS_SCORE_SYSTEM_MSG_POTENTIAL_BIDDERS`
I will define a new prompt designed to score news about these units highly (4+), as requested.

**Draft Prompt Logic:**
*   **Role**: Potential Client Intelligence Analyst.
*   **Scoring Criteria**:
    *   **5 points (Core/Important)**: Major events, bidding/procurement notices, strategic cooperation, or significant achievements directly involving the keyword unit.
    *   **4 points (Relevant News)**: General news updates, activity reports, or official announcements where the keyword unit is the main subject.
    *   **3 points (Mentioned)**: The unit is mentioned in a relevant context but is not the sole focus.
    *   **2 points (Weak)**: Casual mention or background reference.
    *   **1 point (Invalid)**: Non-news (ads), non-mainland.
    *   **0 point**: Irrelevant.
*   **Key Instruction**: If it is a news item and directly related to the unit, score it at least 4.

### 3. Register the Prompt
Update `KEYWORD_SPECIFIC_SYSTEM_PROMPTS` to map `"潜在招标客户一"` to the new system prompt.

```python
KEYWORD_SPECIFIC_SYSTEM_PROMPTS = {
    # ... existing items ...
    "潜在招标客户一": NEWS_SCORE_SYSTEM_MSG_POTENTIAL_BIDDERS,
}
```