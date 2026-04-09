"""
HTML Hub — 一个简单的 Python 服务器，用于发布、管理和分享 AI 生成的 HTML 页面。
"""

import asyncio
import json
import logging
import os
import secrets
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import bcrypt
import httpx
import yaml

logger = logging.getLogger("htmlhub")
from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, TimestampSigner

# ---------------------------------------------------------------------------
# 初始化
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
PAGES_DIR = BASE_DIR / "pages"
TEMPLATES_DIR = BASE_DIR / "templates"
SUPER_ADMIN_EMAIL = "__super_admin__"

DATA_DIR.mkdir(exist_ok=True)
PAGES_DIR.mkdir(exist_ok=True)

# 加载配置
with open(BASE_DIR / "config.yaml", "r", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

SUPER_ADMIN_TOKEN = CONFIG.get("admin_token", "")
SERVER_CONFIG = CONFIG.get("server", {})
BASE_URL = SERVER_CONFIG.get("base_url", "http://localhost:8888")
SESSION_SECRET = SERVER_CONFIG.get("session_secret", secrets.token_hex(32))

# 注册模式: "open" 允许任何人注册, "closed" 禁止注册
REGISTRATION_CONFIG = CONFIG.get("registration", {})
REGISTRATION_MODE = REGISTRATION_CONFIG.get("mode", "open")

# OpenClaw 配置
OPENCLAW_CONFIG = CONFIG.get("openclaw", {})
OPENCLAW_WEBHOOK_URL = OPENCLAW_CONFIG.get("webhook_url", "")
OPENCLAW_WEBHOOK_TOKEN = OPENCLAW_CONFIG.get("webhook_token", "")

signer = TimestampSigner(SESSION_SECRET)

app = FastAPI(title="HTML Hub", docs_url=None, redoc_url=None)
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
EVENT_SUBSCRIBERS = set()
EVENT_HISTORY = []
EVENT_HISTORY_LIMIT = 200
APPROVAL_SUBSCRIBERS = {}

# ---------------------------------------------------------------------------
# 数据读写
# ---------------------------------------------------------------------------

INDEX_FILE = DATA_DIR / "index.json"
VISITORS_FILE = DATA_DIR / "visitors.json"
USERS_FILE = DATA_DIR / "users.json"


def _normalize_page_owners(index: dict) -> bool:
    changed = False
    for page in index.get("pages", []):
        if not page.get("owner"):
            # Legacy pages predate multi-owner support; treat them as explicit
            # super-admin-owned pages until someone reassigns them.
            page["owner"] = SUPER_ADMIN_EMAIL
            changed = True
    return changed


def read_index() -> dict:
    if INDEX_FILE.exists():
        data = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
        if _normalize_page_owners(data):
            write_index(data)
        return data
    return {"pages": []}


def write_index(data: dict):
    INDEX_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_visitors() -> dict:
    if VISITORS_FILE.exists():
        return json.loads(VISITORS_FILE.read_text(encoding="utf-8"))
    return {"visitors": []}


def write_visitors(data: dict):
    VISITORS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_users() -> dict:
    if USERS_FILE.exists():
        return json.loads(USERS_FILE.read_text(encoding="utf-8"))
    return {"users": []}


def write_users(data: dict):
    USERS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def find_user_by_token(token: str) -> Optional[dict]:
    """通过 token 查找用户"""
    users_data = read_users()
    for u in users_data["users"]:
        if u["token"] == token:
            return u
    return None


def find_user_by_email(email: str) -> Optional[dict]:
    """通过邮箱查找用户"""
    users_data = read_users()
    for u in users_data["users"]:
        if u["email"] == email:
            return u
    return None


def find_page(slug: str) -> Optional[dict]:
    index = read_index()
    for page in index["pages"]:
        if page["slug"] == slug:
            return page
    return None


def find_visitor(visitor_id: str) -> Optional[dict]:
    visitors = read_visitors()
    for v in visitors["visitors"]:
        if v["id"] == visitor_id:
            return v
    return None


# ---------------------------------------------------------------------------
# Session 管理
# ---------------------------------------------------------------------------

SESSION_COOKIE = "htmlhub_session"
SESSION_MAX_AGE = 30 * 24 * 3600  # 30 天


def get_session_visitor(request: Request) -> Optional[dict]:
    """从 Cookie 中解析当前登录的访客"""
    cookie = request.cookies.get(SESSION_COOKIE)
    if not cookie:
        return None
    try:
        visitor_id = signer.unsign(cookie, max_age=SESSION_MAX_AGE).decode()
        return find_visitor(visitor_id)
    except BadSignature:
        return None


def set_session_cookie(response: Response, visitor_id: str):
    signed = signer.sign(visitor_id).decode()
    response.set_cookie(
        SESSION_COOKIE,
        signed,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
    )


# ---------------------------------------------------------------------------
# OpenClaw 通知推送
# ---------------------------------------------------------------------------


def build_access_event(page: dict, visitor: dict) -> dict:
    provider_name = {"dingtalk": "钉钉", "wechat": "微信"}.get(
        visitor.get("provider", ""), visitor.get("provider", "未知")
    )
    event = {
        "id": f"req_{int(time.time() * 1000)}_{secrets.token_hex(4)}",
        "type": "access_requested",
        "page": {
            "slug": page["slug"],
            "title": page["title"],
        },
        "visitor": {
            "id": visitor["id"],
            "name": visitor["name"],
            "provider": visitor.get("provider", ""),
            "provider_name": provider_name,
        },
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "message": (
            f"有人想查看你的页面\n"
            f"页面：{page['title']} ({page['slug']})\n"
            f"访客：{visitor['name']}（{provider_name}登录）\n"
            f"访客ID：{visitor['id']}\n\n要通过吗？"
        ),
    }
    return event


async def publish_event(event: dict):
    EVENT_HISTORY.append(event)
    if len(EVENT_HISTORY) > EVENT_HISTORY_LIMIT:
        del EVENT_HISTORY[:-EVENT_HISTORY_LIMIT]

    stale = []
    for queue in list(EVENT_SUBSCRIBERS):
        try:
            queue.put_nowait(event)
        except Exception:
            stale.append(queue)
    for queue in stale:
        EVENT_SUBSCRIBERS.discard(queue)


def _approval_key(slug: str, visitor_id: str) -> str:
    return f"{slug}:{visitor_id}"


async def publish_approval_event(slug: str, visitor_id: str, status: str):
    key = _approval_key(slug, visitor_id)
    payload = {
        "slug": slug,
        "visitor_id": visitor_id,
        "status": status,
        "published_at": time.time(),
    }
    queues = list(APPROVAL_SUBSCRIBERS.get(key, set()))
    stale = []
    for queue in queues:
        try:
            queue.put_nowait(payload)
        except Exception:
            stale.append(queue)
    if stale and key in APPROVAL_SUBSCRIBERS:
        for queue in stale:
            APPROVAL_SUBSCRIBERS[key].discard(queue)
        if not APPROVAL_SUBSCRIBERS[key]:
            APPROVAL_SUBSCRIBERS.pop(key, None)


async def notify_openclaw(page: dict, visitor: dict):
    """通过 OpenClaw webhook 推送，或通过 SSE 广播事件"""
    event = build_access_event(page, visitor)
    await publish_event(event)

    if not OPENCLAW_WEBHOOK_URL:
        logger.info("OpenClaw webhook 未配置，已仅广播到 SSE 订阅者")
        return

    payload = {
        "message": event["message"],
        "event": event,
        "name": "htmlhub-client",
        "deliver": True,
    }

    headers = {}
    if OPENCLAW_WEBHOOK_TOKEN:
        headers["Authorization"] = f"Bearer {OPENCLAW_WEBHOOK_TOKEN}"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                OPENCLAW_WEBHOOK_URL, json=payload, headers=headers
            )
            if resp.status_code < 300:
                logger.info(f"OpenClaw 通知已推送：{visitor['name']} → {page['slug']}")
            else:
                logger.warning(f"OpenClaw 通知推送失败: {resp.status_code} {resp.text}")
    except Exception as e:
        logger.warning(f"OpenClaw 通知推送异常: {e}")


# ---------------------------------------------------------------------------
# Admin 鉴权
# ---------------------------------------------------------------------------

# 超级管理员虚拟用户（用于向后兼容 config.yaml 中的 admin_token）
SUPER_ADMIN_USER = {
    "email": SUPER_ADMIN_EMAIL,
    "token": SUPER_ADMIN_TOKEN,
    "role": "super_admin",
    "created_at": "",
}


def _extract_token(request: Request) -> str:
    """从 Authorization header 或 query param 中提取 token"""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return request.query_params.get("token", "")


def verify_admin(request: Request) -> dict:
    """验证管理员身份，返回用户 dict"""
    token = _extract_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized")

    # 超级管理员 token（向后兼容）
    if SUPER_ADMIN_TOKEN and token == SUPER_ADMIN_TOKEN:
        return SUPER_ADMIN_USER

    # 通过 token 查找注册用户
    user = find_user_by_token(token)
    if user:
        return user

    raise HTTPException(status_code=401, detail="Unauthorized")


def _is_super_admin(user: dict) -> bool:
    return user.get("role") == "super_admin"


def _page_owner(page: dict) -> str:
    return page.get("owner") or SUPER_ADMIN_EMAIL


def _resolve_owner_email(owner: str) -> str:
    normalized = owner.strip().lower()
    if not normalized:
        raise HTTPException(400, "owner required")
    if normalized == SUPER_ADMIN_EMAIL:
        return SUPER_ADMIN_EMAIL
    target = find_user_by_email(normalized)
    if not target:
        raise HTTPException(
            400,
            f"owner must be an existing user email or {SUPER_ADMIN_EMAIL}",
        )
    return target["email"]


def _user_owns_page(user: dict, page: dict) -> bool:
    """检查用户是否拥有该页面（超级管理员拥有所有页面）"""
    if _is_super_admin(user):
        return True
    return _page_owner(page) == user["email"]


def _get_user_pages(user: dict, index: dict) -> list:
    """获取用户拥有的所有页面"""
    if _is_super_admin(user):
        return index["pages"]
    return [p for p in index["pages"] if _page_owner(p) == user["email"]]


def _require_page_owner(user: dict, page: dict):
    """要求用户是页面的所有者，否则 403"""
    if not _user_owns_page(user, page):
        raise HTTPException(status_code=403, detail="You don't own this page")


# ---------------------------------------------------------------------------
# 目录页生成
# ---------------------------------------------------------------------------


def regenerate_index_page():
    """重新生成公开目录页"""
    index = read_index()
    public_pages = [p for p in index["pages"] if p.get("access") == "public"]
    public_pages.sort(key=lambda p: p.get("created_at", ""), reverse=True)
    html = templates.get_template("index.html").render(
        pages=public_pages, base_url=BASE_URL
    )
    (PAGES_DIR / "index.html").write_text(html, encoding="utf-8")


# ---------------------------------------------------------------------------
# 路由：公开目录
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def home():
    index_file = PAGES_DIR / "index.html"
    if index_file.exists():
        return HTMLResponse(index_file.read_text(encoding="utf-8"))
    # 首次启动，还没有页面
    regenerate_index_page()
    return HTMLResponse((PAGES_DIR / "index.html").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 用户注册 / 登录
# ---------------------------------------------------------------------------


@app.post("/api/auth/register")
async def register(request: Request):
    """注册新用户，返回 token"""
    if REGISTRATION_MODE != "open":
        raise HTTPException(status_code=403, detail="Registration is closed")

    body = await request.json()
    email = body.get("email", "").strip().lower()
    password = body.get("password", "")

    if not email or not password:
        raise HTTPException(400, "email and password required")
    if len(password) < 6:
        raise HTTPException(400, "password must be at least 6 characters")

    if find_user_by_email(email):
        raise HTTPException(409, "Email already registered")

    # 生成 token 和密码哈希
    token = f"uhub_{secrets.token_hex(24)}"
    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    user = {
        "email": email,
        "password_hash": password_hash,
        "token": token,
        "role": "admin",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    users_data = read_users()
    users_data["users"].append(user)
    write_users(users_data)

    logger.info(f"新用户注册: {email}")
    return {"ok": True, "token": token, "email": email}


@app.post("/api/auth/login")
async def login(request: Request):
    """登录获取 token"""
    body = await request.json()
    email = body.get("email", "").strip().lower()
    password = body.get("password", "")

    if not email or not password:
        raise HTTPException(400, "email and password required")

    user = find_user_by_email(email)
    if not user:
        raise HTTPException(401, "Invalid email or password")

    if not bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
        raise HTTPException(401, "Invalid email or password")

    return {"ok": True, "token": user["token"], "email": user["email"]}


# ---------------------------------------------------------------------------
# 路由：管理后台
# ---------------------------------------------------------------------------


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, user=Depends(verify_admin)):
    index = read_index()
    visitors_data = read_visitors()

    # 只显示当前用户拥有的页面
    my_pages = _get_user_pages(user, index)

    # 按分类分组
    categories: dict[str, list] = {}
    for page in my_pages:
        cat = page.get("category", "未分类")
        categories.setdefault(cat, []).append(page)

    # 为每个页面附加访客信息
    for page in my_pages:
        slug = page["slug"]
        approved = [
            v for v in visitors_data["visitors"]
            if slug in v.get("approved_pages", [])
        ]
        pending = [
            v for v in visitors_data["visitors"]
            if slug in v.get("pending_pages", [])
        ]
        page["_approved_visitors"] = approved
        page["_pending_visitors"] = pending

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "categories": categories,
        "base_url": BASE_URL,
        "admin_token": user["token"],
    })


# ---------------------------------------------------------------------------
# API：发布
# ---------------------------------------------------------------------------


@app.post("/api/publish")
async def publish(
    request: Request,
    slug: str = Form(...),
    title: str = Form(...),
    category: str = Form("未分类"),
    access: str = Form("public"),
    description: str = Form(""),
    file: UploadFile = File(...),
    user=Depends(verify_admin),
):
    if access not in ("public", "approval", "private"):
        raise HTTPException(400, "access must be public, approval, or private")

    # 更新索引
    index = read_index()
    # 如果已存在则更新（必须是自己的页面）
    existing = None
    for p in index["pages"]:
        if p["slug"] == slug:
            existing = p
            break

    now = datetime.now(timezone.utc).isoformat()
    if existing:
        _require_page_owner(user, existing)
    else:
        page_dir = PAGES_DIR / slug
        if page_dir.exists():
            raise HTTPException(
                409,
                "Page directory already exists; reindex and assign an owner before publishing",
            )

    # 权限校验完成后再写 HTML，避免跨 owner 覆盖现有页面文件。
    page_dir = PAGES_DIR / slug
    page_dir.mkdir(parents=True, exist_ok=True)
    content = await file.read()
    (page_dir / "index.html").write_bytes(content)

    if existing:
        existing["title"] = title
        existing["category"] = category
        existing["access"] = access
        existing["description"] = description
        existing["updated_at"] = now
    else:
        index["pages"].append({
            "slug": slug,
            "title": title,
            "category": category,
            "access": access,
            "description": description,
            "owner": user["email"],
            "created_at": now,
            "updated_at": now,
        })

    write_index(index)
    regenerate_index_page()

    return {"ok": True, "url": f"{BASE_URL}/{slug}"}


@app.post("/api/reindex")
async def reindex(user=Depends(verify_admin)):
    """扫描 pages 目录，重建索引（新发现的页面归当前用户所有）"""
    index = read_index()
    existing_slugs = {p["slug"] for p in index["pages"]}
    now = datetime.now(timezone.utc).isoformat()

    for entry in PAGES_DIR.iterdir():
        if entry.is_dir() and (entry / "index.html").exists():
            slug = entry.name
            if slug not in existing_slugs:
                index["pages"].append({
                    "slug": slug,
                    "title": slug,
                    "category": "未分类",
                    "access": "public",
                    "description": "",
                    "owner": SUPER_ADMIN_EMAIL,
                    "created_at": now,
                    "updated_at": now,
                })

    write_index(index)
    regenerate_index_page()
    return {"ok": True, "pages": len(index["pages"])}


# ---------------------------------------------------------------------------
# API：页面管理
# ---------------------------------------------------------------------------


@app.put("/api/pages/{slug}")
async def update_page(slug: str, request: Request, user=Depends(verify_admin)):
    body = await request.json()
    index = read_index()
    page = None
    for p in index["pages"]:
        if p["slug"] == slug:
            page = p
            break
    if not page:
        raise HTTPException(404, "Page not found")

    _require_page_owner(user, page)

    for key in ("title", "category", "access", "description"):
        if key in body:
            page[key] = body[key]
    if "owner" in body:
        if not _is_super_admin(user):
            raise HTTPException(403, "Only super admin can change owner")
        page["owner"] = _resolve_owner_email(body["owner"])
    page["updated_at"] = datetime.now(timezone.utc).isoformat()

    write_index(index)
    regenerate_index_page()
    return {"ok": True}


@app.delete("/api/pages/{slug}")
async def delete_page(slug: str, user=Depends(verify_admin)):
    index = read_index()
    page = None
    for p in index["pages"]:
        if p["slug"] == slug:
            page = p
            break
    if not page:
        raise HTTPException(404, "Page not found")

    _require_page_owner(user, page)

    index["pages"] = [p for p in index["pages"] if p["slug"] != slug]
    write_index(index)

    page_dir = PAGES_DIR / slug
    if page_dir.exists():
        shutil.rmtree(page_dir)

    regenerate_index_page()
    return {"ok": True}


# ---------------------------------------------------------------------------
# API：访客管理
# ---------------------------------------------------------------------------


def _find_page_or_404(slug: str, user: dict) -> dict:
    """查找页面并验证所有权，否则 404/403"""
    page = find_page(slug)
    if not page:
        raise HTTPException(404, "Page not found")
    _require_page_owner(user, page)
    return page


@app.get("/api/pages/{slug}/visitors")
async def get_visitors(slug: str, user=Depends(verify_admin)):
    _find_page_or_404(slug, user)
    visitors_data = read_visitors()
    approved = [
        v for v in visitors_data["visitors"]
        if slug in v.get("approved_pages", [])
    ]
    pending = [
        v for v in visitors_data["visitors"]
        if slug in v.get("pending_pages", [])
    ]
    return {"approved": approved, "pending": pending}


@app.post("/api/pages/{slug}/approve")
async def approve_visitor(slug: str, request: Request, user=Depends(verify_admin)):
    _find_page_or_404(slug, user)
    body = await request.json()
    visitor_id = body.get("visitor_id")
    if not visitor_id:
        raise HTTPException(400, "visitor_id required")

    visitors_data = read_visitors()
    for v in visitors_data["visitors"]:
        if v["id"] == visitor_id:
            if slug in v.get("pending_pages", []):
                v["pending_pages"].remove(slug)
            if slug not in v.get("approved_pages", []):
                v.setdefault("approved_pages", []).append(slug)
            break
    else:
        raise HTTPException(404, "Visitor not found")

    write_visitors(visitors_data)
    await publish_approval_event(slug, visitor_id, "approved")
    return {"ok": True}


@app.post("/api/pages/{slug}/reject")
async def reject_visitor(slug: str, request: Request, user=Depends(verify_admin)):
    _find_page_or_404(slug, user)
    body = await request.json()
    visitor_id = body.get("visitor_id")
    if not visitor_id:
        raise HTTPException(400, "visitor_id required")

    visitors_data = read_visitors()
    for v in visitors_data["visitors"]:
        if v["id"] == visitor_id:
            if slug in v.get("pending_pages", []):
                v["pending_pages"].remove(slug)
            break

    write_visitors(visitors_data)
    await publish_approval_event(slug, visitor_id, "rejected")
    return {"ok": True}


@app.delete("/api/pages/{slug}/visitors/{visitor_id}")
async def revoke_visitor(slug: str, visitor_id: str, user=Depends(verify_admin)):
    _find_page_or_404(slug, user)
    visitors_data = read_visitors()
    for v in visitors_data["visitors"]:
        if v["id"] == visitor_id:
            if slug in v.get("approved_pages", []):
                v["approved_pages"].remove(slug)
            break

    write_visitors(visitors_data)
    return {"ok": True}


# ---------------------------------------------------------------------------
# OAuth：钉钉
# ---------------------------------------------------------------------------


@app.get("/auth/dingtalk")
async def dingtalk_login(request: Request, redirect: str = Query("")):
    """跳转到钉钉 OAuth 授权页"""
    dt_config = CONFIG.get("dingtalk", {})
    app_key = dt_config.get("app_key", "")
    if not app_key:
        raise HTTPException(500, "DingTalk OAuth not configured")

    state = f"{redirect}|{secrets.token_hex(8)}"
    # 钉钉新版 OAuth2.0
    auth_url = (
        "https://login.dingtalk.com/oauth2/auth?"
        f"redirect_uri={BASE_URL}/auth/dingtalk/callback"
        f"&response_type=code"
        f"&client_id={app_key}"
        f"&scope=openid"
        f"&prompt=consent"
        f"&state={state}"
    )
    return RedirectResponse(auth_url)


@app.get("/auth/dingtalk/callback")
async def dingtalk_callback(
    request: Request,
    code: str = Query(""),
    state: str = Query(""),
):
    """钉钉 OAuth 回调"""
    dt_config = CONFIG.get("dingtalk", {})
    app_key = dt_config.get("app_key", "")
    app_secret = dt_config.get("app_secret", "")

    if not code:
        raise HTTPException(400, "Missing code")

    # 用 code 换取 user access token
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            "https://api.dingtalk.com/v1.0/oauth2/userAccessToken",
            json={
                "clientId": app_key,
                "clientSecret": app_secret,
                "code": code,
                "grantType": "authorization_code",
            },
        )
        if token_resp.status_code != 200:
            raise HTTPException(502, "Failed to get DingTalk token")
        token_data = token_resp.json()
        access_token = token_data.get("accessToken", "")

        # 获取用户信息
        user_resp = await client.get(
            "https://api.dingtalk.com/v1.0/contact/users/me",
            headers={"x-acs-dingtalk-access-token": access_token},
        )
        if user_resp.status_code != 200:
            raise HTTPException(502, "Failed to get DingTalk user info")
        user_data = user_resp.json()

    visitor_id = f"dingtalk_{user_data.get('openId', '')}"
    name = user_data.get("nick", "钉钉用户")
    avatar = user_data.get("avatarUrl", "")

    # 解析 redirect 目标
    redirect_slug = ""
    if state and "|" in state:
        redirect_slug = state.split("|")[0]

    # 注册或更新访客
    return await _register_visitor_and_redirect(
        request, visitor_id, "dingtalk", name, avatar, redirect_slug
    )


# ---------------------------------------------------------------------------
# OAuth：微信
# ---------------------------------------------------------------------------


@app.get("/auth/wechat")
async def wechat_login(request: Request, redirect: str = Query("")):
    """跳转到微信 OAuth 授权页"""
    wx_config = CONFIG.get("wechat", {})
    app_id = wx_config.get("app_id", "")
    if not app_id:
        raise HTTPException(500, "WeChat OAuth not configured")

    state = f"{redirect}|{secrets.token_hex(8)}"
    auth_url = (
        "https://open.weixin.qq.com/connect/qrconnect?"
        f"appid={app_id}"
        f"&redirect_uri={BASE_URL}/auth/wechat/callback"
        f"&response_type=code"
        f"&scope=snsapi_login"
        f"&state={state}"
        "#wechat_redirect"
    )
    return RedirectResponse(auth_url)


@app.get("/auth/wechat/callback")
async def wechat_callback(
    request: Request,
    code: str = Query(""),
    state: str = Query(""),
):
    """微信 OAuth 回调"""
    wx_config = CONFIG.get("wechat", {})
    app_id = wx_config.get("app_id", "")
    app_secret = wx_config.get("app_secret", "")

    if not code:
        raise HTTPException(400, "Missing code")

    async with httpx.AsyncClient() as client:
        # 用 code 换取 access_token + openid
        token_resp = await client.get(
            "https://api.weixin.qq.com/sns/oauth2/access_token",
            params={
                "appid": app_id,
                "secret": app_secret,
                "code": code,
                "grant_type": "authorization_code",
            },
        )
        token_data = token_resp.json()
        access_token = token_data.get("access_token", "")
        openid = token_data.get("openid", "")

        if not openid:
            raise HTTPException(502, "Failed to get WeChat token")

        # 获取用户信息
        user_resp = await client.get(
            "https://api.weixin.qq.com/sns/userinfo",
            params={"access_token": access_token, "openid": openid},
        )
        user_data = user_resp.json()

    visitor_id = f"wechat_{openid}"
    name = user_data.get("nickname", "微信用户")
    avatar = user_data.get("headimgurl", "")

    redirect_slug = ""
    if state and "|" in state:
        redirect_slug = state.split("|")[0]

    return await _register_visitor_and_redirect(
        request, visitor_id, "wechat", name, avatar, redirect_slug
    )


# ---------------------------------------------------------------------------
# 访客注册 + 重定向（OAuth 回调共用）
# ---------------------------------------------------------------------------


async def _register_visitor_and_redirect(
    request: Request,
    visitor_id: str,
    provider: str,
    name: str,
    avatar: str,
    redirect_slug: str,
) -> Response:
    visitors_data = read_visitors()
    visitor = None
    for v in visitors_data["visitors"]:
        if v["id"] == visitor_id:
            visitor = v
            v["name"] = name
            v["avatar"] = avatar
            break

    if not visitor:
        visitor = {
            "id": visitor_id,
            "provider": provider,
            "name": name,
            "avatar": avatar,
            "first_seen": datetime.now(timezone.utc).isoformat(),
            "approved_pages": [],
            "pending_pages": [],
            "blocked": False,
        }
        visitors_data["visitors"].append(visitor)

    # 如果有 redirect_slug，自动提交访问申请
    should_notify = False
    notify_page = None
    if redirect_slug:
        notify_page = find_page(redirect_slug)
        if notify_page and notify_page.get("access") == "approval":
            if (
                redirect_slug not in visitor.get("approved_pages", [])
                and redirect_slug not in visitor.get("pending_pages", [])
            ):
                visitor.setdefault("pending_pages", []).append(redirect_slug)
                should_notify = True

    write_visitors(visitors_data)

    # 推送 OpenClaw 通知
    if should_notify and notify_page:
        await notify_openclaw(notify_page, visitor)

    # 设置 session cookie 并重定向
    redirect_url = f"/{redirect_slug}" if redirect_slug else "/"
    response = RedirectResponse(redirect_url, status_code=302)
    set_session_cookie(response, visitor_id)
    return response


# ---------------------------------------------------------------------------
# API：轮询审批状态
# ---------------------------------------------------------------------------


@app.get("/api/check-approval/{slug}")
async def check_approval(slug: str, request: Request):
    visitor = get_session_visitor(request)
    if not visitor:
        return {"status": "not_logged_in"}
    if slug in visitor.get("approved_pages", []):
        return {"status": "approved"}
    if slug in visitor.get("pending_pages", []):
        return {"status": "pending"}
    return {"status": "not_requested"}


@app.get("/api/check-approval/stream/{slug}")
async def check_approval_stream(slug: str, request: Request):
    visitor = get_session_visitor(request)
    if not visitor:
        async def not_logged_in():
            yield 'event: status\ndata: {"status":"not_logged_in"}\n\n'
        return StreamingResponse(not_logged_in(), media_type="text/event-stream")

    visitor_id = visitor["id"]
    key = _approval_key(slug, visitor_id)

    async def generate():
        queue = asyncio.Queue()
        APPROVAL_SUBSCRIBERS.setdefault(key, set()).add(queue)
        try:
            if slug in visitor.get("approved_pages", []):
                yield 'event: status\ndata: {"status":"approved"}\n\n'
                return
            if slug not in visitor.get("pending_pages", []):
                yield 'event: status\ndata: {"status":"not_requested"}\n\n'
                return
            yield 'event: status\ndata: {"status":"pending"}\n\n'
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=55)
                    yield f'event: status\ndata: {json.dumps(event, ensure_ascii=False)}\n\n'
                    if event.get("status") in ("approved", "rejected"):
                        break
                except asyncio.TimeoutError:
                    yield ': ping\n\n'
        finally:
            if key in APPROVAL_SUBSCRIBERS:
                APPROVAL_SUBSCRIBERS[key].discard(queue)
                if not APPROVAL_SUBSCRIBERS[key]:
                    APPROVAL_SUBSCRIBERS.pop(key, None)

    return StreamingResponse(generate(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    })


@app.get("/api/pending")
async def get_all_pending(user=Depends(verify_admin)):
    """获取当前用户拥有的页面的待审批请求"""
    visitors_data = read_visitors()
    index = read_index()
    my_pages = _get_user_pages(user, index)
    my_slugs = {p["slug"] for p in my_pages}
    title_map = {p["slug"]: p["title"] for p in my_pages}
    provider_names = {"dingtalk": "钉钉", "wechat": "微信"}

    pending_list = []
    for v in visitors_data["visitors"]:
        for slug in v.get("pending_pages", []):
            if slug in my_slugs:
                pending_list.append({
                    "slug": slug,
                    "page_title": title_map.get(slug, slug),
                    "visitor_id": v["id"],
                    "visitor_name": v["name"],
                    "provider": provider_names.get(v.get("provider", ""), v.get("provider", "")),
                })

    return {"pending": pending_list, "count": len(pending_list)}


@app.get("/api/events/stream")
async def event_stream(request: Request, user=Depends(verify_admin)):
    last_event_id = request.headers.get("Last-Event-ID", "") or request.query_params.get("last_event_id", "")

    # 预取当前用户拥有的页面 slug 集合
    index = read_index()
    my_slugs = {p["slug"] for p in _get_user_pages(user, index)}
    is_super = _is_super_admin(user)

    def _event_belongs_to_user(event: dict) -> bool:
        if is_super:
            return True
        slug = event.get("page", {}).get("slug", "")
        return slug in my_slugs

    async def generate():
        queue = asyncio.Queue()
        EVENT_SUBSCRIBERS.add(queue)
        try:
            if last_event_id:
                replay = False
                for item in EVENT_HISTORY:
                    if replay and _event_belongs_to_user(item):
                        yield f"id: {item['id']}\nevent: {item['type']}\ndata: {json.dumps(item, ensure_ascii=False)}\n\n"
                    elif item["id"] == last_event_id:
                        replay = True
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=55)
                    if _event_belongs_to_user(event):
                        yield f"id: {event['id']}\nevent: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            EVENT_SUBSCRIBERS.discard(queue)

    return StreamingResponse(generate(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    })


# ---------------------------------------------------------------------------
# 路由：页面访问（鉴权入口）— 放在最后避免匹配其他路由
# ---------------------------------------------------------------------------


@app.get("/{slug}", response_class=HTMLResponse)
async def view_page(slug: str, request: Request):
    # 排除特殊路径
    if slug in ("favicon.ico", "robots.txt"):
        raise HTTPException(404)

    page = find_page(slug)
    if not page:
        raise HTTPException(404, "Page not found")

    page_file = PAGES_DIR / slug / "index.html"
    if not page_file.exists():
        raise HTTPException(404, "Page file not found")

    access = page.get("access", "public")

    # 公开页面直接返回
    if access == "public":
        return HTMLResponse(page_file.read_text(encoding="utf-8"))

    # 私密页面只有页面所有者能看
    if access == "private":
        token = _extract_token(request)
        if token:
            # 超级管理员可看所有私密页面
            if SUPER_ADMIN_TOKEN and token == SUPER_ADMIN_TOKEN:
                return HTMLResponse(page_file.read_text(encoding="utf-8"))
            # 普通用户只能看自己的私密页面
            user = find_user_by_token(token)
            if user and _user_owns_page(user, page):
                return HTMLResponse(page_file.read_text(encoding="utf-8"))
        raise HTTPException(403, "This page is private")

    # 审批制页面
    visitor = get_session_visitor(request)

    if not visitor:
        # 未登录 → 显示登录页
        has_dingtalk = bool(CONFIG.get("dingtalk", {}).get("app_key"))
        has_wechat = bool(CONFIG.get("wechat", {}).get("app_id"))
        return templates.TemplateResponse("login.html", {
            "request": request,
            "page": page,
            "slug": slug,
            "has_dingtalk": has_dingtalk,
            "has_wechat": has_wechat,
            "base_url": BASE_URL,
        })

    if visitor.get("blocked"):
        raise HTTPException(403, "You have been blocked")

    if slug in visitor.get("approved_pages", []):
        return HTMLResponse(page_file.read_text(encoding="utf-8"))

    # 未审批 → 自动提交申请并显示等待页
    if slug not in visitor.get("pending_pages", []):
        visitors_data = read_visitors()
        for v in visitors_data["visitors"]:
            if v["id"] == visitor["id"]:
                v.setdefault("pending_pages", []).append(slug)
                break
        write_visitors(visitors_data)
        # 推送 OpenClaw 通知
        await notify_openclaw(page, visitor)

    return templates.TemplateResponse("pending.html", {
        "request": request,
        "page": page,
        "slug": slug,
        "visitor": visitor,
        "base_url": BASE_URL,
    })


# 静态资源服务
@app.get("/{slug}/assets/{path:path}")
async def page_assets(slug: str, path: str):
    file_path = PAGES_DIR / slug / "assets" / path
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(404)
    # 安全检查：防止路径穿越
    try:
        file_path.resolve().relative_to((PAGES_DIR / slug / "assets").resolve())
    except ValueError:
        raise HTTPException(403)

    import mimetypes
    content_type, _ = mimetypes.guess_type(str(file_path))
    return Response(
        content=file_path.read_bytes(),
        media_type=content_type or "application/octet-stream",
    )


# ---------------------------------------------------------------------------
# 启动
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    host = SERVER_CONFIG.get("host", "0.0.0.0")
    port = SERVER_CONFIG.get("port", 8000)
    print(f"🚀 HTML Hub running at http://{host}:{port}")
    uvicorn.run(app, host=host, port=port)
