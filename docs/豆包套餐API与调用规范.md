# 豆包（火山方舟）套餐 API 与调用规范

> 面向其他项目接入：说明如何正确使用**订阅套餐（Agent Plan）**与可选的**旧按量**双通道。  
> 本文可整份拷贝到目标仓库；密钥勿写入文档，只放 `.env`。

---

## 1. 先分清两条通道（最重要）

火山方舟文本调用至少有两条互不兼容的通道：

| 通道 | 用途 | Base URL | API Key 来源 | 计费 |
|------|------|----------|--------------|------|
| **旧按量** | 传统按量付费 | `https://ark.cn-beijing.volces.com/api/v3` | 控制台「按量/通用」API Key | 现金按 token |
| **订阅套餐（Agent Plan）** | 月付套餐额度 | `https://ark.cn-beijing.volces.com/api/plan/v3` | **Agent Plan 订阅页专属** API Key | **AFP**（Agent Fuel Points） |

### 硬性规则

1. **Key 与 Base URL 必须成对匹配**，禁止混用。  
   - 订阅 Key + `/api/v3` → 常见 **401**  
   - 按量 Key + `/api/plan/v3` → 无法正确扣套餐 / 鉴权失败  
2. Agent Plan **不是** Coding Plan。Coding Plan 有自己的专属地址（常见含 `/api/coding/`）；Agent Plan OpenAI 兼容地址固定为 **`/api/plan/v3`**。  
3. 套餐额度按 **AFP** 消耗，不是「直接按 token 现金价」理解；模型/上下文/多模态权重不同，AFP 消耗不同。  
4. 国内服务器调用豆包时，建议 **直连**（不要误走系统 HTTP 代理），否则易 `Connection error`。

---

## 2. 推荐环境变量命名

在目标项目 `.env` 中建议拆成两组（名称可改，但语义必须分离）：

```env
# —— 可选：旧按量 ——
DOUBAO_API_KEY=你的按量Key
DOUBAO_BASE_URL=https://ark.cn-beijing.volces.com/api/v3

# —— 推荐：Agent Plan 订阅套餐 ——
DOUBAO_PLAN_API_KEY=你的套餐专属Key
DOUBAO_PLAN_BASE_URL=https://ark.cn-beijing.volces.com/api/plan/v3

# 文本模型（OpenAI 兼容 model 字段）
DOUBAO_TEXT_MODEL=doubao-seed-2-1-turbo-260628

# 可选
TEXT_REQUEST_TIMEOUT_SECONDS=120
# 国内直连：不要信任系统代理（默认 0）
DOUBAO_HTTP_TRUST_ENV=0
```

### 仅套餐时

只配 `DOUBAO_PLAN_API_KEY` + `DOUBAO_PLAN_BASE_URL` 即可，不要再把套餐 Key 塞进 `DOUBAO_API_KEY`。

### 双通道互备时（推荐生产）

- 同时配置按量 Key 与套餐 Key  
- **调用顺序建议：先旧按量，失败再套餐**（或按你项目成本策略反过来）  
- 两边都失败：提示「按量可能欠费 / 套餐可能过期或未生效」

---

## 3. 模型选择

| 模型 ID（示例） | 说明 |
|-----------------|------|
| `doubao-seed-2-1-turbo-260628` | **推荐默认（Agent Plan）**：Seed 2.1 Turbo；2.0 Pro/Code 将于 8/8 下线 |
| `doubao-seed-2-1-pro-260628` | Seed 2.1 Pro：更强、更重任务 |
| `doubao-seed-2-0-lite-260428` | 旧 Lite（过渡期仍可能可用，勿作新项目默认） |

注意：

- `model` 填控制台/文档给出的 **接入点 ID 或模型名**（以你账号实际开通为准）。  
- Agent Plan 控制台若提供 Auto/路由类模型名（如部分文档写的 `ark-code-latest`），仅在该套餐支持时使用；日常业务文本建议固定 Lite。  
- ≤32k 语境下，Lite 相对 Pro 大约输入 ~5×、输出 ~4× 更便宜（按量价目），不是「整一个数量级」。

---

## 4. 调用协议：OpenAI 兼容 Chat Completions

Agent Plan 与按量均可用 **OpenAI Python SDK**（`openai` 包）指向方舟 Base URL。

### 4.1 最小可用示例（仅套餐）

```python
import os
from openai import OpenAI

client = OpenAI(
    api_key=os.environ["DOUBAO_PLAN_API_KEY"],
    base_url=os.environ.get(
        "DOUBAO_PLAN_BASE_URL",
        "https://ark.cn-beijing.volces.com/api/plan/v3",
    ),
    timeout=120.0,
)

resp = client.chat.completions.create(
    model=os.environ.get("DOUBAO_TEXT_MODEL", "doubao-seed-2-1-turbo-260628"),
    messages=[
        {"role": "system", "content": "你是简洁的中文助手。"},
        {"role": "user", "content": "用一句话介绍你自己。"},
    ],
    max_tokens=512,
    temperature=0.3,
)
print(resp.choices[0].message.content)
```

### 4.2 多模态（图文）

`messages` 的 `content` 可为列表，含 `image_url`：

```python
messages = [{
    "role": "user",
    "content": [
        {"type": "text", "text": "描述这张图里的菜。"},
        {
            "type": "image_url",
            "image_url": {"url": "data:image/jpeg;base64,...."},
        },
    ],
}]
```

**体积限制（踩坑）：**

- 豆包多模态 `image_url` 常见硬限约 **10 MiB / 张**（不是 OpenAI 的 20 MiB）。  
- 4K PNG 往往超限 → 上传前缩放（建议长边 ≤2048、短边 ≤768）并转 JPEG，压到约 **9 MiB** 以内。  
- 生图原图可保留高清；**仅投喂豆包视觉接口时压缩**。

### 4.3 HTTP 客户端建议（国内）

```python
import httpx
from openai import OpenAI

http_client = httpx.Client(
    timeout=120.0,
    trust_env=False,  # 等价 DOUBAO_HTTP_TRUST_ENV=0：不走系统代理
)
client = OpenAI(
    api_key=plan_key,
    base_url="https://ark.cn-beijing.volces.com/api/plan/v3",
    http_client=http_client,
)
```

---

## 5. 双通道故障转移（参考实现要点）

伪代码：

```text
channels = []
if DOUBAO_API_KEY:      append ("旧按量", client(/api/v3, metered_key))
if DOUBAO_PLAN_API_KEY: append ("订阅套餐", client(/api/plan/v3, plan_key))

for label, client in channels:
    try:
        return client.chat.completions.create(...)
    except 鉴权/欠费/超时/连接类错误:
        记录错误，尝试下一通道

raise "双通道均失败：按量可能欠费，套餐可能过期/未生效"
```

建议把对外接口做成与 OpenAI SDK 同形：

```python
client.chat.completions.create(model=..., messages=..., max_tokens=..., temperature=...)
```

便于业务代码不感知底层是按量还是套餐。

---

## 6. 给另一个项目 Agent 的落地清单

复制本文后，按顺序做：

1. 在火山引擎开通 **Agent Plan**，于订阅/套餐控制台创建 **专属 API Key**（保存一次，勿提交 Git）。  
2. `.env` 写入 `DOUBAO_PLAN_API_KEY` + `DOUBAO_PLAN_BASE_URL=.../api/plan/v3`。  
3. 用 OpenAI SDK 指向该 Base URL；`model` 用账号已开通的 Seed Lite（或控制台给出的 ID）。  
4. 若仍保留旧按量：另配 `DOUBAO_API_KEY` + `/api/v3`，实现故障转移，**两 Key 绝不交叉填错 URL**。  
5. 国内部署：`trust_env=False`，避免代理干扰。  
6. 有多模态：统一做图片压缩封装，防 `OversizeImage` / `InvalidParameter`。  
7. 日志打印当前命中通道名（`旧按量` / `订阅套餐`），方便排障。

---

## 7. 常见错误对照

| 现象 | 常见原因 | 处理 |
|------|----------|------|
| 401 / Unauthorized | 套餐 Key 打了 `/api/v3`，或 Key 过期 | 改 `/api/plan/v3`；换套餐页新 Key |
| 欠费 / AccountOverdue | 按量账户余额不足 | 充值按量，或切到有效套餐通道 |
| 套餐无效 / 额度相关 | AFP 耗尽或订阅过期 | 续订 / 升档；查控制台 AFP |
| Connection error | 走了错误代理或网络阻断 | `trust_env=False`；检查防火墙 |
| OversizeImage | 单图超过约 10 MiB | 缩放转 JPEG 后再传 |
| model 相关报错 | 模型 ID 未开通或写错 | 控制台核对模型/接入点 ID |

---

## 8. 与 Coding Plan / 其他套餐的边界

- **Agent Plan**：`https://ark.cn-beijing.volces.com/api/plan/v3` + Agent Plan Key + AFP。  
- **Coding Plan**：使用 Coding Plan 控制台给出的专属 Base URL / Key，**不要**拿 Agent Plan Key 去打 Coding 地址，反之亦然。  
- **通用按量**：`/api/v3` + 按量 Key。  

第三方客户端（Claude Code、OpenCode、Trae 等）若文档写「方舟」，务必确认页面是 **Agent Plan** 还是 Coding Plan，再填对应 endpoint。

---

## 9. 自检命令（可选）

```bash
# 仅验证套餐通道（勿把 Key 打进日志仓库）
python - <<'PY'
import os
from openai import OpenAI
c = OpenAI(
    api_key=os.environ["DOUBAO_PLAN_API_KEY"],
    base_url="https://ark.cn-beijing.volces.com/api/plan/v3",
)
r = c.chat.completions.create(
    model=os.environ.get("DOUBAO_TEXT_MODEL", "doubao-seed-2-1-turbo-260628"),
    messages=[{"role": "user", "content": "回复：ok"}],
    max_tokens=16,
)
print(r.choices[0].message.content)
PY
```

成功应打出短文本；401 则检查 Key/URL 是否成对。

---

## 10. 参考来源（本规范提炼自）

- 火山方舟 Agent Plan：OpenAI 兼容地址 `/api/plan/v3`，额度单位 AFP。  
- 本仓库实践（`yiyechushi`）：`V2/text_provider.py` 中 `DoubaoFailoverClient`（先按量后套餐）、默认模型 `doubao-seed-2-1-turbo-260628`、`DOUBAO_HTTP_TRUST_ENV=0`、多模态上传前压图。

---

**给 Agent 的一句话：**  
接豆包最新套餐时，只用 **Agent Plan 专属 Key + `https://ark.cn-beijing.volces.com/api/plan/v3`**，走 OpenAI `chat.completions`；切勿与 `/api/v3` 按量 Key 混用。
