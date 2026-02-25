# 📋 Commands Quick Reference

所有命令在项目根目录下执行：
```bash
cd ~/Projects/job-workflow-oss
```

Python 路径缩写（可加进 shell alias）：
```bash
alias py="/opt/homebrew/Caskroom/miniconda/base/bin/python3"
```

---

## 🚀 日常使用

| 命令 | 说明 |
|------|------|
| `py src/cli.py run` | 完整跑一次：scrape → 简历 → cover letter → PDF → Notion |
| `py src/cli.py retry-day` | 重跑今天所有失败的 job |
| `py src/cli.py retry-day 2026-02-23` | 重跑指定日期所有失败的 job |
| `py src/cli.py retry "https://linkedin.com/jobs/view/123/"` | 重跑单个 job（传 LinkedIn URL） |
| `py src/cli.py status` | 查看今天输出结果 |

### retry 可选参数
```bash
py src/cli.py retry "https://..." \
  --title "AI Engineer" \
  --company "Acme" \
  --location "San Francisco" \
  --category ai        # sde 或 ai
```

---

## ⚙️ 配置

| 命令 | 说明 |
|------|------|
| `py src/cli.py model codex` | 切换到 Codex |
| `py src/cli.py model claude` | 切换到 Claude CLI |
| `grep LLM_MODE .env` | 查看当前模型 |

搜索关键词、目标数量 → 编辑 `config/search_config.json`

---

## 🔧 维护

```bash
# 清空 seen jobs（让 scraper 重新发现所有职位）
echo '{"seen_ids": []}' > data/seen_jobs.json

# 查看 cron 任务（每天 9:00 自动跑）
crontab -l

# 实时查看日志
tail -f logs/workflow.log

# 查看历史日志
cat logs/workflow.log | grep "2026-02-23"
```

---

## 📁 关键文件

```
.env                        # Token、模型配置（不上传 git）
config/search_config.json   # 搜索关键词、城市、目标数量
config/candidate.txt        # 个人简介（不上传 git）
resume/base_resume.html     # 基础简历模板
data/seen_jobs.json         # 已处理的 job ID（不重复处理）
data/jobs_YYYY-MM-DD.json   # 每日 job 清单（retry-day 依赖）
resume/output/YYYY-MM-DD/   # 每日输出（简历 + cover letter + PDF）
logs/workflow.log           # 完整运行日志
```
