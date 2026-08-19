## 当前实现与启动说明

当前已完成数据清洗，清洗后的 SQLite 数据库为后续 FastAPI 的唯一业务数据源。前端和后端的搭建约定、接口合同与页面交互说明见 [doc/前后端搭建计划.md](doc/前后端搭建计划.md)。

### 当前可运行：数据清洗

```powershell
python scripts/clean_data.py
python -m unittest discover -s tests -v
```

执行后会生成：

- `data/clean/sales_clean.sqlite`：后端将读取的清洗数据库。
- `data/quarantine/quarantine.csv`：隔离与告警记录。
- `data/reports/cleaning_report.json`：清洗统计报告。

### 看板完成后的启动方式

以下命令是 `backend/` 和 `frontend/` 完成搭建后使用的本地开发流程；当前仓库尚未初始化这两个目录。

```powershell
# 终端 1：刷新数据并启动 FastAPI
python scripts/clean_data.py
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# 终端 2：启动 React + Vite
cd frontend
npm install
npm run dev
```

前端默认访问 `http://127.0.0.1:5173`，接口文档访问 `http://127.0.0.1:8000/docs`。
